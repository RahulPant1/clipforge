import { isBusy, overallProgress } from '../util.js'

function shortSource(src) {
  try {
    const u = new URL(src)
    return u.searchParams.get('v') ? `youtube · ${u.searchParams.get('v')}` : u.hostname.replace('www.', '') + u.pathname
  } catch {
    return src
  }
}

const LABEL = { review: 'needs review', queued: 'queued', running: 'running', done: 'done', failed: 'failed' }

export default function JobList({ jobs, selectedId, onSelect, onDelete }) {
  if (!jobs.length) return <div className="card muted">No jobs yet.</div>
  // new jobs waiting their turn (a batch), oldest first = the order the worker takes them
  const waiting = jobs.filter((j) => j.status === 'queued' && !j.clips.length)
    .sort((a, b) => a.created_at.localeCompare(b.created_at)).map((j) => j.id)

  return (
    <div className="card list">
      <h3>Jobs</h3>
      {jobs.map((j) => {
        const approved = j.clips.filter((c) => c.approved).length
        const place = waiting.indexOf(j.id) // ≥ 0: not started yet — can be removed
        const posted = j.clips.flatMap((c) => Object.values(c.content || {})).filter((p) => p.status === 'posted').length
        return (
          <div key={j.id} className={`job ${j.id === selectedId ? 'sel' : ''}`} onClick={() => onSelect(j.id)}>
            <div className="job-top">
              <span className={`pill ${j.status}`}>{LABEL[j.status]}</span>
              <span className="src" title={j.source}>{shortSource(j.source)}</span>
              <button
                className="x"
                title={place >= 0 ? 'Remove from the queue' : isBusy(j) ? 'Wait for this job to finish before deleting it'
                  : 'Delete job'}
                disabled={isBusy(j) && place < 0}
                onClick={(e) => {
                  e.stopPropagation()
                  if (confirm(place >= 0 ? 'Remove this video from the queue?' : 'Delete this job and its files?')) {
                    onDelete(j.id)
                  }
                }}
              >
                ×
              </button>
            </div>
            {j.topic && <div className="msg topic" title={`Only moments about: ${j.topic}`}>🎯 {j.topic}</div>}
            {isBusy(j) && (
              <>
                <div className="bar">
                  <div style={{ width: `${Math.round(overallProgress(j) * 100)}%` }} />
                </div>
                <div className="msg">{place >= 0 ? `#${place + 1} in the queue` : j.message || j.stage}</div>
              </>
            )}
            {!isBusy(j) && j.clips.length > 0 && (
              <div className="msg">
                {approved} approved · {j.clips.filter((c) => c.file).length} rendered
                {posted > 0 && ` · ${posted} posted`}
              </div>
            )}
            {j.status === 'failed' && <div className="msg err" title={j.error}>{j.error}</div>}
          </div>
        )
      })}
    </div>
  )
}
