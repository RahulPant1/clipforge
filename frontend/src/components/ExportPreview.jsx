import { useState } from 'react'
import { api } from '../api.js'

export default function ExportPreview({ jobId, index, busy, act }) {
  const [pending, setPending] = useState(false)
  const [src, setSrc] = useState('')
  const [start, setStart] = useState('0')
  const [length, setLength] = useState('10')
  const generate = async (event) => {
    event.preventDefault()
    setPending(true)
    setSrc('')
    try {
      const { file } = await act(() => api.exportPreview(jobId, index, {
        start: Number(start), length: Number(length),
      }))
      setSrc(api.fileUrl(jobId, file, Date.now()))
    } catch { /* shown by act() */ } finally { setPending(false) }
  }
  return (
    <div>
      <form className="row-btns" onSubmit={generate} aria-label={`Preview range for short ${index}`}>
        <label className="small">Output start (s){' '}
          <input type="number" min="0" step="0.01" required value={start} disabled={pending || busy}
                 onChange={(event) => { setStart(event.target.value); setSrc('') }} />
        </label>
        <label className="small">Length (s){' '}
          <input type="number" min="0.5" max="10" step="0.01" required value={length} disabled={pending || busy}
                 onChange={(event) => { setLength(event.target.value); setSrc('') }} />
        </label>
        <button className="btn sm ghost" disabled={busy || pending}>
          {pending ? 'Rendering preview…' : 'Preview export'}
        </button>
      </form>
      {src && <video aria-label={`Export preview for short ${index}`} controls playsInline preload="metadata"
                     style={{ display: 'block', width: '100%', maxWidth: 240 }} src={src} />}
    </div>
  )
}
