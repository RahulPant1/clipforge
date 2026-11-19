import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { clipText, fmtPrecise } from '../../util.js'

function Nudge({ label, value, onChange, disabled }) {
  return (
    <div className="nudge">
      <span className="muted">{label}</span>
      <button disabled={disabled} onClick={() => onChange(value - 1)}>−1s</button>
      <button disabled={disabled} onClick={() => onChange(value - 0.2)}>−.2</button>
      <b>{fmtPrecise(value)}</b>
      <button disabled={disabled} onClick={() => onChange(value + 0.2)}>+.2</button>
      <button disabled={disabled} onClick={() => onChange(value + 1)}>+1s</button>
    </div>
  )
}

function Candidate({ job, clip, transcript, busy, act }) {
  const [title, setTitle] = useState(clip.title)
  const [hook, setHook] = useState(clip.hook)
  const [kw, setKw] = useState(clip.keywords.join(', '))
  useEffect(() => {
    setTitle(clip.title)
    setHook(clip.hook)
    setKw(clip.keywords.join(', '))
  }, [clip.title, clip.hook, clip.keywords])

  const save = (changes) => act(() => api.editClip(job.id, clip.index, changes)).catch(() => {})
  const dur = clip.end - clip.start
  const src = `${api.fileUrl(job.id, 'source.mp4')}#t=${clip.start.toFixed(2)},${clip.end.toFixed(2)}`

  return (
    <div className={`cand-card ${clip.approved ? 'is-approved' : ''}`}>
      <video key={src} src={src} controls preload="metadata" />
      <div className="cand-body">
        <div className="cand-head">
          <span className="score" title="Gemma virality score">{clip.score}/10</span>
          {clip.custom && <span className="chip">your clip</span>}
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

        <div className="timing">
          <Nudge label="Start" value={clip.start} disabled={busy} onChange={(v) => save({ start: Math.max(0, v) })} />
          <Nudge label="End" value={clip.end} disabled={busy} onChange={(v) => save({ end: v })} />
          <span className={`dur ${dur > 59 ? 'warn' : ''}`}>{dur.toFixed(1)}s{dur > 59 && ' ⚠ over 59s'}</span>
        </div>
        <p className="excerpt">“{clipText(transcript, clip) || '…'}”</p>
      </div>
    </div>
  )
}

export default function PickStep({ job, busy, act, go, transcript }) {
  const [filter, setFilter] = useState('all')
  const clips = [...job.clips].sort((a, b) => (filter === 'score' ? b.score - a.score : a.start - b.start))
  const shown = filter === 'approved' ? clips.filter((c) => c.approved) : clips
  const approved = job.clips.filter((c) => c.approved)
  const pending = approved.filter((c) => !c.file || c.stale || !Object.keys(c.content || {}).length)

  return (
    <div>
      <div className="step-head">
        <div>
          <h2>Pick clips</h2>
          <p className="muted">
            {approved.length} approved of {job.clips.length} candidates (you asked for {job.num_clips}). Trim with
            the nudge buttons, fix titles, then create. Need a moment Gemma missed? Add it from the{' '}
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
          <Candidate key={c.index} job={job} clip={c} transcript={transcript} busy={busy} act={act} />
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
