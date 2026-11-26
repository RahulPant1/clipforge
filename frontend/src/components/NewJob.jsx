import { useEffect, useState } from 'react'
import { api } from '../api.js'
import BatchList from './BatchList.jsx'
import FindVideos from './FindVideos.jsx'
import VideoCapture from './VideoCapture.jsx'

const MAX_BATCH = 50 // the API's limit per batch

const STATUS = { done: 'done', review: 'in review', failed: 'failed', running: 'running', queued: 'queued',
  deleted: 'deleted' }

// "You've done this video before": earlier jobs for the same video (any link form), from jobs + history
function SeenBefore({ matches, onOpen }) {
  if (!matches.length) return null
  return (
    <div className="seen">
      <b>⚠ You've worked on this video before{matches[0].title ? <> — “{matches[0].title}”</> : ''}</b>
      <ul>
        {matches.map((m) => (
          <li key={m.id}>
            <span>
              {new Date(m.processed).toLocaleDateString()} · {STATUS[m.status] || m.status}
              {m.topic && <> · topic “{m.topic}”</>} · <b>{m.shorts.length} short{m.shorts.length === 1 ? '' : 's'}</b>
              {m.posted > 0 && <> · {m.posted} posted</>}
              {m.shorts.length > 0 && <span className="muted"> — {m.shorts.slice(0, 3).join(' · ')}
                {m.shorts.length > 3 && ` +${m.shorts.length - 3}`}</span>}
              {!m.exists && m.folder_kept && <span className="muted"> · files kept in the History folder</span>}
            </span>
            {m.exists && <button type="button" className="btn" onClick={() => onOpen(m.id)}>Open</button>}
          </li>
        ))}
      </ul>
      <span className="muted small">You can still start a new job for it.</span>
    </div>
  )
}

