import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import DubEditor, { NarrationDurationSelect, SmartDurationLimit } from './DubEditor.jsx'

export default function NarrationBatch({ job, clips, busy, act }) {
  const [open, setOpen] = useState(false)
  const [voices, setVoices] = useState(null)
  const [voiceId, setVoiceId] = useState('')
  const [version, setVersion] = useState('')
  const [mode, setMode] = useState('original')
  const [pacing, setPacing] = useState('fixed')
  const [smartLimit, setSmartLimit] = useState(null)
  const [durationOverrides, setDurationOverrides] = useState({})
  const [ambience, setAmbience] = useState(false)
  const [ambienceLevel, setAmbienceLevel] = useState(18)
  const [ambienceAvailable, setAmbienceAvailable] = useState(false)
  const [selected, setSelected] = useState([])
  const [drafts, setDrafts] = useState([])
  const [warnings, setWarnings] = useState({})
  const [fits, setFits] = useState({})
  const [shorter, setShorter] = useState(null)
  const reportFit = useCallback((index, result) => {
    setFits((previous) => JSON.stringify(previous[index]) === JSON.stringify(result)
      ? previous : { ...previous, [index]: result })
  }, [])
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

  const clearDrafts = () => { setDrafts([]); setWarnings({}); setFits({}); setShorter(null) }
  const reportWarnings = useCallback((index, count) => {
    setWarnings((previous) => previous[index] === count ? previous : { ...previous, [index]: count })
  }, [])
  const prepare = async () => {
    setWorking(mode !== 'original' ? 'Drafting narration…' : 'Preparing scripts…')
    try {
      const response = await act(() => api.draftDubs(job.id, {
        clips: chosen, voice: voiceId, version: version === '' ? null : Number(version), mode,
      }))
      setDrafts(response.clips)
      setShorter(null)
      setFits({})
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
        smart_max_seconds: pacing === 'smart' ? smartLimit : null,
        duration_overrides: Object.fromEntries(prepared.filter((clip) => durationOverrides[clip.index])
          .map((clip) => [clip.index, durationOverrides[clip.index]])),
      }))
      clearDrafts()
      setDurationOverrides({})
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
          <option value="compact">Suggest shorter wording</option>
        </select>
      </div>
      <p className="muted small">Existing line edits are kept. Review any suggested wording before generating.</p>
      <div className="row-btns">
        <NarrationDurationSelect value={pacing} disabled={disabled} onChange={setPacing} />
        {pacing === 'smart' && <SmartDurationLimit value={smartLimit} onChange={setSmartLimit} disabled={disabled} />}
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
      {pacing === 'fixed' && <p className="muted small">Keeps the length of the selected video and adjusts speech tempo.</p>}
      {pacing === 'smart' && <p className="muted small">
        Tightens pauses, speeds speech up by at most 15%, and adds up to four seconds within the selected limit.
        Choose Suggest shorter wording above to review a condensed script.
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
      {prepared.length > 0 && <section aria-label="Batch duration estimates">
        <h4>Fit before queueing</h4>
        <ul>
          {prepared.map((clip) => {
            const fit = fits[clip.index]
            return <li key={clip.index}>
              <strong>#{clip.index} {clip.title}</strong>
              {fit ? <> · {fit.duration.toFixed(1)} s before end card
                {fit.rate ? ` · ${fit.rate.toFixed(2)}× tempo` : ' · tempo varies by line'}
                {fit.over > 0.000001 && <span className="warn">
                  {' '}· {fit.over.toFixed(2)} s over limit
                  {fit.sourceTooLong ? ' · trim the source or raise the limit' : ` · would need ${fit.requiredRate.toFixed(2)}×`}
                </span>}
                {fit.over > 0.000001 && !fit.sourceTooLong && <button className="btn sm ghost" disabled={disabled}
                  onClick={async () => {
                    setWorking('Drafting a shorter line…')
                    setShorter(null)
                    try {
                      const result = await act(() => api.suggestDubLine(job.id, clip.index, fit.longest, true))
                      if (result) setShorter({ ...result, clip: clip.index, line: fit.longest })
                    } catch { /* shown by act() */ } finally { setWorking('') }
                  }}>Suggest shorter line</button>}
              </> : ' · Estimate unavailable'}
              {shorter?.clip === clip.index && <div className="small">
                <p>Current line — {shorter.original}</p>
                <p>Suggested line — {shorter.text}</p>
                <button className="btn sm" disabled={disabled} onClick={async () => {
                  setWorking('Saving shorter line…')
                  try {
                    await act(() => api.putDub(job.id, clip.index, { texts: { [shorter.line]: shorter.text },
                      expected_texts: { [shorter.line]: shorter.original } }))
                    setShorter(null)
                    setRevision((current) => current + 1)
                  } catch { /* shown by act() */ } finally { setWorking('') }
                }}>Use shorter line</button>
                <button className="btn sm ghost" disabled={disabled} onClick={() => setShorter(null)}>Dismiss</button>
              </div>}
            </li>
          })}
        </ul>
      </section>}
      {prepared.map((clip) => {
        const override = durationOverrides[clip.index]
        const effectivePacing = override?.pacing ?? pacing
        const effectiveLimit = override ? override.smart_max_seconds : smartLimit
        const setDuration = (value, limit) => setDurationOverrides((previous) => ({
          ...previous, [clip.index]: value ? { pacing: value, smart_max_seconds: value === 'smart' ? limit : null } : null,
        }))
        return (
          <details key={clip.index}>
            <summary>Review short {clip.index} · {clip.title}
              {override && <span className="small"> · Custom duration</span>}
              {warnings[clip.index] > 0 && <span className="small warn">
                {' '}· {warnings[clip.index]} possible incomplete {warnings[clip.index] === 1 ? 'line' : 'lines'}
              </span>}
            </summary>
            <div className="row-btns">
              <NarrationDurationSelect inherit value={override?.pacing ?? ''} disabled={disabled}
                onChange={(value) => setDuration(value, effectivePacing === 'smart' ? effectiveLimit : null)} />
              {override?.pacing === 'smart' && (
                <SmartDurationLimit value={effectiveLimit} disabled={disabled}
                  onChange={(limit) => setDuration('smart', limit)} />
              )}
            </div>
            <DubEditor key={revision} job={job} clip={clip} busy={busy || !!working} act={editAct} fixedVoice
              previewPacing={effectivePacing} previewSmartLimit={effectiveLimit} onWarnings={reportWarnings}
              batchVoices={voices ?? []} onFit={reportFit} />
          </details>
        )
      })}
      {prepared.length > 0 && (
        <button className="btn sm" disabled={disabled} onClick={generate}>Queue narration ({prepared.length})</button>
      )}
      {working && <p role="status" className="muted small">{working}</p>}
    </section>
  )
}
