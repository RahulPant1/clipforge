import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function ScriptMusic({ initialPrompt, script, track, onSelect, available, narrationDuration, busy, run, gain, onGain, voice, voiceVersion, takeId, onPreview }) {
  const [library, setLibrary] = useState(null)
  const [prompt, setPrompt] = useState(initialPrompt ?? '')
  useEffect(() => { setPrompt(initialPrompt ?? '') }, [initialPrompt])
  const [mix, setMix] = useState(null)
  const [customDuration, setCustomDuration] = useState('')
  const defaultDuration = Math.min(30, Math.max(10, Math.ceil(narrationDuration ?? 20)))
  const duration = customDuration === '' ? defaultDuration : Number(customDuration)

  useEffect(() => {
    api.sounds().then(setLibrary).catch(() => {})
  }, [])

  useEffect(() => () => {
    if (mix) URL.revokeObjectURL(mix.url)
  }, [mix])

  const mixKey = (id) => JSON.stringify([id, track, gain, script, voice, voiceVersion])
  const audioMix = mix?.key === mixKey(takeId) ? mix.url : null

  return <details className="card">
    <summary>Background music</summary>
    <div className="form">
      <div className="row-btns">
        <label>Music track <select aria-label="Music track" value={track} disabled={!!busy} onChange={(event) => onSelect(event.target.value)}>
          <option value="">No music</option>
          {track && !library?.music.includes(track) && <option value={track}>{track}</option>}
          {library?.music.map((name) => <option key={name} value={name}>{name}</option>)}
        </select></label>
        <button type="button" className="btn ghost" disabled={!!busy}
          onClick={() => run('music-library', async () => setLibrary(await api.sounds()))}>Refresh music library</button>
      </div>
      {track && <audio key={track} aria-label="Music track preview" controls preload="none" src={api.musicTrack(track)} />}
      {track && <>
        <label>Music level ({gain > 0 ? '+' : ''}{gain} dB)
          <input type="range" aria-label="Music level" min={-18} max={6} step={1} value={gain}
            disabled={!!busy} onChange={(event) => onGain(Number(event.target.value))} />
        </label>
        <div className="row-btns">
          <button type="button" className="btn ghost" disabled={!!busy || !voice || !script.trim()}
            onClick={() => run('music-preview', async () => {
              const preview = await onPreview()
              setMix({ url: URL.createObjectURL(preview.blob), key: mixKey(preview.takeId) })
            })}>{busy === 'music-preview' ? 'Preparing mix…' : 'Preview voice and music'}</button>
          {audioMix && <audio key={audioMix} aria-label="Voice and music preview" controls src={audioMix} />}
        </div>
      </>}
      {library?.folder && <p className="muted small">To add your own tracks, place them in <code>{library.folder}</code> and refresh.</p>}
      <label>Music prompt
        <textarea rows={3} maxLength={480} value={prompt} disabled={!!busy}
          placeholder="Soft piano, light percussion, slow tempo, no vocals"
          onChange={(event) => setPrompt(event.target.value)} />
      </label>
      <div className="row-btns">
        <button type="button" className="btn ghost" disabled={!!busy || !script.trim()}
          onClick={() => run('music-suggest', async () => {
            const suggested = await api.suggestScriptMusic(script.trim())
            setPrompt(suggested.prompt)
          })}>{busy === 'music-suggest' ? 'Suggesting music…' : 'Suggest from script'}</button>
        <label>Track length (seconds) <input type="number" min={10} max={30} step={1}
          value={customDuration || defaultDuration} disabled={!!busy}
          onChange={(event) => setCustomDuration(event.target.value)} /></label>
        <button type="button" className="btn" disabled={!!busy || !available || !prompt.trim()
          || !Number.isInteger(duration) || duration < 10 || duration > 30}
          onClick={() => run('music-generate', async () => {
            const generated = await api.generateScriptMusic(prompt.trim(), duration)
            onSelect(generated.track)
            setLibrary(await api.sounds())
          })}>{busy === 'music-generate' ? 'Generating instrumental…' : 'Generate instrumental'}</button>
      </div>
      {available === false && <p className="muted small">Configure the local ACE-Step launcher to generate instrumentals.</p>}
    </div>
  </details>
}
