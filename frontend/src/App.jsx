import { useCallback, useEffect, useState } from 'react'
import { api } from './api.js'
import History from './components/History.jsx'
import BrandKit from './components/BrandKit.jsx'
import BrandVoice from './components/BrandVoice.jsx'
import VoiceLab from './components/VoiceLab.jsx'
import HealthBadge from './components/HealthBadge.jsx'
import JobList from './components/JobList.jsx'
import NewJob from './components/NewJob.jsx'
import Workspace from './components/Workspace.jsx'
import { isBusy } from './util.js'

const VOICE = 'brand-voice'
const KIT = 'brand-kit'
const SPEECH = 'your-voice'

export default function App() {
  const [jobs, setJobs] = useState([])
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
        <HealthBadge />
      </header>

      {error && <div className="banner error">{error}</div>}

      <main>
        <aside>
          <button className={`newjob ${selectedId === null ? 'on' : ''}`} onClick={() => setSelectedId(null)}>
            + New job
          </button>
          <button className={`voicebtn ${selectedId === VOICE ? 'on' : ''}`} onClick={() => setSelectedId(VOICE)}>
            🎙 Brand voice
          </button>
          <button className={`voicebtn ${selectedId === KIT ? 'on' : ''}`} onClick={() => setSelectedId(KIT)}>
            🎨 Brand kit
          </button>
          <button className={`voicebtn ${selectedId === SPEECH ? 'on' : ''}`} onClick={() => setSelectedId(SPEECH)}>
            🗣 Voice Library
          </button>
          <History />
          <JobList jobs={jobs} selectedId={selectedId} onSelect={setSelectedId} onDelete={onDelete} />
        </aside>
        <section className="detail">
          {selectedId === VOICE ? (
            <BrandVoice />
          ) : selectedId === SPEECH ? (
            <VoiceLab />
          ) : selectedId === KIT ? (
            <BrandKit />
          ) : selected ? (
            <Workspace key={selected.id} job={selected} onChanged={changed} />
          ) : (
            <NewJob
              onCreated={(job) => {
                setSelectedId(job.id)
                changed()
              }}
              onOpen={setSelectedId}
            />
          )}
        </section>
      </main>
    </div>
  )
}
