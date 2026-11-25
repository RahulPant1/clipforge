import { useEffect, useState } from 'react'
import { api } from '../api.js'

function Placement({ jobId, index, item, disabled, onChange }) {
  const [start, setStart] = useState(item.start)
  const [end, setEnd] = useState(item.end)
  useEffect(() => {
    setStart(item.start)
    setEnd(item.end)
  }, [item.start, item.end])
  const file = `broll_${index}${item.asset ? `_${item.asset}` : ''}.mp4`
  return (
    <div>
      <video controls muted playsInline preload="none" aria-label="B-roll cutaway"
             style={{ width: '100%', maxWidth: 240 }} src={api.fileUrl(jobId, file, item.revision)} />
      <form className="row-btns" onSubmit={(event) => {
        event.preventDefault()
        onChange({ ...item, start: Number(start), end: Number(end) })
      }}>
        <label className="small">Output start (s){' '}
          <input type="number" min="0" step="any" required disabled={disabled} value={start}
                 onChange={(event) => setStart(event.target.value)} />
        </label>
        <label className="small">Output end (s){' '}
          <input type="number" min={(Number(start) + 0.2).toFixed(3)} step="any" required disabled={disabled}
                 value={end} onChange={(event) => setEnd(event.target.value)} />
        </label>
        <button className="btn sm" disabled={disabled}>Save placement</button>
        <button type="button" className="btn sm ghost" disabled={disabled} onClick={() => onChange(null)}>
          Remove cutaway
        </button>
      </form>
    </div>
  )
}

export default function BrollEditor({ jobId, clip, busy, act }) {
  const placements = [...(clip.options?.broll ? [clip.options.broll] : []), ...(clip.options?.brolls || [])]
    .sort((a, b) => a.start - b.start)
  const [working, setWorking] = useState(false)
  const [suggestions, setSuggestions] = useState(null)

  const run = async (operation) => {
    setWorking(true)
    try { await act(operation) } catch { /* shown by act() */ } finally { setWorking(false) }
  }
  const setPlacements = (brolls) => run(() => api.editClip(jobId, clip.index, {
    options: { ...clip.options, broll: null, brolls },
  }))
  const disabled = busy || working

  return (
    <details className="opt-col">
      <summary>Local B-roll{placements.length > 0 && ` · ${placements.length} attached`}</summary>
      <p className="muted small">Add MP4 cutaways on separate output ranges. Cutaways loop when needed, and the short keeps its own audio.</p>
      <label className="small">Add B-roll{' '}
        <input type="file" accept="video/mp4,.mp4" disabled={disabled} onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) run(() => api.uploadBroll(jobId, clip.index, file))
        }} />
      </label>
      <div>
        <button className="btn sm ghost" disabled={disabled}
                onClick={() => run(async () => setSuggestions(await api.suggestBroll(jobId, clip.index)))}>
          Find matches in local B-roll
        </button>
        {suggestions && (suggestions.items.length ? (
          <ul>
            {suggestions.items.map((item) => <li key={item.id}>
              <details>
                <summary>{item.name}</summary>
                <p className="muted small">Matches {item.matches.join(', ')}</p>
                <video controls muted playsInline preload="none" aria-label={item.name}
                       style={{ width: '100%', maxWidth: 240 }} src={api.brollAssetUrl(item.id)} />
                <button className="btn sm" disabled={disabled}
                        onClick={() => run(() => api.useBroll(jobId, clip.index, item.id))}>Use cutaway</button>
              </details>
            </li>)}
          </ul>
        ) : <p className="muted small">No matches in {suggestions.directory}. Add MP4 files with descriptive names or choose a file above.</p>)}
      </div>
      {placements.map((placement) => (
        <Placement key={placement.asset || 'legacy'} jobId={jobId} index={clip.index} item={placement}
                   disabled={disabled} onChange={(next) => setPlacements(
                     placements.flatMap((item) => item === placement ? (next ? [next] : []) : [item])
                   )} />
      ))}
    </details>
  )
}
