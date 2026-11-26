import { useEffect, useRef, useState } from 'react'
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

  const youtube = /^https?:\/\/(?:(?:www\.|m\.)?youtube\.com|youtu\.be)(?:[/:?#]|$)/i.test(job.source)
  const recovering = youtube && job.status === 'failed' && (job.download_failure || job.stage === 'downloading')
  const [downloader, setDownloader] = useState(null)
  const [working, setWorking] = useState('')
  const [now, setNow] = useState(Date.now())
  const fileInput = useRef(null)
  const refreshDownloader = () => api.downloader().then(setDownloader).catch(() => {})
  useEffect(() => {
    if (recovering) refreshDownloader()
  }, [recovering, job.id, job.updated_at])
  const retryAt = (downloader?.retry_after ?? 0) * 1000
  const cooling = retryAt > now
  useEffect(() => {
    if (!cooling) return
    const timer = setTimeout(() => setNow(Date.now()), Math.min(retryAt - now + 50, 1000))
    return () => clearTimeout(timer)
  }, [cooling, now, retryAt])

  const recover = async (fn, label) => {
    setWorking(label)
    try {
      await act(fn)
    } catch { /* shown by act() */ } finally {
      await refreshDownloader()
      setNow(Date.now())
      setWorking('')
    }
  }

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
          {job.whisper_model && <> Whisper <b>{job.whisper_model === 'large-v3-turbo' ? 'Turbo' : 'Small'}</b> ·</>}
          started {new Date(job.created_at).toLocaleString()}
        </p>
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
            {recovering && <section aria-label="Download recovery">
              <h4>Download recovery</h4>
              {downloader && <p className="muted small">yt-dlp {downloader.version} · solver {downloader.solver}</p>}
              {cooling && <p className="warn small">YouTube cooldown ends at {new Date(retryAt).toLocaleTimeString()}.
                You can use a local file now.</p>}
              <div className="row-btns">
                <button className="btn" disabled={busy || !!working} onClick={() => fileInput.current?.click()}>
                  Use a local file
                </button>
                <input ref={fileInput} hidden type="file" accept="video/*" onChange={(event) => {
                  const file = event.target.files?.[0]
                  event.target.value = ''
                  if (file) recover(() => api.recoverSource(job.id, file), 'Importing local video…')
                }} />
                {(job.download_failure == null || job.download_failure === 'downloader') &&
                  <button className="btn" disabled={busy || !!working || cooling}
                    onClick={() => recover(() => api.updateDownloaderAndRetry(job.id), 'Updating and verifying downloader…')}>
                    Update downloader &amp; retry
                  </button>}
                {downloader?.can_rollback && <button className="btn ghost" disabled={busy || !!working}
                  onClick={() => recover(api.rollbackDownloader, 'Restoring previous downloader…')}>
                  Restore previous downloader
                </button>}
              </div>
              {job.download_failure === 'access' && <a href="https://support.google.com/youtube/answer/56100"
                target="_blank" rel="noreferrer">Download your own upload from YouTube Studio</a>}
              {working && <p className="muted small" role="status">{working}</p>}
            </section>}
            <button className="primary" disabled={busy || !!working}
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