export default function NewJob({ onCreated, onOpen, purpose = 'create' }) {
  const [kind, setKind] = useState('url')
  const [url, setUrl] = useState('')
  const [file, setFile] = useState(null)
  const [capture, setCapture] = useState(null)
  const [numClips, setNumClips] = useState(5)
  const [mode, setMode] = useState('review')
  const [topic, setTopic] = useState('') // optional: only moments about this
  const [hinglish, setHinglish] = useState(true) // Hindi speech → captions in Latin letters
  const [whisperModel, setWhisperModel] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [vocab, setVocab] = useState('') // names & terms for Whisper, remembered for all jobs
  const [vocabSaved, setVocabSaved] = useState('')
  const [seen, setSeen] = useState([]) // earlier jobs for the pasted link
  const [picked, setPicked] = useState(null) // { url, title, channel } chosen in Find videos
  const [items, setItems] = useState([]) // batch: videos to queue, each { url, title, …, on }

  useEffect(() => {
    const u = url.trim()
    if (kind !== 'url' || !u.startsWith('http')) return setSeen([])
    const t = setTimeout(() => api.lookup(u).then((r) => setSeen(r.matches)).catch(() => setSeen([])), 350)
    return () => clearTimeout(t)
  }, [url, kind])

  useEffect(() => {
    api.vocabulary().then((v) => {
      const text = v.terms.join(', ')
      setVocab(text)
      setVocabSaved(text)
    }).catch(() => {})
  }, [])

  const queue = items.filter((v) => v.on)
  const ready = kind === 'url' ? url.trim().startsWith('http') : kind === 'batch' ? queue.length > 0 && queue.length <= MAX_BATCH : kind === 'record' ? !!capture : !!file

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      if (vocab !== vocabSaved) await api.saveVocabulary(vocab.split(',').map((t) => t.trim()).filter(Boolean))
      const t = topic.trim()
      if (kind === 'batch') { // one job per video, run one after another by the worker
        const info = Object.fromEntries(queue.filter((v) => v.title)
          .map((v) => [v.url, { title: v.title.slice(0, 300), channel: (v.channel || '').slice(0, 200) }]))
        const { jobs } = await api.createBatch(queue.map((v) => v.url), numClips, mode, t, hinglish, info, whisperModel || null)
        setItems([])
        onCreated(jobs[0])
        return
      }
      const job =
        kind === 'url'
          ? await api.createJob(url.trim(), numClips, mode, t, hinglish, {
            purpose, whisper_model: whisperModel || null,
            ...(picked?.url === url.trim() ? { title: picked.title.slice(0, 300), channel: picked.channel.slice(0, 200) } : {}),
          })
          : await api.uploadJob(kind === 'record' ? capture : file, numClips, mode, t, hinglish, purpose,
            whisperModel || null, kind === 'record')
      onCreated(job)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="newjob-wrap">
      <h2>{purpose === 'learn' ? 'Learn from a video' : 'Make shorts from a video'}</h2>
      <form className="card form big source-form" onSubmit={submit}>
        <div className="tabs" role="group" aria-label="Video source">
          {[['url', 'YouTube link'], ['file', 'Upload file'], ['record', 'Record video']].map(([key, label]) => (
            <button type="button" key={key} className={kind === key ? 'on' : ''} aria-pressed={kind === key}
              disabled={busy} onClick={() => setKind(key)}>{label}</button>
          ))}
        </div>
        <details className="source-options">
          <summary>More sources{kind === 'find' ? ' · Find videos' : kind === 'batch' ? ' · Batch' : ''}</summary>
          <div className="row-btns">
            <button type="button" className="btn ghost" aria-pressed={kind === 'find'}
              disabled={busy} onClick={() => setKind('find')}>Find videos</button>
            {purpose === 'create' && <button type="button" className="btn ghost" aria-pressed={kind === 'batch'}
              disabled={busy} onClick={() => setKind('batch')}>Batch{items.length > 0 && ` (${queue.length})`}</button>}
          </div>
        </details>

        {kind === 'find' ? (
          <FindVideos initialTopic={topic}
                      onUse={(r, t) => {
                        setUrl(r.url); setTopic(t); setKind('url')
                        setPicked({ url: r.url, title: r.title || '', channel: r.channel || '' })
                      }}
                      onQueue={purpose === 'create' ? (picks, t) => {
                        const have = new Set(items.map((v) => v.url))
                        setItems([...items, ...picks.filter((v) => !have.has(v.url)).map((v) => ({ ...v, on: true }))])
                        setTopic(t)
                        setKind('batch')
                      } : undefined} />
        ) : (<>

        {kind === 'batch' ? (
          <BatchList items={items} setItems={setItems} />
        ) : kind === 'url' ? (
          <input
            type="url"
            aria-label="YouTube link"
            autoFocus
            placeholder="https://www.youtube.com/watch?v=…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        ) : kind === 'record' ? (
          <>
            {capture ? <div className="row-btns">
              <span className="small">Recording ready</span>
              <button type="button" className="btn ghost" disabled={busy} onClick={() => setCapture(null)}>Record again</button>
            </div> : <VideoCapture disabled={busy} onUse={setCapture} />}
            <p className="hint">Up to 10 minutes per recording. Screen capture includes your microphone.</p>
          </>
        ) : (
          <input type="file" aria-label="Video file" accept="video/*" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        )}
        {kind === 'url' && <SeenBefore matches={seen} onOpen={onOpen} />}

        {purpose === 'create' && <label className="row">
          Shorts you want
          <input type="number" min="1" max="20" value={numClips} onChange={(event) => setNumClips(Number(event.target.value))} />
        </label>}
        {purpose === 'learn' && <p className="muted small">Create timestamped study notes, chapters, and a quiz.</p>}

        <button className="primary" disabled={!ready || busy}>
          {busy ? 'Starting…' : kind === 'batch'
            ? `Queue ${queue.length} video${queue.length === 1 ? '' : 's'}`
            : purpose === 'learn' ? 'Create study notes' : mode === 'auto' ? 'Create shorts' : 'Find shorts'}
        </button>

        <details className="creation-advanced">
          <summary>Advanced settings</summary>
          <div className="form">
            <label className="fl">
              <span>Transcription model</span>
              <select value={whisperModel} onChange={(event) => setWhisperModel(event.target.value)}>
                <option value="">App default</option>
                <option value="small">Whisper Small</option>
                <option value="large-v3-turbo">Whisper Turbo · Hindi, Hinglish, or difficult speech</option>
              </select>
            </label>
            {purpose === 'create' && <label className="fl">
              <span>Topic <span className="muted">· optional</span></span>
              <input value={topic} maxLength={120} placeholder="e.g. salary negotiation"
                onChange={(event) => setTopic(event.target.value)} />
            </label>}
            <label className="checkline">
              <input type="checkbox" checked={hinglish} onChange={(event) => setHinglish(event.target.checked)} />
              <span>Write Hindi as <b>Hinglish</b> in Latin letters</span>
            </label>
            <label className="fl">
              <span>Names &amp; terms <span className="muted">· applies to all jobs</span></span>
              <input value={vocab} placeholder="e.g. ClipForge, Power BI" onChange={(event) => setVocab(event.target.value)} />
            </label>
            {purpose === 'create' && <div className="modes">
              <label className={`mode ${mode === 'review' ? 'on' : ''}`}>
                <input type="radio" name="mode" checked={mode === 'review'} onChange={() => setMode('review')} />
                <div><b>Review before rendering</b><p>Approve the suggested clips before ClipForge renders them.</p></div>
              </label>
              <label className={`mode ${mode === 'auto' ? 'on' : ''}`}>
                <input type="radio" name="mode" checked={mode === 'auto'} onChange={() => setMode('auto')} />
                <div><b>Render automatically</b><p>Render suggested clips. You can edit them afterward.</p></div>
              </label>
            </div>}
          </div>
        </details>
        {err && <p className="err">{err}</p>}
        {kind === 'batch' && queue.length > MAX_BATCH && (
          <p className="err">At most {MAX_BATCH} videos per batch — untick {queue.length - MAX_BATCH}.</p>
        )}
        {kind === 'batch' && (
          <p className="hint">They run one at a time, in this order — leave it overnight. In review mode each stops at
            Pick clips for you; a queued job can be removed from the Jobs list until it starts.</p>
        )}
        <p className="hint">Use your own or Creative Commons videos.</p>
        </>)}
      </form>
    </div>
  )
}
