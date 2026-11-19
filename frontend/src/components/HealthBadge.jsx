import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function HealthBadge() {
  const [h, setH] = useState(null)

  useEffect(() => {
    const load = () => api.health().then(setH).catch(() => setH({ api: 'down' }))
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [])

  if (!h) return <div className="health">checking…</div>
  if (h.api !== 'ok') return <div className="health bad">API offline</div>

  const o = h.ollama || {}
  const ok = o.ok && o.model_available
  const label = !o.ok ? 'Ollama offline' : !o.model_available ? `${o.model} not pulled` : o.model
  return (
    <div className={`health ${ok ? 'good' : 'bad'}`} title={o.error || (o.models || []).join(', ')}>
      <span className="dot" /> {label} · whisper {h.whisper} · {h.encoder}
    </div>
  )
}
