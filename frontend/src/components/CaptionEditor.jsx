import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api.js'

// A word is cut when its middle lies in a struck range — same rule as jumpcut.is_struck on the server
const struckBy = (w, ranges) => {
  const mid = (w.start + w.end) / 2
  return ranges.some(([a, b]) => a <= mid && mid < b)
}

/**
 * Edit one clip's words.
 * Fix: click a word to edit it (Enter saves, Tab saves + next, Esc cancels), or replace a word / phrase everywhere
 * in the transcript. Rendered shorts covering a fix become "re-render needed".
 * Cut (text-based editing): click words to strike them out of this short, ⇧-click strikes a run. Saved as
 * clip.removed (source-time ranges); the render cuts them with the pause around them, captions skip them.
 */
export default function CaptionEditor({ job, clip, start, end, playhead, onSeek, busy, act, onFixed }) {
  const [mode, setMode] = useState('fix') // fix | cut
  const [struck, setStruck] = useState(null) // Set of word indexes (optimistic while saving) | null = from clip
  const anchor = useRef(null) // last word clicked in cut mode, for ⇧-click runs
  const [words, setWords] = useState(null)
  const [edit, setEdit] = useState(null) // { k: index in words, text }
  const [find, setFind] = useState('')
  const [repl, setRepl] = useState('')
  const [count, setCount] = useState(null)
  const [remember, setRemember] = useState(true)
  const [note, setNote] = useState('')
  const active = useRef(null) // index being edited — Enter then blur must not save twice

  const load = useCallback(
    () => api.words(job.id, start, end).then(setWords).catch(() => setWords([])),
    [job.id, start, end],
  )
  useEffect(() => {
    load()
  }, [load])

  // How many words "replace all" would change (debounced dry run)
  useEffect(() => {
    setCount(null)
    if (!find.trim() || !repl.trim()) return
    const t = setTimeout(() => {
      api.replaceWords(job.id, { find, replace: repl, dry_run: true }).then((r) => setCount(r.matches)).catch(() => {})
    }, 300)
    return () => clearTimeout(t)
  }, [job.id, find, repl, words])

  const removed = clip.removed || []
  useEffect(() => setStruck(null), [clip.removed])
  const cut = useMemo(
    () => struck || new Set((words || []).flatMap((w, k) => (struckBy(w, removed) ? [k] : []))),
    [struck, words, removed],
  )

  // Struck words → ranges: one per run of neighbouring words; ranges over words outside this view are kept
  const saveCut = (next) => {
    setStruck(next)
    const ks = [...next].sort((a, b) => a - b)
    const runs = []
    for (const k of ks) {
      const last = runs[runs.length - 1]
      if (last && last[1] === k - 1) last[1] = k
      else runs.push([k, k])
    }
    const outside = removed.filter(([a, b]) => !words.some((w) => struckBy(w, [[a, b]])))
    const ranges = [...outside, ...runs.map(([a, b]) => [words[a].start, words[b].end])]
    act(() => api.editClip(job.id, clip.index, { removed: ranges })).catch(() => setStruck(null))
  }
  const toggleCut = (k, shift) => {
    const next = new Set(cut)
    const on = !cut.has(k)
    const [a, b] = shift && anchor.current != null ? [Math.min(anchor.current, k), Math.max(anchor.current, k)] : [k, k]
    for (let i = a; i <= b; i++) on ? next.add(i) : next.delete(i)
    anchor.current = k
    saveCut(next)
  }
  const cutSeconds = (words || []).reduce((t, w, k) => t + (cut.has(k) ? w.end - w.start : 0), 0)

  const after = async () => {
    await load()
    onFixed()
  }

  const startEdit = (k) => {
    active.current = k
    setEdit({ k, text: words[k].word })
  }

  const save = async (k, text, next) => {
    if (active.current !== k) return
    active.current = null
    const w = words[k]
    setEdit(null)
    if (text.trim() && text.trim() !== w.word) {
      try {
        await act(() => api.editWord(job.id, { seg: w.seg, i: w.i, word: text }))
        const bare = (t) => t.trim().replace(/[^\p{L}\p{N}']+$/u, '') // punctuation stays with each word
        setFind(bare(w.word))
        setRepl(bare(text))
        setNote(`Fixed “${w.word}” → “${text.trim()}”. Same mistake elsewhere? Replace all below.`)
        await after()
      } catch {
        return
      }
    }
    if (next && k + 1 < words.length) startEdit(k + 1)
  }

  const replaceAll = async () => {
    try {
      const r = await act(() => api.replaceWords(job.id, { find, replace: repl, remember }))
      setNote(`Replaced ${r.matches} × “${find}” → “${repl}”${remember ? ' — remembered for future transcripts' : ''}.`)
      setFind('')
      setRepl('')
      await after()
    } catch {
      /* shown by act() */
    }
  }

  const onKey = (k) => (e) => {
    if (e.key === 'Enter') save(k, e.currentTarget.value)
    else if (e.key === 'Tab') {
      e.preventDefault()
      save(k, e.currentTarget.value, true)
    } else if (e.key === 'Escape') {
      active.current = null
      setEdit(null)
    }
    e.stopPropagation() // don't trigger the card's A/J/K shortcuts while typing
  }

  if (!words) return <div className="capedit muted">Loading words…</div>
  if (!words.length) return <div className="capedit muted">No words in this clip (no speech).</div>

  return (
    <div className="capedit">
      <div className="capmodes">
        <div className="seg">
          <button className={mode === 'fix' ? 'on' : ''} onClick={() => setMode('fix')}>✎ Fix spelling</button>
          <button className={mode === 'cut' ? 'on' : ''} onClick={() => setMode('cut')}>✂ Cut words</button>
        </div>
        {cut.size > 0 && (
          <span className="muted small">
            ✂ {cut.size} word{cut.size > 1 ? 's' : ''} cut · −{cutSeconds.toFixed(1)} s + the pauses around them
            <button className="btn sm ghost" disabled={busy} onClick={() => saveCut(new Set())}>Restore all</button>
          </span>
        )}
      </div>
      <p className="hint">
        {mode === 'fix'
          ? 'Click a word to fix it — Enter saves, Tab saves and moves on, Esc cancels.'
          : 'Click words to cut them from this short (again to restore), ⇧-click to cut a run. Captions and posts skip them; the preview jumps over them.'}
      </p>
      <div className={`capwords ${mode === 'cut' ? 'cutting' : ''}`}>
        {words.map((w, k) =>
          edit?.k === k ? (
            <input
              key={k}
              autoFocus
              className="capword-in"
              value={edit.text}
              size={Math.max(edit.text.length, 3)}
              disabled={busy}
              onChange={(e) => setEdit({ k, text: e.target.value })}
              onKeyDown={onKey(k)}
              onBlur={(e) => save(k, e.target.value)}
            />
          ) : (
            <span
              key={k}
              className={`capword ${playhead != null && playhead >= w.start && playhead < w.end ? 'now' : ''} ${
                cut.has(k) ? 'struck' : ''}`}
              title={mode === 'cut' ? (cut.has(k) ? 'Click to restore' : 'Click to cut') : undefined}
              onClick={(e) => {
                if (busy) return
                if (mode === 'cut') return toggleCut(k, e.shiftKey)
                onSeek(w.start)
                startEdit(k)
              }}
            >
              {w.word}
            </span>
          ),
        )}
      </div>
      {mode === 'fix' && <div className="capreplace">
        <input value={find} placeholder="Find (word or phrase)" disabled={busy} onChange={(e) => setFind(e.target.value)}
               onKeyDown={(e) => e.stopPropagation()} />
        <span className="muted">→</span>
        <input value={repl} placeholder="Replace with" disabled={busy} onChange={(e) => setRepl(e.target.value)}
               onKeyDown={(e) => e.stopPropagation()} />
        <button className="btn sm" disabled={busy || !count} onClick={replaceAll}>
          Replace all{count != null ? ` (${count})` : ''}
        </button>
        <label className="muted small remember">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          remember for future transcripts
        </label>
      </div>}
      {note && <p className="hint">{note}</p>}
    </div>
  )
}
