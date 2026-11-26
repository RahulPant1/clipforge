import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function ThemeLibrary({ theme, onTheme, selectedImage, standaloneImage, selectedVideo, track, scenes, script,
  reference, busy, run, onApply }) {
  const [items, setItems] = useState([])
  const [description, setDescription] = useState('')
  const [plan, setPlan] = useState(null)
  const [plannedFor, setPlannedFor] = useState('')
  const key = JSON.stringify([theme, script, reference, scenes])

  useEffect(() => { api.themeLibrary().then(({ items }) => setItems(items ?? [])).catch(() => {}) }, [])
  const names = [...new Set(items.map((item) => item.theme))]
  const tagged = items.filter((item) => item.theme.toLowerCase() === theme.trim().toLowerCase())
  const save = (kind, asset) => run('theme-save', async () => {
    await api.saveThemeAsset({ theme: theme.trim(), kind, asset, description: description.trim() })
    setItems((await api.themeLibrary()).items)
    setDescription('')
  })

  return <details className="card">
    <summary>Theme library</summary>
    <div className="form">
      <label>Theme <input list="clipforge-themes" value={theme} maxLength={80} disabled={!!busy}
        placeholder="Calm plant-care tutorials" onChange={(event) => onTheme(event.target.value)} /></label>
      <datalist id="clipforge-themes">{names.map((name) => <option key={name} value={name} />)}</datalist>
      {!!tagged.length && <details>
        <summary>Saved assets · {tagged.length}</summary>
        <ul>{tagged.map((item) => <li key={item.kind + item.asset}>{item.kind} · {item.description}</li>)}</ul>
      </details>}
      {(selectedImage || standaloneImage || selectedVideo || track) && <details>
        <summary>Save a reusable asset</summary>
        <label>Describe the asset
          <textarea rows={2} maxLength={600} value={description} disabled={!!busy}
            placeholder="Subject, action and look for visuals. Mood, tempo and instruments for music."
            onChange={(event) => setDescription(event.target.value)} />
        </label>
        <div className="row-btns">
          {[[selectedImage, 'image', 'Save scene image'], [standaloneImage, 'image', 'Save standalone image'], [selectedVideo, 'video', 'Save video footage'],
            [track, 'music', 'Save selected music']].map(([asset, kind, label]) => asset &&
              <button key={label} type="button" className="btn ghost"
                disabled={!!busy || !theme.trim() || !description.trim()} onClick={() => save(kind, asset)}>{label}</button>)}
        </div>
      </details>}
      <button type="button" className="btn" disabled={!!busy || !theme.trim() || !script.trim() || !scenes.length}
        onClick={() => run('theme-match', async () => {
          const matches = await api.matchTheme({ theme: theme.trim(), text: script, reference, scenes })
          setPlan(matches)
          setPlannedFor(key)
        })}>{busy === 'theme-match' ? 'Matching story assets…' : 'Find matches for this story'}</button>
      {plan && plannedFor === key && <div>
        <ol>{plan.scenes.map((pick, index) => <li key={index}>
          {pick.asset ? `${pick.asset.kind} · ${pick.asset.description}` : 'Generate this scene'}<br />
          <span className="muted small">{pick.reason}</span>
        </li>)}</ol>
        <p>{plan.music.asset ? `Music · ${plan.music.asset.description}` : 'Generate one background track'}</p>
        <p className="muted small">{plan.music.reason}</p>
        <button type="button" className="btn" disabled={!!busy}
          onClick={() => { onApply(plan); setPlan(null) }}>Use suggested assets for empty slots</button>
      </div>}
    </div>
  </details>
}
