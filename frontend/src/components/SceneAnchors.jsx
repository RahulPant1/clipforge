import { useRef, useState } from 'react'
import { api } from '../api.js'

export default function SceneAnchors({ job, clip, script, busy, audio, act }) {
  const video = useRef(null)
  const [open, setOpen] = useState(false)
  const [source, setSource] = useState('')
  const [narration, setNarration] = useState('')
  const [editing, setEditing] = useState(null)
  const [saving, setSaving] = useState(false)
  const disabled = busy || saving
  const anchors = clip.options.dub_anchors || []
  const duration = script.pacing !== 'source' ? script.track_duration : null
  const reset = () => { setSource(''); setNarration(''); setEditing(null) }
  const update = async (next) => {
    setSaving(true)
    try {
      await act(() => api.editClip(job.id, clip.index, { options: { ...clip.options, dub_anchors: next } }))
      reset()
    } catch { /* shown by act() */ } finally { setSaving(false) }
  }

  const save = async (event) => {
    event.preventDefault()
    const anchor = { source: Number(source), narration: Number(narration) }
    const next = anchors.filter((_, index) => index !== editing)
    await update([...next, anchor])
  }

  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Manual scene anchors{anchors.length ? ` (${anchors.length})` : ''}</summary>
      {open && <>
        <p className="muted small">Pair a time in the source video with a time in the generated voice track.
          Anchors take priority over nearby automatic cuts.</p>
        <video ref={video} controls playsInline muted preload="metadata" aria-label="Source video for scene anchors"
               style={{ width: '100%', maxWidth: 360 }}
               src={`${api.fileUrl(job.id, 'source.mp4')}#t=${clip.start},${clip.end}`} />
        <form onSubmit={save} className="row-btns">
          <label className="small">Source time (s){' '}
            <input type="number" min={(clip.start + 0.05).toFixed(2)} max={(clip.end - 0.05).toFixed(2)}
                   step="any" required value={source} disabled={disabled} onChange={(event) => setSource(event.target.value)} />
          </label>
          <button type="button" className="btn sm ghost" disabled={disabled}
                  onClick={() => { if (video.current) setSource(video.current.currentTime.toFixed(3)) }}>
            Use video position
          </button>
          <label className="small">Narration time (s){' '}
            <input type="number" min="0.05" max={duration ? duration - 0.05 : undefined}
                   step="any" required value={narration} disabled={disabled}
                   onChange={(event) => setNarration(event.target.value)} />
          </label>
          <button type="button" className="btn sm ghost" disabled={disabled || !script.track}
                  onClick={() => { if (audio.current) setNarration(audio.current.currentTime.toFixed(3)) }}>
            Use voice position
          </button>
          <button className="btn sm" disabled={disabled || (editing === null && anchors.length >= 24)}>
            {editing === null ? 'Add anchor' : 'Save anchor'}
          </button>
          {editing !== null && <button type="button" className="btn sm ghost" onClick={reset}>Cancel</button>}
        </form>
        {anchors.length > 0 && <ul>
          {anchors.map((anchor, index) => {
            const outside = anchor.source < clip.start + 0.05 || anchor.source > clip.end - 0.05
              || (duration && anchor.narration > duration - 0.05)
            return <li key={`${anchor.source}-${anchor.narration}`}>
              {anchor.source.toFixed(2)} s source → {anchor.narration.toFixed(2)} s narration
              {outside && <span className="small warn"> · outside the current timing</span>}{' '}
              <button className="btn sm ghost" disabled={disabled} onClick={() => {
                setEditing(index)
                setSource(String(anchor.source))
                setNarration(String(anchor.narration))
              }}>Edit</button>{' '}
              <button className="btn sm ghost" disabled={disabled}
                      onClick={() => update(anchors.filter((_, i) => i !== index))}>Remove</button>
            </li>
          })}
        </ul>}
      </>}
    </details>
  )
}
