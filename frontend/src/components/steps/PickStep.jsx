import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api.js'
import { MAX_SHORT, clipText, fmtPrecise, moveEdge, timelineWindow } from '../../util.js'
import CaptionEditor from '../CaptionEditor.jsx'
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

export default function PickStep({ job, busy, act, go, transcript, reloadTranscript }) {
  const [filter, setFilter] = useState('all')
  const [wave, setWave] = useState(null)
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
  const pending = approved.filter((c) => !c.file || c.stale || !Object.keys(c.content || {}).length)

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

      <div className="cands">
        {shown.map((c) => (
          <Candidate key={c.index} job={job} clip={c} transcript={transcript} wave={wave} total={total}
                     others={job.clips.filter((o) => o.index !== c.index).map((o) => [o.start, o.end])}
                     busy={busy} act={act} reloadTranscript={reloadTranscript} />
        ))}
      </div>

      <div className="footer-bar">
        {pending.length > 0 ? (
          <>
            <span>
              <b>{pending.length}</b> approved clip{pending.length > 1 && 's'} need{pending.length === 1 && 's'} posts
              and/or rendering
            </span>
            <button className="primary" disabled={busy} onClick={() => act(() => api.produce(job.id)).catch(() => {})}>
              {busy ? 'Working…' : `Create shorts & posts (${pending.length}) →`}
            </button>
          </>
        ) : (
          <>
            <span className="muted">All approved clips are rendered.</span>
            <button className="primary" disabled={!approved.length} onClick={() => go('shorts')}>
              Review shorts →
            </button>
          </>
        )}
      </div>
    </div>
  )
}
