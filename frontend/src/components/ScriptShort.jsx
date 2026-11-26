import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { assColor as cssCaptionColor, LookChip, Seg } from './CaptionControls.jsx'
import ScriptGraphics from './ScriptGraphics.jsx'
import ScriptMusic from './ScriptMusic.jsx'
import ScriptVisuals from './ScriptVisuals.jsx'
import ThemeLibrary from './ThemeLibrary.jsx'
import TakeReview from './TakeReview.jsx'

const recoveryKey = 'clipforge.script-short.recovery.v1'

function restoreWorkspace() {
  try {
    return JSON.parse(window.localStorage.getItem(recoveryKey)) ?? {}
  } catch {
    return {}
  }
}

export default function ScriptShort({ openDraftId }) {
  const [recovery] = useState(restoreWorkspace)
  const [section, setSection] = useState(recovery.section ?? 'script')
  const [drafts, setDrafts] = useState([])
  const [draftId, setDraftId] = useState(recovery.draftId ?? '')
  const [draftToOpen, setDraftToOpen] = useState(recovery.draftId ?? '')
  const [notice, setNotice] = useState('')
  const [imageJob, setImageJob] = useState(null)
  const [standalone, setStandalone] = useState(null)
  const [sceneSelection, setSceneSelection] = useState(0)
  const [catalog, setCatalog] = useState(null)
  const [monitor, setMonitor] = useState('edit')
  const [captionPresets, setCaptionPresets] = useState(null)
  const [graphics, setGraphics] = useState(recovery.graphics ?? [])
  const [musicGain, setMusicGain] = useState(recovery.musicGain ?? 0)
  const [music, setMusic] = useState(recovery.music ?? "")
  const [captionLook, setCaptionLook] = useState(recovery.captionLook ?? {})
  const setCaption = (key, value) => {
    setCaptionLook((current) => ({ ...current, [key]: value }))
    setMonitor('edit')
  }
  const [voices, setVoices] = useState([])
  const [voice, setVoice] = useState(recovery.voice ?? '')
  const [version, setVersion] = useState(recovery.version ?? '')
  const [title, setTitle] = useState(recovery.title ?? '')
  const [text, setText] = useState(recovery.text ?? '')
  const [brief, setBrief] = useState(recovery.brief ?? '')
  const [creative, setCreative] = useState(recovery.creative ?? { theme: "", reference: "", image: null })
  const [themeMusicPrompt, setThemeMusicPrompt] = useState(recovery.themeMusicPrompt ?? "")
  const [storyboard, setStoryboard] = useState(recovery.storyboard ?? [])
  const [imageMotion, setImageMotion] = useState(recovery.imageMotion ?? true)
  const [asset, setAsset] = useState('')
  const [chosen, setChosen] = useState(recovery.chosen ?? [])
  const [result, setResult] = useState(recovery.result ?? null)
  const [busy, setBusy] = useState('')
  const [preview, setPreview] = useState(recovery.preview ?? null)
  const [plan, setPlan] = useState(null)
  const [timing, setTiming] = useState(recovery.timing ?? 'equal')
  const [sceneLines, setSceneLines] = useState(recovery.sceneLines ?? {})
  const audio = useRef(null)
  const steps = useRef(null)
  const moveToSection = (next) => {
    setSection(next)
    requestAnimationFrame(() => steps.current?.querySelector('[aria-pressed="true"]')?.focus())
  }
  const [error, setError] = useState('')
  const refresh = () => api.scriptShorts().then(setCatalog)

  useEffect(() => {
    api.captionPresets().then(setCaptionPresets).catch((failure) => setError(failure.message))
    Promise.all([refresh(), api.scriptDrafts().then(({ items }) => setDrafts(items)), api.voices().then(({ voices: items }) =>
      setVoices(items.filter((item) => item.builds?.some((build) => build.prompt))))])
      .catch((failure) => setError(failure.message))
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(recoveryKey, JSON.stringify({
        section, draftId, voice, version, title, text, brief, storyboard, imageMotion,
        chosen, preview, timing, sceneLines, result, captionLook, music, musicGain, creative, themeMusicPrompt, graphics,
      }))
    } catch {
      // Named drafts still work if this browser blocks local storage.
    }
  }, [section, draftId, voice, version, title, text, brief, storyboard, imageMotion, chosen, preview, timing, sceneLines, result, captionLook, music, musicGain, creative, themeMusicPrompt, graphics])

  const saveDraft = async (copy = false) => {
    setBusy('draft')
    setError('')
    setNotice('')
    try {
      const saved = await api.saveScriptDraft({ id: copy ? null : draftId || null, title, text, voice,
        version: preview?.version ?? (version ? Number(version) : null), assets: chosen,
        scene_lines: Object.fromEntries(chosen.filter((id) => id in sceneLines).map((id) => [id, sceneLines[id]])),
        timing, section, preview_id: preview?.id ?? null, storyboard, image_motion: imageMotion, ...captionLook, music, music_gain_db: musicGain,
        graphics, theme: creative.theme, theme_music_prompt: themeMusicPrompt, visual_reference: creative.reference, reference_image: creative.image })
      setDrafts((previous) => [saved, ...previous.filter((draft) => draft.id !== saved.id)])
      setDraftId(saved.id)
      setDraftToOpen(saved.id)
      setNotice('Draft saved on this device.')
      return saved
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy('')
    }
  }
  useEffect(() => { setImageJob(null) }, [draftId])

  useEffect(() => {
    if (!draftId) return
    let active = true
    let timer
    async function poll() {
      let retry = true
      try {
        const { job } = await api.storyboardImageJob(draftId)
        if (!active) return
        setImageJob((current) => JSON.stringify(current) === JSON.stringify(job) ? current : job)
        retry = ['queued', 'running'].includes(job?.status)
      } catch {
        // A reconnect can recover progress from the backend.
      } finally {
        if (active && retry) timer = window.setTimeout(poll, 1500)
      }
    }
    poll()
    return () => { active = false; window.clearTimeout(timer) }
  }, [draftId, imageJob?.id])

  useEffect(() => {
    if (!imageJob) return
    setStoryboard((items) => items.map((item, index) => {
      const completed = imageJob.scenes.find((scene) =>
        scene.index === index && scene.prompt === item.prompt && scene.image_id)
      return completed && !item.asset_id ? { ...item, image_id: completed.image_id } : item
    }))
  }, [imageJob])

  const queueImages = async (indexes) => {
    const saved = await saveDraft()
    if (!saved) return
    setBusy('queue-images')
    setError('')
    try {
      const { job } = await api.queueStoryboardImages(saved.id, indexes)
      setImageJob(job)
      setNotice('Storyboard images queued. You can leave this page.')
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy('')
    }
  }

  const openDraft = async (draft = drafts.find((item) => item.id === draftToOpen)) => {
    if (!draft) return
    setBusy('draft')
    setError('')
    setNotice('')
    let savedPreview = null
    if (draft.preview_id) {
      try {
        savedPreview = await api.scriptNarration(draft.preview_id)
      } catch {
        setNotice('Draft opened. Generate a fresh narration preview to use saved scene timing.')
      }
    }
    setDraftId(draft.id)
    setDraftToOpen(draft.id)
    setSceneSelection(0)
    setGraphics(draft.graphics ?? [])
    setMusic(draft.music ?? "")
    setMusicGain(draft.music_gain_db ?? 0)
    setTitle(draft.title)
    setText(draft.text)
    setBrief('')
    setStoryboard(draft.storyboard ?? [])
    setCreative({ theme: draft.theme ?? '', reference: draft.visual_reference ?? '', image: draft.reference_image ?? null })
    setThemeMusicPrompt(draft.theme_music_prompt ?? '')
    setImageMotion(draft.image_motion ?? true)
    setCaptionLook({ caption_style: draft.caption_style ?? 'simple', caption_preset: draft.caption_preset,
      caption_position: draft.caption_position })
    setVoice(draft.voice)
    setVersion(draft.version == null ? '' : String(draft.version))
    setChosen(draft.assets)
    setSceneLines(draft.scene_lines)
    setTiming(draft.timing)
    setPreview(savedPreview)
    setPlan(null)
    setAsset('')
    setResult(null)
    setBusy('')
    moveToSection(draft.section)
  }
  const opened = useRef(null)
  useEffect(() => {
    const draft = drafts.find((item) => item.id === openDraftId)
    if (draft && opened.current !== draft.id) {
      opened.current = draft.id
      openDraft(draft)
    }
  }, [openDraftId, drafts])

  const newDraft = () => {
    setDraftId('')
    setDraftToOpen('')
    setStandalone(null)
    setTitle('')
    setText('')
    setBrief('')
    setStoryboard([])
    setCreative({ theme: '', reference: '', image: null })
    setThemeMusicPrompt('')
    setImageMotion(true)
    setCaptionLook({})
    setMusic('')
    setGraphics([])
    setMusicGain(0)
    setVoice('')
    setVersion('')
    setChosen([])
    setAsset('')
    setResult(null)
    setError('')
    setNotice('')
    invalidatePreview()
    moveToSection('script')
  }

  const selectedVoice = voices.find((item) => item.id === voice)
  const kokoroVoices = catalog?.kokoro_voices ?? []
  const voiceName = selectedVoice?.name ?? kokoroVoices.find((item) => item.id === voice)?.name
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
  const run = async (operation, fn) => {
    setBusy(operation)
    setError('')
    setNotice('')
    try {
      await fn()
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy('')
    }
  }
  const changeScript = (value) => {
    setText(value)
    setStoryboard([])
    invalidatePreview()
  }
  const writeScript = () => {
    if (text.trim() && !window.confirm('Replace the current script with a generated draft?')) return
    run('write', async () => {
      const draft = await api.writeScript(brief.trim())
      setTitle(draft.title)
      changeScript(draft.text)
    })
  }
  const saveGeneratedImage = async (id) => {
    const saved = await api.saveScriptImage(id, imageMotion)
    await refresh()
    setChosen((items) => items.includes(saved.id) || items.length >= 6 ? items : [...items, saved.id])
    setPlan(null)
    setNotice('Image saved to your local B-roll library.')
  }
  const useStoryboard = async () => {
    const generatedAssets = []
    for (const scene of storyboard) {
      generatedAssets.push(scene.asset_id || (await api.saveScriptImage(scene.image_id, scene.motion ?? imageMotion)).id)
    }
    const take = preview ?? await api.previewScriptNarration(narration())
    setPreview(take)
    setChosen(generatedAssets)
    setSceneLines(Object.fromEntries(generatedAssets.map((id, index) => [id, storyboard[index].line])))
    setTiming('lines')
    setPlan(null)
    await refresh()
    moveToSection('export')
  }

  const applyTheme = (plan) => {
    setStoryboard((items) => {
      const used = new Set(items.flatMap((item) => [item.image_id, item.asset_id].filter(Boolean)))
      return items.map((item, index) => {
        const asset = plan.scenes[index]?.asset
        if (item.image_id || item.asset_id || !asset || used.has(asset.asset)) return item
        used.add(asset.asset)
        return asset.kind === 'video' ? { ...item, asset_id: asset.asset } : { ...item, image_id: asset.asset }
      })
    })
    if (!music && plan.music.asset) setMusic(plan.music.asset.asset)
    if (!music && !plan.music.asset) setThemeMusicPrompt(plan.music_prompt)
    setNotice('Theme matches applied. Generate remaining images in Storyboard and any missing music in Finish.')
  }

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

  const retakeLine = async (index) => {
    setBusy(`retake-${index}`)
    setError('')
    setNotice('')
    try {
      setPreview(await api.retakeScriptLine(preview.id, index, narration()))
      setNotice(`Line ${index + 1} regenerated. Other takes and scene choices are retained.`)
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy('')
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

  const graphicsReady = graphics.every((item) => preview?.lines[item.line] && item.duration >= 0.5
    && item.duration <= 8 && (item.kind !== 'label' || item.text.trim()))
  const generate = async (event) => {
    event.preventDefault()
    if (busy || section !== 'export' || !scriptReady || !footageReady || !graphicsReady) return
    setBusy('render')
    setError('')
    try {
      const created = await api.createScriptShort({ ...narration(), title: title.trim() || 'Script short', ...captionLook, music, music_gain_db: musicGain,
        assets: chosen, graphics, preview_id: preview?.id ?? null, scene_starts: sceneStarts })
      setResult(created)
      setMonitor('export')
      await refresh()
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy('')
    }
  }

  const sceneIndex = Math.min(sceneSelection, Math.max(0, storyboard.length - 1))
  const scene = storyboard[sceneIndex]
  const look = captionPresets?.presets.find((item) => item.id === (captionLook.caption_preset ?? captionPresets.default))
  const sample = (scene?.text || text).trim().split(/\s+/).slice(0, look?.words ?? 3).join(' ')
  const frame = scene?.image_id && api.scriptImage(scene.image_id)
  const showingExport = section === 'export' && result && monitor === 'export'

  return <div className="script-desktop">
    <div className="script-desktop-controls">
    <h2>Script → Short</h2>
    <p className="muted">Write your narration, choose a voice, and arrange B-roll for a captioned 9:16 video. Changes are kept in this browser.</p>
    {error && <p className="banner error" role="alert">{error}</p>}
    <div className="card row-btns">
      <label>Saved drafts <select value={draftToOpen} disabled={!!busy} onChange={(event) => setDraftToOpen(event.target.value)}>
        <option value="">Choose a draft</option>
        {drafts.map((draft) => <option key={draft.id} value={draft.id}>
          {draft.title || draft.text.slice(0, 40) || 'Untitled draft'}
        </option>)}
      </select></label>
      <button type="button" className="btn ghost" disabled={!!busy || !draftToOpen} onClick={() => openDraft()}>Open draft</button>
      <button type="button" className="btn" disabled={!!busy || (!title.trim() && !text.trim() && !chosen.length)}
        onClick={() => saveDraft()}>Save draft</button>
      {draftId && <button type="button" className="btn ghost" disabled={!!busy} onClick={() => saveDraft(true)}>Save a copy</button>}
      {(draftId || text || brief || storyboard.length > 0) && <button type="button" className="btn ghost" disabled={!!busy}
        onClick={() => window.confirm('Start a blank draft? Save changes you want to keep first.') && newDraft()}>New draft</button>}
      {notice && <p className="muted small" role="status">{notice}</p>}
    </div>
    <form className="card form big" onSubmit={generate}>
      <div ref={steps} className="script-steps" role="group" aria-label="Script short steps">
        {[['script', 'Script'], ['footage', 'Storyboard'], ['export', 'Finish']].map(([key, label]) => (
          <button key={key} type="button" className="btn ghost" aria-pressed={key === section}
            disabled={!!busy} onClick={() => moveToSection(key)}>{label}</button>
        ))}
      </div>
      {section === 'export' && <details open className="script-section form">
        <summary>Caption styling</summary>
        <Seg label="Captions" value={captionLook.caption_style ?? 'simple'} disabled={!!busy}
          options={[['karaoke', 'Animated'], ['simple', 'Simple'], ['none', 'Off']]}
          onChange={(value) => setCaption('caption_style', value)} />
        {captionPresets && captionLook.caption_style !== 'none' && <>
          <div className="opt-col">
            <span className="muted">Caption look</span>
            <div className="looks">
              {captionPresets.presets.map((preset) => <LookChip key={preset.id} p={preset}
                on={(captionLook.caption_preset ?? captionPresets.default) === preset.id} disabled={!!busy}
                onPick={() => setCaption('caption_preset', preset.id)} />)}
            </div>
          </div>
          <Seg label="Position" value={captionLook.caption_position ?? captionPresets.position} disabled={!!busy}
            options={[['bottom', 'Bottom'], ['middle', 'Middle']]}
            onChange={(value) => setCaption('caption_position', value)} />
        </>}
      </details>}
      <section className="script-section form" hidden={section !== 'script'} aria-label="Script and voice">
        <details>
          <summary>Draft a script from a topic</summary>
          <label>Topic or brief
            <textarea rows={3} value={brief} maxLength={1600} disabled={!!busy}
              placeholder="Explain why watering at the roots helps houseplants"
              onChange={(event) => setBrief(event.target.value)} />
          </label>
          <button type="button" className="btn" disabled={!!busy || !brief.trim()} onClick={writeScript}>
            {busy === 'write' ? 'Drafting script…' : 'Generate script'}
          </button>
        </details>
        <label>Title <input value={title} maxLength={120} disabled={!!busy}
          onChange={(event) => setTitle(event.target.value)} placeholder="Script short" /></label>
        <label>Script <textarea rows={7} value={text} maxLength={1200} disabled={!!busy}
          onChange={(event) => changeScript(event.target.value)} /></label>
        <span className="muted small">{text.length}/1200 characters · up to {catalog?.max_seconds ?? 60} seconds of speech</span>
        <div className="row-btns">
          <label>Narration voice <select value={voice} disabled={!!busy} onChange={(event) => {
            setVoice(event.target.value)
            setVersion('')
            invalidatePreview()
          }}>
            <option value="">Choose a voice</option>
            {voices.length > 0 && <optgroup label="Your trained voices">
              {voices.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </optgroup>}
            {kokoroVoices.length > 0 && <optgroup label="Kokoro preset voices">
              {kokoroVoices.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </optgroup>}
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
          {preview && <span className="muted small">{preview.duration.toFixed(1)} s{preview.version && ` · v${preview.version}`} · reused in export</span>}
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
                <button type="button" className="btn sm ghost" disabled={!!busy}
                  onClick={() => retakeLine(index)} aria-label={`Regenerate line ${index + 1}`}>
                  {busy === `retake-${index}` ? 'Regenerating…' : 'Retake'}
                </button>
                <TakeReview issues={line.issues} heard={line.heard} revision={preview.id} busy={busy}
                  onSuggest={() => api.reviewScriptTake(preview.id, index)} />
              </li>)}
            </ol>
          </details>
        </div>}
      </section>
      <section className="script-section form" hidden={section !== 'footage'} aria-label="Footage">
        <ScriptVisuals key={draftId} setup={catalog?.image_generation} script={text} scenes={storyboard} setScenes={setStoryboard}
          busy={busy} run={run} onSave={saveGeneratedImage} onUseStoryboard={useStoryboard} canCreate={scriptReady}
          motion={imageMotion} setMotion={setImageMotion} selected={sceneIndex} onSelect={setSceneSelection}
          narrationLines={preview?.lines} imageJob={imageJob} queueImages={queueImages}
          reference={creative} setReference={(values) => setCreative((current) => ({ ...current, ...values }))}
          onStandalone={setStandalone} />
        <ThemeLibrary theme={creative.theme} onTheme={(theme) => setCreative((current) => ({ ...current, theme }))}
          selectedImage={scene?.image_id} standaloneImage={standalone} selectedVideo={scene?.asset_id || asset} track={music}
          scenes={storyboard} script={text} reference={creative.reference}
          busy={busy || ['queued', 'running'].includes(imageJob?.status)} run={run} onApply={applyTheme} />
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
        <p className="muted small">{voiceName ?? 'Choose a voice'} · {chosen.length}
          {chosen.length === 1 ? ' clip' : ' clips'}{preview && ` · ${preview.duration.toFixed(1)} s narration ready`}</p>
        <ScriptGraphics items={graphics} setItems={setGraphics} take={preview} busy={busy}
          canPreview={scriptReady} onPreview={previewNarration} onPlay={listenToLine} />
        {!graphicsReady && <p className="warn small">Check the narration line, duration, and label text for each graphic.</p>}
        <ScriptMusic initialPrompt={themeMusicPrompt} script={text} track={music} onSelect={setMusic} available={catalog?.music_generation}
          narrationDuration={preview?.duration} busy={busy} run={run} gain={musicGain} onGain={setMusicGain}
          voice={voice} voiceVersion={version} takeId={preview?.id}
          onPreview={async () => {
            const take = preview ?? await api.previewScriptNarration(narration())
            setPreview(take)
            return { blob: await api.previewScriptMix(take.id, music, musicGain), takeId: take.id }
          }} />
        {!scriptReady && <p className="warn small">Add a script and choose a saved voice.</p>}
        {!footageReady && <p className="warn small">Add available footage and check scene timing.</p>}
        {!!catalog?.items.length && <div className="card">
          <label>Completed shorts <select value={result?.id || ''} onChange={(event) => {
            setResult(catalog.items.find((item) => item.id === event.target.value) ?? null)
            setMonitor('export')
          }}>
            {!catalog.items.some((item) => item.id === result?.id) && <option value="">Choose a short</option>}
            {catalog.items.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.duration} s</option>)}
          </select></label>
        </div>}
        {result && <div className="card">
          <h3>{result.title}</h3>
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
          disabled={!!busy || !scriptReady} onClick={() => moveToSection('footage')}>Continue to storyboard</button>}
        {section === 'footage' && <button type="button" className="primary"
          disabled={!!busy || !footageReady} onClick={() => moveToSection('export')}>Continue to finish</button>}
        {section === 'export' && <button type="submit" className="primary" disabled={!!busy || !scriptReady || !footageReady || !graphicsReady}>
          {busy === 'render' ? (preview ? 'Rendering video…' : 'Generating narration and rendering…') : 'Export short'}
        </button>}
      </div>
      {busy && <span className="muted small" role="status">Longer scripts can take a few minutes.</span>}
    </form>
    </div>
    <section className="card script-desktop-preview" aria-label="Portrait preview">
      <h3>Portrait preview</h3>
      {section === 'export' && result && <div className="row-btns" role="group" aria-label="Portrait preview mode">
        <button type="button" className="btn sm ghost" aria-pressed={!showingExport} onClick={() => setMonitor('edit')}>Edit preview</button>
        <button type="button" className="btn sm ghost" aria-pressed={!!showingExport} onClick={() => setMonitor('export')}>Last export</button>
      </div>}
      {showingExport && <video className="srcvid" controls preload="metadata"
        aria-label="Script short preview" src={api.scriptShortFile(result.id, 'short.mp4')} />}
      {!showingExport && <div className="portrait-frame">
        {frame ? <img src={frame} alt={`Storyboard scene ${sceneIndex + 1}`} />
          : scene?.asset_id ? <video controls preload="metadata" src={api.brollAssetUrl(scene.asset_id)} />
            : chosen.length ? <video controls preload="metadata" src={api.brollAssetUrl(chosen[0])} />
            : <span className="muted">Add a storyboard image or footage to preview it here.</span>}
        {frame && section === 'export' && sample && captionLook.caption_style !== 'none' &&
          <span className={`portrait-caption ${(captionLook.caption_position ?? captionPresets?.position) === 'middle' ? 'middle' : ''}`}
            style={{ fontFamily: look?.font, color: cssCaptionColor(captionLook.caption_style === 'karaoke' ? look?.active : look?.text),
              fontWeight: look?.bold === false ? 400 : 700,
              background: look?.box ? cssCaptionColor(look.outline_color) : undefined }}>
            {look?.uppercase === false ? sample : sample.toUpperCase()}
          </span>}
      </div>}
    </section>
  </div>
}
