import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api.js'
import { MAX_SHORT, clipText, fmtPrecise, moveEdge, timelineWindow } from '../../util.js'
import CaptionEditor from '../CaptionEditor.jsx'
import NarrationBatch from '../NarrationBatch.jsx'
import Timeline from '../Timeline.jsx'

const KEY_SAVE_MS = 600 // keyboard / button nudges are saved once you pause

// One edge: value + fine nudges (touch screens have no J/K); clicking it makes it the edge the keys move
function Edge({ label, value, on, onPick, onNudge, disabled }) {
  return (
    <div className={`nudge ${on ? 'on' : ''}`} onClick={onPick}>
      <span className="muted">{label}</span>
      <button disabled={disabled} onClick={() => onNudge(-0.2)} aria-label={`${label} 0.2 s earlier`}>−</button>
      <b>{fmtPrecise(value)}</b>
      <button disabled={disabled} onClick={() => onNudge(0.2)} aria-label={`${label} 0.2 s later`}>+</button>
    </div>
  )
}

// L: several title + hook pairs in different styles from Gemma; click one to use it
const STYLE = { question: 'Question', bold_claim: 'Bold claim', number: 'Number', curiosity: 'Curiosity',
  pov: 'POV', contrarian: 'Contrarian', mistake: 'Mistake', other: 'Other' }

const VERDICT = {
  ready: ['✓ Ready', 'Stands on its own: setup and payoff are inside the clip'],
  trim: ['✂ Trim', 'Publishable after moving an edge — the fix below does it'],
  needs_context: ['⚠ Needs context', "Missing setup or payoff that doesn't fit in a Short as cut"],
  reject: ['✕ Reject', 'Changes the meaning, or has no hook and no value'],
}
const CHECK = { opening: 'Opening', ending: 'Ending', list: 'List', faithful: 'Meaning', hook: 'Hook',
  value: 'Value', clear: 'Clarity', hint: 'Hint', fix: 'Fix' }

// Clip judge (judge.py): verdict + what exactly is missing + a one-click fix. Re-check after you trim.
function ReviewPanel({ job, clip, busy, act, save }) {
  const [asking, setAsking] = useState(false)
  const r = clip.review
  const outdated = r && (Math.abs(r.start - clip.start) > 0.05 || Math.abs(r.end - clip.end) > 0.05)
  const check = async () => {
    setAsking(true)
    try { await act(() => api.reviewClip(job.id, clip.index)) } catch { /* shown by act() */ } finally { setAsking(false) }
  }
  const issues = r ? r.checks.filter((c) => !c.ok) : []
  return (
    <div className={`review v-${r?.verdict || 'none'}`}>
      <div className="review-head">
        {r ? <b title={VERDICT[r.verdict][1]}>{VERDICT[r.verdict][0]}</b> : <span className="muted">Not checked yet</span>}
        {outdated && <span className="chip warnchip" title="You changed the clip since it was checked">outdated</span>}
        {r && !r.model && <span className="muted small" title="Gemma wasn't available: only the transcript checks ran">rules only</span>}
        <button className="btn sm ghost" disabled={busy || asking} onClick={check}>
          {asking ? 'Checking…' : r ? '↻ Check again' : 'Check this clip'}
        </button>
      </div>
      {issues.length > 0 && (
        <ul>
          {issues.map((c, i) => (
            <li key={i} className={c.key === 'hint' ? 'muted' : ''}>
              <span className="tag">{CHECK[c.key] || c.key}</span> {c.note}
            </li>
          ))}
        </ul>
      )}
      {r?.fix && !outdated && (
        <button className="btn sm" disabled={busy} title="Moves the clip edges; you can still adjust them below"
                onClick={() => save({ start: r.fix.start, end: r.fix.end })}>
          Apply fix: {fmtPrecise(r.fix.start)}–{fmtPrecise(r.fix.end)} ({(r.fix.end - r.fix.start).toFixed(0)} s) · {r.fix.reason}
        </button>
      )}
    </div>
  )
}

