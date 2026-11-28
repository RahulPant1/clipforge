import { useState } from 'react'
import { api } from '../api.js'

export const blankPublish = { title: '', description: '', cover_text: '', cover_time: 0, sources: '' }

export default function StudioPublish({ text, title, notes, options, setOptions, duration, busy, run, checked, setChecked }) {
  const [suggestions, setSuggestions] = useState(null)
  const update = (key, value) => setOptions((current) => ({ ...(current ?? blankPublish), [key]: value }))
  const current = options ?? blankPublish

  return <details className="card">
    <summary>YouTube upload package</summary>
    <div className="form">
      <button type="button" className="btn ghost" disabled={!!busy || !text.trim()}
        onClick={() => run('youtube-copy', async () => {
          const result = await api.studioPublishCopy({ text, title, source_notes: notes })
          setSuggestions({ ...result, text })
          setChecked(false)
          setOptions((value) => ({ ...(value ?? blankPublish), title: result.titles[0],
            description: result.description, cover_text: result.cover_text }))
        })}>Suggest titles and description</button>
      {suggestions?.text === text && <>
        <div className="row-btns">{suggestions.titles.map((option, index) => <button key={index} type="button"
          className="btn ghost" disabled={!!busy} onClick={() => update('title', option)}>{option}</button>)}</div>
        {suggestions.review_notes.length > 0 && <div>
          <h4>Claims to verify</h4>
          <ul>{suggestions.review_notes.map((note, index) => <li key={index}>{note}</li>)}</ul>
        </div>}
      </>}
      <label>YouTube title <input value={current.title} maxLength={100} disabled={!!busy}
        onChange={(event) => update('title', event.target.value)} /></label>
      <label>Description <textarea value={current.description} rows={4} maxLength={3500} disabled={!!busy}
        onChange={(event) => update('description', event.target.value)} /></label>
      <label>Reviewed source credits <textarea value={current.sources} rows={2} maxLength={1000} disabled={!!busy}
        onChange={(event) => update('sources', event.target.value)} /></label>
      <div className="row-btns">
        <label>Thumbnail text <input value={current.cover_text} maxLength={100} disabled={!!busy}
          onChange={(event) => update('cover_text', event.target.value)} /></label>
        <label>Thumbnail frame (seconds) <input type="number" min={0} max={duration ?? 60} step={0.1}
          value={current.cover_time} disabled={!!busy} onChange={(event) => update('cover_time', Number(event.target.value))} /></label>
      </div>
      <label><input type="checkbox" checked={checked && !!options} disabled={!!busy}
        onChange={(event) => {
          setChecked(event.target.checked)
          update('title', current.title)
        }} /> Include YouTube package after I review the copy and claims</label>
    </div>
  </details>
}
