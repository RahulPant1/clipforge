import { useEffect, useState } from 'react'
import { api } from '../api.js'

const FLOW = ['Source', 'Transcript', 'Pick clips', 'Shorts', 'Posts', 'Publish']

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

export default function NewJob({ onCreated, onOpen }) {
  const [kind, setKind] = useState('url')
  const [url, setUrl] = useState('')
  const [file, setFile] = useState(null)
  const [numClips, setNumClips] = useState(5)
  const [mode, setMode] = useState('review')
  const [topic, setTopic] = useState('') // optional: only moments about this
  const [hinglish, setHinglish] = useState(true) // Hindi speech → captions in Latin letters
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [vocab, setVocab] = useState('') // names & terms for Whisper, remembered for all jobs
  const [vocabSaved, setVocabSaved] = useState('')
  const [seen, setSeen] = useState([]) // earlier jobs for the pasted link

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

  const ready = kind === 'url' ? url.trim().startsWith('http') : !!file

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      if (vocab !== vocabSaved) await api.saveVocabulary(vocab.split(',').map((t) => t.trim()).filter(Boolean))
      const t = topic.trim()
      const job =
        kind === 'url'
          ? await api.createJob(url.trim(), numClips, mode, t, hinglish)
          : await api.uploadJob(file, numClips, mode, t, hinglish)
      onCreated(job)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="newjob-wrap">
      <h2>New job</h2>
      <ol className="flow">
        {FLOW.map((f, i) => (
          <li key={f}>
            <span>{i + 1}</span>
            {f}
          </li>
        ))}
      </ol>

      <form className="card form big" onSubmit={submit}>
        <div className="tabs">
          <button type="button" className={kind === 'url' ? 'on' : ''} onClick={() => setKind('url')}>
            YouTube link
          </button>
          <button type="button" className={kind === 'file' ? 'on' : ''} onClick={() => setKind('file')}>
            Upload file
          </button>
        </div>

        {kind === 'url' ? (
          <input
            type="url"
            autoFocus
            placeholder="https://www.youtube.com/watch?v=…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        ) : (
          <input type="file" accept="video/*" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        )}
        {kind === 'url' && <SeenBefore matches={seen} onOpen={onOpen} />}

        <label className="row">
          Shorts you want
          <input type="number" min="1" max="20" value={numClips} onChange={(e) => setNumClips(Number(e.target.value))} />
        </label>

        <label className="fl">
          <span>
            Topic <span className="muted">· optional — only moments about this. Leave empty for the best moments on any
            subject</span>
          </span>
          <input value={topic} maxLength={120} placeholder="e.g. salary negotiation, the pricing story, funny moments"
                 onChange={(e) => setTopic(e.target.value)} />
        </label>

        <label className="checkline">
          <input type="checkbox" checked={hinglish} onChange={(e) => setHinglish(e.target.checked)} />
          <span>
            Hindi speech → <b>Hinglish</b> captions <span className="muted">· Latin letters ("kya baat hai") instead of
            Devanagari — easier to read and to fix. Switchable later on the Transcript step</span>
          </span>
        </label>

        <label className="fl">
          <span>
            Names & terms <span className="muted">· spelled right in captions (your name, brands, jargon), comma
            separated, remembered for all jobs</span>
          </span>
          <input value={vocab} placeholder="e.g. Rahul Pant, ClipForge, Power BI" onChange={(e) => setVocab(e.target.value)} />
        </label>

        <div className="modes">
          <label className={`mode ${mode === 'review' ? 'on' : ''}`}>
            <input type="radio" name="mode" checked={mode === 'review'} onChange={() => setMode('review')} />
            <div>
              <b>Review each step</b> <span className="tag">recommended</span>
              <p>Pauses after Gemma finds clips. You get {numClips * 2} candidates to edit, trim and approve before anything is rendered.</p>
            </div>
          </label>
          <label className={`mode ${mode === 'auto' ? 'on' : ''}`}>
            <input type="radio" name="mode" checked={mode === 'auto'} onChange={() => setMode('auto')} />
            <div>
              <b>Fully automatic</b>
              <p>Runs everything end to end. You can still edit and re-render afterwards.</p>
            </div>
          </label>
        </div>

        <button className="primary" disabled={!ready || busy}>
          {busy ? 'Starting…' : 'Start →'}
        </button>
        {err && <p className="err">{err}</p>}
        <p className="hint">Use your own or Creative Commons videos.</p>
      </form>
    </div>
  )
}
