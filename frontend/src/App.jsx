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

function CreateHome({ onChoose, jobs, onDelete, onDraft, showCreate }) {
  const [drafts, setDrafts] = useState([])
  useEffect(() => { api.scriptDrafts().then(({ items }) => setDrafts(items)).catch(() => {}) }, [])
  const active = jobs.filter((job) => isBusy(job) || ['review', 'failed'].includes(job.status))
  return <div className="projects-home form">
    <div className="row-btns"><h2>Projects</h2>
      <details className="project-tools"><summary>Project tools</summary>
        <History revision={jobs.filter((job) => !isBusy(job)).map((job) => job.id).join(',')} />
      </details>
    </div>
    {showCreate && <>
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
        <strong>Content Creation Studio</strong>
        <span>Turn an idea or script into a finished YouTube short.</span>
      </button>
    </div>
    <button className="btn ghost create-learn" onClick={() => onChoose(LEARN_VIDEO)}>Learn from a video</button>
    </>}
    {(drafts.length > 0 || active.length > 0) && <section className="card">
      <h3>Continue working</h3>
      <div className="row-btns">
        {drafts[0] && <button className="btn ghost" onClick={() => onDraft(drafts[0].id)}>
          Resume {drafts[0].title || 'latest draft'}
        </button>}
        {active.slice(0, 5).map((job) => <button key={job.id} className="btn ghost" onClick={() => onChoose(job.id)}>
          {job.title || job.source} · {job.status === 'review' ? 'Needs review' : job.status}
        </button>)}
      </div>
    </section>}
    <details className="card project-list"><summary>Video projects · {jobs.length}</summary>
      {jobs.length ? <JobList jobs={jobs} selectedId={null} onSelect={onChoose} onDelete={onDelete} showHeading={false} />
        : <p className="muted">Your video projects appear here.</p>}
    </details>
    <details className="card project-list"><summary>Script drafts · {drafts.length}</summary>
      <div className="form">{drafts.map((draft) => <button key={draft.id} className="btn ghost" onClick={() => onDraft(draft.id)}>
        {draft.title || draft.text.slice(0, 60) || 'Untitled draft'}
      </button>)}</div>
      {!drafts.length && <p className="muted">Saved script drafts appear here.</p>}
    </details>
  </div>
}

export default function App() {
  const [jobs, setJobs] = useState([])
  const [menuOpen, setMenuOpen] = useState(false)
  const [railCollapsed, setRailCollapsed] = useState(false)
  const [showCreate, setShowCreate] = useState(true)
  const [openDraftId, setOpenDraftId] = useState(null)
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
    setOpenDraftId(null)
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

      <main className={railCollapsed ? "rail-collapsed" : ""}>
        <aside id="workspace-navigation" className={menuOpen ? 'menu-open' : ''}
          onKeyDown={(event) => { if (event.key === 'Escape') setMenuOpen(false) }}>
          <button className="btn ghost rail-toggle" onClick={() => setRailCollapsed(true)}>Hide project rail</button>
          <button className={`voicebtn ${creationPages.includes(selectedId) || selected ? 'on' : ''}`}
            onClick={() => { navigate(null); setShowCreate(false) }}>Projects</button>
          <button className={`voicebtn ${selectedId === SPEECH ? 'on' : ''}`} onClick={() => navigate(SPEECH)}>Voice Library</button>
          <button className={`voicebtn ${[VOICE, KIT].includes(selectedId) ? 'on' : ''}`} onClick={() => navigate(KIT)}>Brand</button>
          {selectedId !== null && <JobList jobs={jobs} selectedId={selectedId} onSelect={navigate} onDelete={onDelete} />}
          <button className="newjob" onClick={() => { navigate(null); setShowCreate(true) }}>+ New project</button>
        </aside>
        <section className="detail">
          {railCollapsed && <button className="btn ghost rail-toggle" onClick={() => setRailCollapsed(false)}>Show project rail</button>}
          {[VOICE, KIT].includes(selectedId) && <nav className="row-btns brand-nav" aria-label="Brand settings">
            <button className="btn ghost" aria-pressed={selectedId === KIT} onClick={() => navigate(KIT)}>Visual identity</button>
            <button className="btn ghost" aria-pressed={selectedId === VOICE} onClick={() => navigate(VOICE)}>Writing style</button>
          </nav>}
          {selectedId !== null && creationPages.includes(selectedId) &&
            <button className="btn sm ghost create-back" onClick={() => navigate(null)}>← Back to Projects</button>}
          {selectedId === VOICE ? (
            <BrandVoice />
          ) : selectedId === SPEECH ? (
            <VoiceLab onRevoice={() => navigate(REVOICE)} />
          ) : selectedId === KIT ? (
            <BrandKit />
          ) : selectedId === SCRIPT_SHORT ? (
            <ScriptShort openDraftId={openDraftId} />
          ) : selectedId === REVOICE ? (
            <div className="revoice-workspace"><RevoicePanel onVoiceLibrary={() => navigate(SPEECH)} /></div>
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
          ) : <CreateHome onChoose={navigate} jobs={jobs} onDelete={onDelete} showCreate={showCreate}
            onDraft={(id) => { navigate(SCRIPT_SHORT); setOpenDraftId(id) }} />}
        </section>
      </main>
    </div>
  )
}
