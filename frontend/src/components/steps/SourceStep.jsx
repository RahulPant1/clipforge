import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { STAGES } from '../../util.js'

const PREP = [
  { key: 'downloading', label: 'Download video' },
  { key: 'transcribing', label: 'Transcribe with Whisper' },
  { key: 'selecting', label: 'Gemma finds clip candidates' },
]

export default function SourceStep({ job, go, act, busy }) {
  const [topic, setTopic] = useState(job.topic || '') // a topic job can be retried with a broader one
  useEffect(() => setTopic(job.topic || ''), [job.id, job.topic])
  const at = STAGES.indexOf(job.stage)
  const prepared = job.clips.length > 0
  const isUrl = job.source.startsWith('http')

  return (
    <div className="step-grid">
      <div className="card">
        <h3>Source</h3>
        <p className="src-line">
          {isUrl ? (
            <a href={job.source} target="_blank" rel="noreferrer">{job.source}</a>
          ) : (
            <span>📁 {job.source}</span>
          )}
        </p>
        <p className="muted">
          Mode: <b>{job.mode === 'review' ? 'Review each step' : 'Fully automatic'}</b> · wants {job.num_clips} shorts ·
          {job.topic && <> topic <b>“{job.topic}”</b> ·</>}
          started {new Date(job.created_at).toLocaleString()}
        </p>
        {prepared && <video className="srcvid" src={api.fileUrl(job.id, 'source.mp4')} controls preload="metadata" />}
      </div>

      <div className="card">
        <h3>Preparation</h3>
        <ul className="checklist">
          {PREP.map((p) => {
            const i = STAGES.indexOf(p.key)
            const state = prepared || at > i ? 'done'
              : at === i && job.status === 'running' ? 'now'
              : at === i && job.status === 'failed' ? 'fail' : 'todo'
            return (
              <li key={p.key} className={state}>
                <span className="dot">{{ done: '✓', now: '●', fail: '✕' }[state] || ''}</span>
                {p.label}
                {state === 'now' && <span className="muted"> — {Math.round(job.progress * 100)}%</span>}
              </li>
            )
          })}
        </ul>
        {job.status === 'failed' && (
          <>
            <p className="err">{job.error}</p>
            {job.topic && (
              <label className="fl">
                <span>Topic <span className="muted">· make it broader, or clear it for the best moments on any subject</span></span>
                <input value={topic} maxLength={120} disabled={busy} onChange={(e) => setTopic(e.target.value)} />
              </label>
            )}
            <button className="primary" disabled={busy}
                    onClick={() => act(() => api.retry(job.id, job.topic ? topic.trim() : null)).catch(() => {})}>
              ↻ Retry
            </button>
            <p className="hint">Picks up where it stopped: a finished download or transcript is reused.</p>
          </>
        )}
        {prepared && (
          <button className="primary" onClick={() => go('pick')}>
            Review {job.clips.length} clips →
          </button>
        )}
      </div>
    </div>
  )
}
