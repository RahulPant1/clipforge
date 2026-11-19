import { useState } from 'react'
import { api } from '../api.js'

const FLOW = ['Source', 'Transcript', 'Pick clips', 'Shorts', 'Posts', 'Publish']

export default function NewJob({ onCreated }) {
  const [kind, setKind] = useState('url')
  const [url, setUrl] = useState('')
  const [file, setFile] = useState(null)
  const [numClips, setNumClips] = useState(5)
  const [mode, setMode] = useState('review')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const ready = kind === 'url' ? url.trim().startsWith('http') : !!file

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      const job =
        kind === 'url' ? await api.createJob(url.trim(), numClips, mode) : await api.uploadJob(file, numClips, mode)
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

        <label className="row">
          Shorts you want
          <input type="number" min="1" max="20" value={numClips} onChange={(e) => setNumClips(Number(e.target.value))} />
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
