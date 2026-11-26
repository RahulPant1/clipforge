import { useCallback, useEffect, useState } from 'react'
import { api } from './api.js'
import History from './components/History.jsx'
import BrandKit from './components/BrandKit.jsx'
import BrandVoice from './components/BrandVoice.jsx'
import VoiceLab from './components/VoiceLab.jsx'
import RevoicePanel from './components/RevoicePanel.jsx'
import ScriptShort from './components/ScriptShort.jsx'
import HealthBadge from './components/HealthBadge.jsx'
import JobList from './components/JobList.jsx'
import NewJob from './components/NewJob.jsx'
import Workspace from './components/Workspace.jsx'
import { isBusy } from './util.js'

const VOICE = 'brand-voice'
const KIT = 'brand-kit'
const SPEECH = 'your-voice'
const SCRIPT_SHORT = 'script-short'

const VIDEO_SHORTS = 'video-shorts'
const REVOICE = 'revoice'
const LEARN_VIDEO = 'learn-video'
const creationPages = [null, VIDEO_SHORTS, REVOICE, SCRIPT_SHORT, LEARN_VIDEO]

function CreateHome({ onChoose }) {
  return <div className="newjob-wrap">
    <h2>Create</h2>
    <div className="create-paths">
      <button className="card create-path" onClick={() => onChoose(VIDEO_SHORTS)}>
        <strong>Make shorts from a video</strong>
        <span>Paste a YouTube link, upload a video, or record your screen or webcam.</span>
      </button>
      <button className="card create-path" onClick={() => onChoose(REVOICE)}>
        <strong>Re-voice a recording</strong>
        <span>Replace one sentence or narrate the entire recording in a saved voice.</span>
      </button>
      <button className="card create-path" onClick={() => onChoose(SCRIPT_SHORT)}>
        <strong>Make a short from a script</strong>
        <span>Turn a script, a saved voice, and local B-roll into a captioned short.</span>
      </button>
    </div>
    <button className="btn ghost create-learn" onClick={() => onChoose(LEARN_VIDEO)}>Learn from a video</button>
  </div>
}

export default function App() {
  const [jobs, setJobs] = useState([])
  const [menuOpen, setMenuOpen] = useState(false)
  const [selectedId, setSelectedId] = useState(null) // null = "new job" screen, VOICE = brand voice
  const [error, setError] = useState('')

  const [live, setLive] = useState(false) // event stream connected: it keeps `jobs` current

  const refresh = useCallback(async () => {
    try {
      setJobs(await api.listJobs())
      setError('')
    } catch (e) {
      setError(`Backend not reachable: ${e.message}`)
    }
  }, [])

  // Live updates over SSE: a snapshot, then every change (progress, edits, renders) as it happens.
  // EventSource reconnects by itself after an API restart; the snapshot it gets then resyncs everything.
  useEffect(() => {
    const es = new EventSource(api.eventsUrl())
    const upsert = (job) =>
      setJobs((jobs) => {
        const rest = jobs.filter((j) => j.id !== job.id)
        return [job, ...rest].sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
      })
    es.addEventListener('snapshot', (e) => {
      setJobs(JSON.parse(e.data))
      setLive(true)
      setError('')
    })
    es.addEventListener('job', (e) => upsert(JSON.parse(e.data)))
    es.addEventListener('deleted', (e) => {
      const { id } = JSON.parse(e.data)
      setJobs((jobs) => jobs.filter((j) => j.id !== id))
    })
    es.onerror = () => setLive(false)
    return () => es.close()
  }, [])

  // Without the stream: load once, and poll while anything is queued or running
  useEffect(() => {
    if (!live) refresh()
  }, [live, refresh])
  const active = jobs.some(isBusy)
  useEffect(() => {
    if (live || !active) return
    const t = setInterval(refresh, 1500)
    return () => clearInterval(t)
  }, [live, active, refresh])

  // After an action: the stream already delivers the change (in order) — a fetch could only race it
  const changed = useCallback(() => (live ? Promise.resolve() : refresh()), [live, refresh])

  const selected = jobs.find((j) => j.id === selectedId) || null

  const navigate = (id) => {
    setSelectedId(id)
    setMenuOpen(false)
  }

  const onDelete = async (id) => {
    try {
      await api.deleteJob(id)
      if (id === selectedId) setSelectedId(null)
    } catch (e) {
      alert(`Could not delete job: ${e.message}`)
    }
    changed()
  }

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">✂</span>
          <div>
            <h1>ClipForge</h1>
            <p>Long video → Shorts + posts. 100% local.</p>
          </div>
        </div>
        <div className="header-actions">
          <HealthBadge />
          <button type="button" className="btn mobile-menu" aria-expanded={menuOpen} aria-controls="workspace-navigation"
            onClick={() => setMenuOpen((open) => !open)}>{menuOpen ? 'Close menu' : 'Menu'}</button>
        </div>
      </header>

      {error && <div className="banner error">{error}</div>}

      <main>
        <aside id="workspace-navigation" className={menuOpen ? 'menu-open' : ''}
          onKeyDown={(event) => { if (event.key === 'Escape') setMenuOpen(false) }}>
          <button className={`newjob ${creationPages.includes(selectedId) ? 'on' : ''}`} onClick={() => navigate(null)}>
            + Create
          </button>
          <button className={`voicebtn ${selectedId === VOICE ? 'on' : ''}`} onClick={() => navigate(VOICE)}>
            🎙 Brand voice
          </button>
          <button className={`voicebtn ${selectedId === KIT ? 'on' : ''}`} onClick={() => navigate(KIT)}>
            🎨 Brand kit
          </button>
          <button className={`voicebtn ${selectedId === SPEECH ? 'on' : ''}`} onClick={() => navigate(SPEECH)}>
            🗣 Voice Library
          </button>
          <History revision={jobs.filter((job) => !isBusy(job)).map((job) => job.id).join(',')} />
          <JobList jobs={jobs} selectedId={selectedId} onSelect={navigate} onDelete={onDelete} />
        </aside>
        <section className="detail">
          {selectedId !== null && creationPages.includes(selectedId) &&
            <button className="btn sm ghost create-back" onClick={() => navigate(null)}>← Back to Create</button>}
          {selectedId === VOICE ? (
            <BrandVoice />
          ) : selectedId === SPEECH ? (
            <VoiceLab onRevoice={() => navigate(REVOICE)} />
          ) : selectedId === KIT ? (
            <BrandKit />
          ) : selectedId === SCRIPT_SHORT ? (
            <ScriptShort />
          ) : selectedId === REVOICE ? (
            <div className="newjob-wrap"><RevoicePanel onVoiceLibrary={() => navigate(SPEECH)} /></div>
          ) : selected ? (
            <Workspace key={selected.id} job={selected} onChanged={changed} />
          ) : selectedId === VIDEO_SHORTS || selectedId === LEARN_VIDEO ? (
            <NewJob key={selectedId} purpose={selectedId === LEARN_VIDEO ? 'learn' : 'create'}
              onCreated={(job) => {
                setSelectedId(job.id)
                changed()
              }}
              onOpen={navigate}
            />
          ) : <CreateHome onChoose={navigate} />}
        </section>
      </main>
    </div>
  )
}
