import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import DubEditor from './DubEditor.jsx'

export default function NarrationBatch({ job, clips, busy, act }) {
  const [open, setOpen] = useState(false)
  const [voices, setVoices] = useState(null)
  const [voiceId, setVoiceId] = useState('')
  const [version, setVersion] = useState('')
  const [mode, setMode] = useState('original')
  const [pacing, setPacing] = useState('source')
  const [ambience, setAmbience] = useState(false)
  const [ambienceLevel, setAmbienceLevel] = useState(18)
  const [ambienceAvailable, setAmbienceAvailable] = useState(false)
  const [selected, setSelected] = useState([])
  const [drafts, setDrafts] = useState([])
  const [warnings, setWarnings] = useState({})
  const [revision, setRevision] = useState(0)
  const [working, setWorking] = useState('')
  const [editing, setEditing] = useState(0)
  const eligible = clips.filter((clip) => !clip.visual)
  const chosen = eligible.filter((clip) => selected.includes(clip.index)).map((clip) => clip.index)
  const prepared = eligible.filter((clip) => drafts.includes(clip.index))
  const voice = voices?.find((item) => item.id === voiceId)
  const disabled = busy || !!working || editing > 0

  useEffect(() => {
    if (!open) return
    let active = true
    api.voices().then(({ voices: items }) => {
      if (active) setVoices(items.filter((item) => item.builds?.some((build) => build.prompt)))
    }).catch(() => { if (active) setVoices([]) })
    api.narrationOptions().then((options) => {
      if (active) setAmbienceAvailable(options.ambience_available)
    }).catch(() => {})
    return () => { active = false }
  }, [open])

  const clearDrafts = () => { setDrafts([]); setWarnings({}) }
  const reportWarnings = useCallback((index, count) => {
    setWarnings((previous) => previous[index] === count ? previous : { ...previous, [index]: count })
  }, [])
  const prepare = async () => {
    setWorking(mode === 'rewrite' ? 'Drafting narration…' : 'Preparing scripts…')
    try {
      const response = await act(() => api.draftDubs(job.id, {
        clips: chosen, voice: voiceId, version: version === '' ? null : Number(version), mode,
      }))
      setDrafts(response.clips)
      setRevision((n) => n + 1)
    } catch { /* shown by act() */ } finally { setWorking('') }
  }

  const editAct = async (fn) => {
    setEditing((n) => n + 1)
    try { return await act(fn) } finally { setEditing((n) => n - 1) }
  }

  const generate = async () => {
    setWorking('Queueing narration…')
    try {
      await act(() => api.queueNarration(job.id, {
        clips: prepared.map((clip) => clip.index), pacing, ambience, ambience_level: ambienceLevel / 100,
      }))
      clearDrafts()
    } catch { /* shown by act() */ } finally {
      setWorking('')
    }
  }

  if (!eligible.length) return null
  if (!open) return (
    <button className="btn sm ghost" disabled={busy} onClick={() => {
      setSelected(eligible.map((clip) => clip.index))
      setOpen(true)
    }}>Narrate in my voice…</button>
  )

  return (
    <section className="card" aria-label="Narrate in my voice">
      <h3>Narrate in my voice</h3>
      {voices === null && <p className="muted small">Loading saved voices…</p>}
      {voices?.length === 0 && <p className="muted small">Build a saved voice in Voice Library to get started.</p>}
      <div className="row-btns">
        <select aria-label="Saved narration voice" value={voiceId} disabled={disabled || !voices?.length}
                onChange={(e) => { setVoiceId(e.target.value); setVersion(''); clearDrafts() }}>
          <option value="">Choose a voice</option>
          {voices?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        {voice && (
          <select aria-label="Voice version" value={version} disabled={disabled}
                  onChange={(e) => { setVersion(e.target.value); clearDrafts() }}>
            <option value="">Newest saved version</option>
            {[...voice.builds].reverse().filter((build) => build.prompt).map((build) =>
              <option key={build.version} value={build.version}>Version {build.version}</option>)}
          </select>
        )}
        <select aria-label="Narration wording" value={mode} disabled={disabled}
                onChange={(e) => { setMode(e.target.value); clearDrafts() }}>
          <option value="original">Use transcript wording</option>
          <option value="rewrite">Suggest narration rewrite</option>
        </select>
      </div>
      <p className="muted small">Existing line edits are kept. Review any suggested wording before generating.</p>
      <div className="row-btns">
        <select aria-label="Narration pacing" value={pacing} disabled={disabled} onChange={(e) => setPacing(e.target.value)}>
          <option value="source">Match source timing</option>
          <option value="natural">Natural pacing · flexible picture</option>
          <option value="scene">Scene-aware pacing · align to indexed cuts</option>
        </select>
        <label className="small" title={ambienceAvailable ? 'Uses local audio separation on export' : 'Requires Demucs'}>
          <input type="checkbox" checked={ambience} disabled={disabled || !ambienceAvailable}
                 onChange={(e) => setAmbience(e.target.checked)} /> Keep source ambience
          {!ambienceAvailable && ' (Demucs required)'}
        </label>
        {ambience && <label className="small">
          Ambience level {ambienceLevel}%
          <input type="range" min="0" max="50" value={ambienceLevel} disabled={disabled}
                 onChange={(e) => setAmbienceLevel(Number(e.target.value))} />
        </label>}
      </div>
      {pacing === 'natural' && <p className="muted small">
        If the narration needs more time, the picture slows down. Use for B-roll or screen recordings.
      </p>}
      {pacing === 'scene' && <p className="muted small">
        Uses indexed scene cuts and narration cues when available. Otherwise it slows the picture uniformly.
      </p>}
      <div className="row-btns" role="group" aria-label="Approved shorts">
        {eligible.map((clip) => (
          <label key={clip.index} className="small">
            <input type="checkbox" checked={selected.includes(clip.index)} disabled={disabled}
                   onChange={(e) => {
                     setSelected(e.target.checked ? [...selected, clip.index] : selected.filter((i) => i !== clip.index))
                     clearDrafts()
                   }} /> #{clip.index} {clip.title}
          </label>
        ))}
      </div>
      <button className="btn sm" disabled={disabled || !voice || !chosen.length} onClick={prepare}>Prepare scripts</button>
      {prepared.map((clip) => (
        <details key={clip.index}>
          <summary>Review short {clip.index} · {clip.title}
            {warnings[clip.index] > 0 && <span className="small warn">
              {' '}· {warnings[clip.index]} possible incomplete {warnings[clip.index] === 1 ? 'line' : 'lines'}
            </span>}
          </summary>
          <DubEditor key={revision} job={job} clip={clip} busy={busy || !!working} act={editAct} fixedVoice
                     previewPacing={pacing} onWarnings={reportWarnings} />
        </details>
      ))}
      {prepared.length > 0 && (
        <button className="btn sm" disabled={disabled} onClick={generate}>Queue narration ({prepared.length})</button>
      )}
      {working && <p role="status" className="muted small">{working}</p>}
    </section>
  )
}