function HookVariants({ job, clip, busy, act, onUse }) {
  const [asking, setAsking] = useState(false)
  const ask = async () => {
    setAsking(true)
    try {
      await act(() => api.hookVariants(job.id, clip.index))
    } catch {
      /* shown by act() */
    } finally {
      setAsking(false)
    }
  }
  const variants = clip.variants || []
  return (
    <div className="variants">
      <button className="btn sm ghost" disabled={busy || asking} onClick={ask}>
        {asking ? '✨ Gemma is writing hooks…' : variants.length ? '✨ New hooks' : '✨ More hooks'}
      </button>
      {variants.length > 0 && (
        <ul>
          {variants.map((v) => {
            const on = v.hook === clip.hook && v.title === clip.title
            return (
              <li key={v.hook}>
                <button className={on ? 'on' : ''} disabled={busy || on} onClick={() => onUse(v)}
                        title="Use this title and hook">
                  <span className="tag">{STYLE[v.style] || v.style}</span> <b>{v.hook}</b>
                  <span className="muted"> · {v.title}</span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function Candidate({ job, clip, others, transcript, wave, total, busy, act, reloadTranscript }) {
  const [fixing, setFixing] = useState(false)
  const [title, setTitle] = useState(clip.title)
  const [hook, setHook] = useState(clip.hook)
  const [kw, setKw] = useState(clip.keywords.join(', '))
  useEffect(() => {
    setTitle(clip.title)
    setHook(clip.hook)
    setKw(clip.keywords.join(', '))
  }, [clip.title, clip.hook, clip.keywords])

  const save = (changes) => act(() => api.editClip(job.id, clip.index, changes)).catch(() => {})

  // Timing being edited (drag / keys) — shown at once, saved on release or after a pause
  const [draft, setDraft] = useState(null) // [start, end] | null
  const [edge, setEdge] = useState('end') // the edge J/K and the ± buttons move
  const [playhead, setPlayhead] = useState(null)
  const video = useRef(null)
  const timer = useRef(null)
  const [start, end] = draft || [clip.start, clip.end]
  useEffect(() => setDraft(null), [clip.start, clip.end]) // saved (or changed elsewhere): show the real clip
  useEffect(() => () => clearTimeout(timer.current), [])

  const commit = (s, e) => {
    clearTimeout(timer.current)
    const changes = {}
    if (Math.abs(s - clip.start) > 0.005) changes.start = s
    if (Math.abs(e - clip.end) > 0.005) changes.end = e
    if (!Object.keys(changes).length) return setDraft(null)
    setDraft([s, e])
    // success: the clip update (SSE) clears the draft — clearing it here would flash the old timing first
    act(() => api.editClip(job.id, clip.index, changes)).catch(() => setDraft(null))
  }
  const later = (s, e) => {
    setDraft([s, e])
    clearTimeout(timer.current)
    timer.current = setTimeout(() => commit(s, e), KEY_SAVE_MS)
  }
  const nudge = (which, by) => later(...moveEdge(which, (which === 'start' ? start : end) + by, start, end, total))

  const win = useMemo(() => timelineWindow(clip.start, clip.end, total), [clip.start, clip.end, total])
  const edges = useMemo(
    () => (transcript || []).flatMap((x) => [x.start, x.end]).filter((t) => t > win[0] && t < win[1]),
    [transcript, win],
  )
  const seek = (t) => {
    if (!video.current) return
    video.current.currentTime = t
    setPlayhead(t)
  }

  const onKey = (e) => {
    if (busy || e.ctrlKey || e.metaKey || ['INPUT', 'TEXTAREA', 'SELECT', 'VIDEO'].includes(e.target.tagName)) return
    const k = e.key.toLowerCase()
    const step = e.shiftKey ? 1 : 0.2
    const v = video.current
    const actions = {
      a: () => save({ approved: !clip.approved }),
      j: () => nudge(edge, -step),
      k: () => nudge(edge, step),
      '[': () => setEdge('start'),
      ']': () => setEdge('end'),
      i: () => playhead != null && later(...moveEdge('start', playhead, start, end, total)),
      o: () => playhead != null && later(...moveEdge('end', playhead, start, end, total)),
      ' ': () => v && (v.paused ? v.play() : v.pause()),
    }
    if (actions[k]) {
      e.preventDefault()
      actions[k]()
    }
  }

  const dur = end - start
  const src = `${api.fileUrl(job.id, 'source.mp4')}#t=${clip.start.toFixed(2)},${clip.end.toFixed(2)}`

  return (
    <div className={`cand-card ${clip.approved ? 'is-approved' : ''}`} tabIndex={0} onKeyDown={onKey}>
      <video key={src} ref={video} src={src} controls preload="metadata"
             onTimeUpdate={(e) => {
               const v = e.currentTarget
               // preview the cut: jump over struck words while playing (timeupdate is ~4/s, so roughly)
               const skip = !v.paused && (clip.removed || []).find(([a, b]) => v.currentTime >= a && v.currentTime < b)
               if (skip) v.currentTime = skip[1]
               setPlayhead(v.currentTime)
             }}
             onSeeked={(e) => setPlayhead(e.currentTarget.currentTime)} />
      <div className="cand-body">
        <div className="cand-head">
          <span className="score" title="Gemma virality score">{clip.score}/10</span>
          {clip.relevance != null && (
            <span className={`chip ${clip.relevance < 7 ? 'warnchip' : ''}`} title="How much of the clip is about your topic">
              on topic {clip.relevance}/10
            </span>
          )}
          {clip.custom && <span className="chip">your clip</span>}
          {clip.removed?.length > 0 && <span className="chip" title="Struck in the transcript">✂ words cut</span>}
          {clip.inserts?.length > 0 && (
            <span className="chip" title="Stat cards: a spoken number counts up on screen — edit them in the Shorts step">
              📊 {clip.inserts.filter((i) => i.approved).length || clip.inserts.length}{' '}
              {clip.inserts.some((i) => i.approved) ? 'shown' : 'suggested'}
            </span>
          )}
          {clip.stale && <span className="chip warnchip">changed — re-render needed</span>}
          {clip.file && !clip.stale && <span className="chip">rendered</span>}
          <button
            className={`approve ${clip.approved ? 'on' : ''}`}
            disabled={busy}
            onClick={() => save({ approved: !clip.approved })}
          >
            {clip.approved ? '✓ Approved' : 'Approve'}
          </button>
        </div>

        {!clip.visual && <ReviewPanel job={job} clip={clip} busy={busy} act={act} save={save} />}

        <label className="fl">
          Title
          <input value={title} disabled={busy} onChange={(e) => setTitle(e.target.value)}
                 onBlur={() => title !== clip.title && save({ title })} />
        </label>
        <label className="fl">
          <span>Hook <span className="muted">· on-screen idea for the first 3 s</span></span>
          <input value={hook} disabled={busy} onChange={(e) => setHook(e.target.value)}
                 onBlur={() => hook !== clip.hook && save({ hook })} />
        </label>
        <HookVariants job={job} clip={clip} busy={busy} act={act} onUse={(v) => {
          setTitle(v.title)
          setHook(v.hook)
          save({ title: v.title, hook: v.hook })
        }} />
        <label className="fl">
          <span>Keywords <span className="muted">· highlighted green in captions, comma separated</span></span>
          <input value={kw} disabled={busy} onChange={(e) => setKw(e.target.value)}
                 onBlur={() => {
                   const k = kw.split(',').map((x) => x.trim().toLowerCase()).filter(Boolean)
                   if (k.join(',') !== clip.keywords.join(',')) save({ keywords: k })
                 }} />
        </label>

        <Timeline
          start={start} end={end} win={win} total={total} wave={wave} edges={edges} others={others}
          playhead={playhead} active={edge} disabled={busy}
          onDrag={(s, e) => { clearTimeout(timer.current); setDraft([s, e]) }}
          onCommit={commit} onSeek={seek} onActive={setEdge}
        />
        <div className="timing">
          <Edge label="Start" value={start} on={edge === 'start'} onPick={() => setEdge('start')}
                onNudge={(by) => nudge('start', by)} disabled={busy} />
          <Edge label="End" value={end} on={edge === 'end'} onPick={() => setEdge('end')}
                onNudge={(by) => nudge('end', by)} disabled={busy} />
          <span className={`dur ${dur > MAX_SHORT ? 'warn' : ''}`}>
            {dur.toFixed(1)}s{dur > MAX_SHORT && ` ⚠ over ${MAX_SHORT}s`}
          </span>
        </div>
        <p className="keys hint">
          Keys: <kbd>A</kbd> approve · <kbd>[</kbd> <kbd>]</kbd> pick edge · <kbd>J</kbd> <kbd>K</kbd> nudge
          0.2 s (<kbd>⇧</kbd> 1 s) · <kbd>I</kbd> <kbd>O</kbd> start/end at playhead · <kbd>Space</kbd> play ·
          drag snaps to sentence edges (<kbd>Alt</kbd>: free)
        </p>
        {clip.visual ? (
          <p className="excerpt" title="No speech: what Gemma saw in the keyframes">🎞 {clip.visual}</p>
        ) : fixing ? (
          <CaptionEditor job={job} clip={clip} start={clip.start} end={clip.end} playhead={playhead} onSeek={seek} busy={busy}
                         act={act} onFixed={reloadTranscript} />
        ) : (
          <p className="excerpt">“{clipText(transcript, clip) || '…'}”</p>
        )}
        {!clip.visual && (
          <button className="btn sm ghost fixcap" onClick={() => setFixing(!fixing)}>
            {fixing ? 'Done editing words' : '✎ Fix / ✂ cut words'}
          </button>
        )}
      </div>
    </div>
  )
}

// The check before rendering: what the clip judge's verdicts mean for this job, and what to do about it.
// It only recommends — every change here is a button you press.
function Validation({ job, v, busy, act }) {
  if (!v) return null
  const byIndex = Object.fromEntries(job.clips.map((c) => [c.index, c]))
  const rank = (c) => c.score + (c.relevance || 0)
  const complete = [...v.ready, ...v.trim].map((i) => byIndex[i]).filter(Boolean).sort((a, b) => rank(b) - rank(a))
  const keep = new Set(complete.slice(0, v.asked).map((c) => c.index))
  const approvalOff = job.clips.some((c) => c.approved !== keep.has(c.index)) && complete.length > 0
  const fixable = v.trim.map((i) => byIndex[i]).filter((c) => c?.review?.fix)
  const approveComplete = () => act(async () => {
    for (const c of job.clips) {
      if (c.approved !== keep.has(c.index)) await api.editClip(job.id, c.index, { approved: keep.has(c.index) })
    }
  }).catch(() => {})
  const applyFixes = () => act(async () => {
    for (const c of fixable) await api.editClip(job.id, c.index, { start: c.review.fix.start, end: c.review.fix.end })
  }).catch(() => {})
  const need = v.need_seconds.length
    ? ` (need ${Math.round(v.need_seconds[0])}${v.need_seconds.length > 1 ? `–${Math.round(v.need_seconds.at(-1))}` : ''} s)` : ''
  return (
    <div className={`card validation k-${v.kind}`}>
      <div className="val-head">
        <b>{{ ok: '✓ Ready to create', fewer: '⚠ Fewer complete clips than asked', longer: '⚠ The ideas here need longer clips',
          none: '✕ No self-contained moment found' }[v.kind]}</b>
        <span className="muted small">checked by the clip judge before anything is rendered</span>
      </div>
      <p>{v.text}</p>
      <div className="val-counts small">
        <span className="chip goodchip">✓ {v.ready.length} ready</span>
        <span className="chip">✂ {v.trim.length} after a fix</span>
        <span className="chip warnchip">⚠ {v.needs_context.length} need context{need}</span>
        <span className="chip badchip">✕ {v.reject.length} reject</span>
        {v.outdated.length > 0 && (
          <span className="muted">· {v.outdated.length} changed since checked — use “Check again” on {v.outdated.length === 1 ? 'it' : 'them'}</span>
        )}
      </div>
      {(approvalOff || fixable.length > 0) && (
        <div className="row-btns">
          {approvalOff && (
            <button className="btn sm" disabled={busy} onClick={approveComplete}
                    title="Approve the best complete clips (up to the number you asked for), un-approve the rest">
              Approve only the complete ones ({keep.size})
            </button>
          )}
          {fixable.length > 0 && (
            <button className="btn sm ghost" disabled={busy} onClick={applyFixes}
                    title="Move each fixable clip's edges to the sentences the judge suggested">
              Apply all {fixable.length} fix{fixable.length === 1 ? '' : 'es'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export default function PickStep({ job, busy, act, go, transcript, reloadTranscript }) {
  const [filter, setFilter] = useState('all')
  const [audioChoice, setAudioChoice] = useState(null)
  const [wave, setWave] = useState(null)
  const [check, setCheck] = useState(null) // job-level validation from the clip judge
  useEffect(() => {
    api.validation(job.id).then((r) => setCheck(r.validation)).catch(() => setCheck(null))
  }, [job.id, job.updated_at])
  useEffect(() => {
    api.waveform(job.id).then(setWave).catch(() => setWave(null)) // no waveform: the timeline still works
  }, [job.id])
  const total =
    (wave?.levels?.length ? wave.levels.length / wave.rate : 0) ||
    Math.max(0, ...(transcript || []).map((s) => s.end), ...job.clips.map((c) => c.end))
  // "Best first" = the same order selection used: virality + how on-topic (when the job has a topic)
  const rank = (c) => c.score + (c.relevance || 0)
  const clips = [...job.clips].sort((a, b) => (filter === 'score' ? rank(b) - rank(a) : a.start - b.start))
  const shown = filter === 'approved' ? clips.filter((c) => c.approved) : clips
  const approved = job.clips.filter((c) => c.approved)
  const canDub = approved.some((clip) => !clip.visual)
  const dubbed = approved.filter((clip) => clip.options?.dub).length
  const audio = canDub ? audioChoice ?? (dubbed ? 'saved' : null) : 'original'
  const pending = approved.filter((clip) => !clip.file || clip.stale || !Object.keys(clip.content || {}).length
    || (audio === 'original' && clip.options?.dub))
  const produce = () => {
    const flagged = check?.approved_flagged || []
    if (flagged.length && !window.confirm(
      `The clip judge flagged ${flagged.length} approved clip${flagged.length === 1 ? '' : 's'}:\n\n`
      + flagged.map((clip) => `#${clip.index} ${clip.title} — ${clip.verdict.replace('_', ' ')}`).join('\n')
      + '\n\nCreate them anyway?')) return
    act(() => api.produce(job.id, flagged.length > 0, audio === 'original')).catch(() => {})
  }

  return (
    <div>
      <div className="step-head">
        <div>
          <h2>Pick clips</h2>
          <p className="muted">
            {approved.length} approved of {job.clips.length} candidates (you asked for {job.num_clips}
            {job.topic ? <>, about <b>“{job.topic}”</b></> : null}). Trim with
            the timeline (or the keys — click a card first), fix titles, then create. Need a moment Gemma missed? Add it from the{' '}
            <a onClick={() => go('transcript')}>Transcript</a>.
          </p>
        </div>
        <div className="seg">
          {[['all', 'Timeline'], ['score', 'Best first'], ['approved', 'Approved']].map(([k, l]) => (
            <button key={k} className={filter === k ? 'on' : ''} onClick={() => setFilter(k)}>{l}</button>
          ))}
        </div>
      </div>

      <Validation job={job} v={check} busy={busy} act={act} />

      <div className="cands">
        {shown.map((c) => (
          <Candidate key={c.index} job={job} clip={c} transcript={transcript} wave={wave} total={total}
                     others={job.clips.filter((o) => o.index !== c.index).map((o) => [o.start, o.end])}
                     busy={busy} act={act} reloadTranscript={reloadTranscript} />
        ))}
      </div>

      {approved.length > 0 && <section className="card audio-choice" aria-label="Audio and export">
        <h3>Audio and export</h3>
        {canDub && <fieldset disabled={busy}>
          <legend>Audio for approved shorts</legend>
          <div className="row-btns">
            {[['original', 'Keep original audio'], ['saved', 'Use a saved voice']].map(([key, label]) => (
              <label key={key}><input type="radio" name="shorts-audio" value={key} checked={audio === key}
                onChange={() => setAudioChoice(key)} /> {label}</label>
            ))}
          </div>
        </fieldset>}
        <NarrationBatch job={job} clips={approved} busy={busy} act={act} open={audio === 'saved'} />
        <div className="footer-bar">
          {!audio ? <span className="muted">Choose the audio you want to use.</span>
            : audio === 'saved' && !dubbed
              ? <span className="muted">Prepare and generate narration to unlock export.</span>
              : pending.length > 0 ? (
            <>
              <span className="muted">
                {audio === 'original' ? 'The approved shorts will use the source audio.'
                  : `${dubbed} of ${approved.length} approved shorts use saved-voice narration.`}
              </span>
              <button className="primary" disabled={busy} onClick={produce}>
                {busy ? 'Working…' : `Create shorts & posts (${pending.length}) →`}
              </button>
            </>
          ) : (
            <button className="primary" disabled={busy} onClick={() => go('shorts')}>Review shorts →</button>
          )}
        </div>
      </section>}
    </div>
  )
}
