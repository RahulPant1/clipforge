import { useCallback, useEffect, useState } from 'react'
import { api } from './api.js'
import HealthBadge from './components/HealthBadge.jsx'
import JobList from './components/JobList.jsx'
import NewJob from './components/NewJob.jsx'
import Workspace from './components/Workspace.jsx'
import { isBusy } from './util.js'

export default function App() {
  const [jobs, setJobs] = useState([])
  const [selectedId, setSelectedId] = useState(null) // null = "new job" screen
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      setJobs(await api.listJobs())
      setError('')
    } catch (e) {
      setError(`Backend not reachable: ${e.message}`)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Poll while anything is queued or running
  const active = jobs.some(isBusy)
  useEffect(() => {
    if (!active) return
    const t = setInterval(refresh, 1500)
    return () => clearInterval(t)
  }, [active, refresh])

  const selected = jobs.find((j) => j.id === selectedId) || null

  const onDelete = async (id) => {
    try {
      await api.deleteJob(id)
      if (id === selectedId) setSelectedId(null)
    } catch (e) {
      alert(`Could not delete job: ${e.message}`)
    }
    refresh()
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
          <JobList jobs={jobs} selectedId={selectedId} onSelect={setSelectedId} onDelete={onDelete} />
        </aside>
        <section className="detail">
          {selected ? (
            <Workspace key={selected.id} job={selected} onChanged={refresh} />
          ) : (
            <NewJob
              onCreated={(job) => {
                setSelectedId(job.id)
                refresh()
              }}
            />
          )}
        </section>
      </main>
    </div>
  )
}
