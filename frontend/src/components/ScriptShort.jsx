import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import TakeReview from './TakeReview.jsx'

export default function ScriptShort() {
  const [section, setSection] = useState('script')
  const [catalog, setCatalog] = useState(null)
  const [voices, setVoices] = useState([])
  const [voice, setVoice] = useState('')
  const [version, setVersion] = useState('')
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [asset, setAsset] = useState('')
  const [chosen, setChosen] = useState([])
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState('')
  const [preview, setPreview] = useState(null)
  const [plan, setPlan] = useState(null)
  const [timing, setTiming] = useState('equal')
  const [sceneLines, setSceneLines] = useState({})
  const audio = useRef(null)
  const steps = useRef(null)
  const moveToSection = (next) => {
    setSection(next)
    requestAnimationFrame(() => steps.current?.querySelector('[aria-pressed="true"]')?.focus())
  }
  const [error, setError] = useState('')
  const refresh = () => api.scriptShorts().then(setCatalog)

  useEffect(() => {
    Promise.all([refresh(), api.voices().then(({ voices: items }) =>
      setVoices(items.filter((item) => item.builds?.some((build) => build.prompt))))])
      .catch((failure) => setError(failure.message))
  }, [])

  const selectedVoice = voices.find((item) => item.id === voice)
  const assetName = (id) => catalog?.assets.find((item) => item.id === id)?.name ?? 'Unavailable footage'
  const move = (index, by) => setChosen((previous) => {
    const next = [...previous]
    const target = index + by
    if (target < 0 || target >= next.length) return previous
    const [item] = next.splice(index, 1)
    next.splice(target, 0, item)
    return next
  })

  const invalidatePreview = () => {
    setPreview(null)
    setPlan(null)
    setTiming('equal')
    setSceneLines({})
  }
  const narration = () => ({ text: text.trim(), voice, version: version ? Number(version) : null })
  const previewNarration = async () => {
    setBusy('preview')
    setError('')
    try {
      const response = await api.previewScriptNarration(narration())
      setPreview(response)
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy('')
    }
  }
  const sceneStarts = timing === 'lines' && preview ? chosen.map((id, index) => index === 0 ? 0 : sceneLines[id] ?? index) : null
  const timingValid = timing === 'equal' || (!!sceneStarts && sceneStarts.every((line, index) =>
    line < preview.lines.length && (!index || line > sceneStarts[index - 1])))
  const listenToLine = async (start) => {
    if (!audio.current) return
    audio.current.currentTime = start
    try {
      await audio.current.play()
    } catch (failure) {
      if (failure.name !== 'AbortError') setError('Could not play the narration preview.')
    }
  }

  const suggestPlan = async () => {
    setBusy('plan')
    setError('')
    setPlan(null)
    try {
      const response = await api.planScriptBroll(preview.id, chosen)
      setPlan(response.scenes)
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy('')
    }
  }
  const applyPlan = () => {
    setChosen(plan.map((scene) => scene.asset))
    setSceneLines(Object.fromEntries(plan.map((scene) => [scene.asset, scene.line])))
    setTiming('lines')
    setPlan(null)
  }
  const flagged = preview?.lines.filter((line) => line.issues?.length).length ?? 0

  const scriptReady = !!voice && !!text.trim()
  const footageReady = chosen.length > 0 && timingValid
    && !chosen.some((id) => !catalog?.assets.some((item) => item.id === id))

  const generate = async (event) => {
    event.preventDefault()
    if (busy || section !== 'export' || !scriptReady || !footageReady) return
    setBusy('render')
    setError('')
    try {
      const created = await api.createScriptShort({ ...narration(), title: title.trim() || 'Script short',
        assets: chosen, preview_id: preview?.id ?? null, scene_starts: sceneStarts })
      setResult(created)
      await refresh()
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy('')
    }
  }

  return <div className="newjob-wrap">
    <h2>Script → Short</h2>
    <p className="muted">Write your narration, choose a saved voice, and arrange local B-roll for a captioned 9:16 video.</p>
    {error && <p className="banner error" role="alert">{error}</p>}
    <form className="card form big" onSubmit={generate}>
      <div ref={steps} className="script-steps" role="group" aria-label="Script short steps">
        {[['script', 'Script & voice'], ['footage', 'Footage'], ['export', 'Preview & export']].map(([key, label]) => (
          <button key={key} type="button" className="btn ghost" aria-pressed={key === section}
            disabled={!!busy} onClick={() => moveToSection(key)}>{label}</button>
        ))}
      </div>
      <section className="script-section form" hidden={section !== 'script'} aria-label="Script and voice">
        <label>Title <input value={title} maxLength={120} disabled={!!busy}
          onChange={(event) => setTitle(event.target.value)} placeholder="Script short" /></label>
        <label>Script <textarea rows={7} value={text} maxLength={1200} disabled={!!busy}
          onChange={(event) => { setText(event.target.value); invalidatePreview() }} /></label>
        <span className="muted small">{text.length}/1200 characters · up to {catalog?.max_seconds ?? 60} seconds of speech</span>
        <div className="row-btns">
          <label>Saved voice <select value={voice} disabled={!!busy} onChange={(event) => {
            setVoice(event.target.value)
            setVersion('')
            invalidatePreview()
          }}>
            <option value="">Choose a voice</option>
            {voices.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select></label>
          {selectedVoice && <label>Version <select value={version} disabled={!!busy} onChange={(event) => { setVersion(event.target.value); invalidatePreview() }}>
            <option value="">Newest</option>
            {[...selectedVoice.builds].reverse().filter((build) => build.prompt).map((build) => (
              <option key={build.version} value={build.version}>v{build.version}</option>
            ))}
          </select></label>}
        </div>
        <div className="row-btns">
          <button type="button" className="btn" disabled={!!busy || !voice || !text.trim()} onClick={previewNarration}>
            {busy === 'preview' ? 'Generating narration preview…' : 'Preview narration'}
          </button>
          {preview && <span className="muted small">{preview.duration.toFixed(1)} s · v{preview.version} · reused in export</span>}
        </div>
        {preview && <div>
          <audio ref={audio} controls preload="metadata" aria-label="Narration preview" src={api.scriptNarrationAudio(preview.id)} />
          <details>
            <summary>Listen by script line{flagged > 0 && ` · ${flagged} flagged`}</summary>
            <ol>
              {preview.lines.map((line, index) => <li key={index}>
                <button type="button" className="btn sm ghost" onClick={() => listenToLine(line.start)}>
                  Play from {line.start.toFixed(2)} s
                </button>{' '}{line.text}
                <TakeReview issues={line.issues} heard={line.heard} revision={preview.id} busy={busy}
                  onSuggest={() => api.reviewScriptTake(preview.id, index)} />
              </li>)}
            </ol>
          </details>
        </div>}
      </section>
      <section className="script-section form" hidden={section !== 'footage'} aria-label="Footage">
        <div className="row-btns">
          <label>Local B-roll <select value={asset} disabled={!!busy || !catalog?.assets.length || chosen.length >= 6}
            onChange={(event) => setAsset(event.target.value)}>
            <option value="">Choose footage</option>
            {catalog?.assets.filter((item) => !chosen.includes(item.id)).map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select></label>
          <button type="button" className="btn" disabled={!!busy || !asset || chosen.length >= 6} onClick={() => {
            setChosen((items) => [...items, asset])
            setPlan(null)
            setAsset('')
          }}>Add footage</button>
          <button type="button" className="btn ghost" disabled={!!busy} onClick={() => refresh().catch((e) => setError(e.message))}>
            Refresh library
          </button>
        </div>
        {catalog && !catalog.assets.length && <p className="muted small">Add MP4 files to your local B-roll folder, then refresh.</p>}
        {chosen.length > 0 && <>
          {preview && <label>Scene timing <select value={timing} disabled={!!busy} onChange={(event) => setTiming(event.target.value)}>
            <option value="equal">Equal time per clip</option>
            <option value="lines">Start scenes at script lines</option>
          </select></label>}
          <ol aria-label="Footage order">
            {chosen.map((id, index) => <li key={id}>
              <div className="row-btns">
                <span>{assetName(id)}</span>
                {sceneStarts && (index === 0 ? <span className="small">Starts at line 1</span> :
                  <label className="small">Start line for {assetName(id)}
                    <select value={sceneStarts[index] < preview.lines.length ? sceneStarts[index] : ''}
                      disabled={!!busy} onChange={(event) => setSceneLines((current) => ({
                        ...current, [id]: Number(event.target.value),
                      }))}>
                      <option value="" disabled>Choose a later line</option>
                      {preview.lines.slice(1).map((line, offset) => <option key={offset} value={offset + 1}>
                        {offset + 2} · {line.start.toFixed(2)} s · {line.text.slice(0, 54)}
                      </option>)}
                    </select>
                  </label>)}
                <button type="button" className="btn sm ghost" disabled={!!busy || !index} onClick={() => move(index, -1)}
                  aria-label={`Move ${assetName(id)} earlier`}>↑</button>
                <button type="button" className="btn sm ghost" disabled={!!busy || index === chosen.length - 1} onClick={() => move(index, 1)}
                  aria-label={`Move ${assetName(id)} later`}>↓</button>
                <button type="button" className="btn sm ghost" disabled={!!busy}
                  onClick={() => { setChosen((items) => items.filter((item) => item !== id)); setPlan(null) }}>Remove</button>
              </div>
            </li>)}
          </ol>
          <p className="muted small">{sceneStarts
            ? 'A scene plays until the next selected line. Short footage loops.'
            : 'Footage plays in order for equal portions of the narration. Short footage loops.'}</p>
          <button type="button" className="btn" disabled={!!busy || !preview} onClick={suggestPlan}>
            {busy === 'plan' ? 'Analyzing your B-roll shortlist…' : 'Suggest B-roll plan'}
          </button>
          {!preview && <span className="muted small">Generate a narration preview to suggest cuts at script lines.</span>}
          {plan && <div>
            <h3>Suggested scenes</h3>
            <ol>
              {plan.map((scene) => <li key={scene.asset}>
                <strong>{assetName(scene.asset)}</strong> · line {scene.line + 1} · {preview.lines[scene.line].start.toFixed(2)} s
                <p className="small">{scene.reason}</p>
              </li>)}
            </ol>
            <div className="row-btns">
              <button type="button" className="btn" disabled={!!busy} onClick={applyPlan}>Use suggested plan</button>
              <button type="button" className="btn ghost" disabled={!!busy} onClick={() => setPlan(null)}>Dismiss</button>
            </div>
          </div>}
          {!timingValid && <p className="warn small">Choose a different, later script line for each scene.</p>}
        </>}
      </section>
      <section className="script-section form" hidden={section !== 'export'} aria-label="Preview and export">
        <p className="muted small">{selectedVoice ? selectedVoice.name : 'Choose a voice'} · {chosen.length}
          {chosen.length === 1 ? ' clip' : ' clips'}{preview && ` · ${preview.duration.toFixed(1)} s narration ready`}</p>
        {!scriptReady && <p className="warn small">Add a script and choose a saved voice.</p>}
        {!footageReady && <p className="warn small">Add available footage and check scene timing.</p>}
        {!!catalog?.items.length && <div className="card">
          <label>Completed shorts <select value={result?.id || ''} onChange={(event) =>
            setResult(catalog.items.find((item) => item.id === event.target.value) ?? null)}>
            {!catalog.items.some((item) => item.id === result?.id) && <option value="">Choose a short</option>}
            {catalog.items.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.duration} s</option>)}
          </select></label>
        </div>}
        {result && <div className="card">
          <h3>{result.title}</h3>
          <video className="srcvid" controls preload="metadata" aria-label="Script short preview"
            src={api.scriptShortFile(result.id, 'short.mp4')} />
          <div className="row-btns">
            <a className="btn" href={api.scriptShortFile(result.id, 'short.mp4')} download="script-short.mp4">Download video</a>
            <a className="btn ghost" href={api.scriptShortFile(result.id, 'captions.srt')} download="captions.srt">Download captions</a>
          </div>
        </div>}
      </section>
      <div className="script-next row-btns">
        {section !== 'script' && <button type="button" className="btn ghost" disabled={!!busy}
          onClick={() => moveToSection(section === 'export' ? 'footage' : 'script')}>Back</button>}
        {section === 'script' && <button type="button" className="primary"
          disabled={!!busy || !scriptReady} onClick={() => moveToSection('footage')}>Continue to footage</button>}
        {section === 'footage' && <button type="button" className="primary"
          disabled={!!busy || !footageReady} onClick={() => moveToSection('export')}>Continue to export</button>}
        {section === 'export' && <button type="submit" className="primary" disabled={!!busy || !scriptReady || !footageReady}>
          {busy === 'render' ? (preview ? 'Rendering video…' : 'Generating narration and rendering…') : 'Create captioned short'}
        </button>}
      </div>
      {busy && <span className="muted small" role="status">Longer scripts can take a few minutes.</span>}
    </form>
  </div>
}
