import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

/**
 * Fix what Whisper misheard in one clip: click a word to edit it (Enter saves, Tab saves + next, Esc cancels),
 * or replace a word / phrase everywhere in the transcript. Rendered shorts covering a fix become "re-render needed".
 */
export default function CaptionEditor({ job, start, end, playhead, onSeek, busy, act, onFixed }) {
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
      <p className="hint">Click a word to fix it — Enter saves, Tab saves and moves on, Esc cancels.</p>
      <div className="capwords">
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
              className={`capword ${playhead != null && playhead >= w.start && playhead < w.end ? 'now' : ''}`}
              onClick={() => {
                if (busy) return
                onSeek(w.start)
                startEdit(k)
              }}
            >
              {w.word}
            </span>
          ),
        )}
      </div>
      <div className="capreplace">
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
      </div>
      {note && <p className="hint">{note}</p>}
    </div>
  )
}
