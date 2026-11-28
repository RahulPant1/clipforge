import { useState } from 'react'
import { api } from '../api.js'

export default function ScriptVisuals({ setup, script, scenes, setScenes, busy, run, onSave,
  onUseStoryboard, canCreate, motion, setMotion, selected, onSelect, narrationLines, imageJob, queueImages, reference, setReference, onStandalone, onRevise, assets = [] }) {
  const [prompt, setPrompt] = useState('')
  const [image, setImage] = useState(null)
  const [lineEdit, setLineEdit] = useState(null)
  const [count, setCount] = useState(3)
  const ready = !!scenes.length && scenes.every((scene) => scene.image_id || scene.asset_id)

  const generateImage = () => run('image', async () => {
    const { images: [generated] } = await api.generateScriptImages([{ prompt }])
    if (generated.error) throw new Error(generated.error)
    setImage(generated)
    onStandalone(generated.id)
  })
  const generating = ['queued', 'running'].includes(imageJob?.status)
  const sceneStatus = (index) => imageJob?.scenes.find((entry) =>
    entry.index === index && entry.prompt === scenes[index]?.prompt)
  const missing = scenes.flatMap((scene, i) => scene.image_id || scene.asset_id ? [] : [i])
  const failed = scenes.flatMap((_, i) => sceneStatus(i)?.status === 'failed' ? [i] : [])
  const unqueued = missing.filter((index) => !failed.includes(index))

  return <div className="script-section form">
    {generating && <p className="muted small" role="status">
      {imageJob.status === 'queued' ? 'Waiting for the GPU' : 'Generating storyboard images'} ·
      {' '}{imageJob.scenes.filter((scene) => scene.status === 'ready').length}/{imageJob.scenes.length} ready
    </p>}
    {setup && !setup.available && <p className="muted small">{setup.setup}</p>}
    <label className="small"><input type="checkbox" checked={motion} disabled={!!busy || generating}
      onChange={(event) => setMotion(event.target.checked)} /> Add gentle pan and zoom to saved footage</label>
    <details>
      <summary>Generate a standalone image</summary>
      <label>Image prompt
        <textarea rows={2} maxLength={600} value={prompt} disabled={!!busy || generating}
          placeholder="A sunlit home workspace, centered vertical framing"
          onChange={(event) => { setPrompt(event.target.value); setImage(null); onStandalone(null) }} />
      </label>
      <div className="row-btns">
        <button type="button" className="btn" disabled={!!busy || generating || !setup?.available || !prompt.trim()} onClick={generateImage}>
          {busy === 'image' ? 'Generating image…' : image ? 'Regenerate image' : 'Generate image'}
        </button>
        {image && <button type="button" className="btn ghost" disabled={!!busy || generating}
          onClick={() => run('save-image', () => onSave(image.id))}>Save to B-roll</button>}
      </div>
      {image && <img className="script-generated-image" src={api.scriptImage(image.id)} alt={prompt} />}
    </details>
    {reference.image && <details className="card" open>
      <summary>Approved visual reference</summary>
      <img className="script-generated-image" src={api.scriptImage(reference.image)} alt="Approved story reference" />
      <label>Details to carry into new images
        <textarea rows={3} maxLength={350} value={reference.reference} disabled={!!busy || generating}
          onChange={(event) => setReference({ reference: event.target.value })} />
      </label>
      <div className="row-btns"><button type="button" className="btn ghost" disabled={!!busy || generating}
        onClick={() => setReference({ image: null, reference: '' })}>Clear reference</button></div>
    </details>}
    <div className="form">
      <div className="row-btns">
        <label>Storyboard scenes <select value={count} disabled={!!busy || generating} onChange={(event) => setCount(Number(event.target.value))}>
          {[1, 2, 3, 4, 5, 6].map((number) => <option key={number}>{number}</option>)}
        </select></label>
        <button type="button" className="btn" disabled={!!busy || generating || !script.trim()} onClick={() => {
          if (!scenes.some((scene) => scene.image_id) || window.confirm('Replace the current storyboard?')) {
            run('storyboard', async () => {
              setScenes((await api.scriptStoryboard(script, count)).scenes)
              onSelect(0)
            })
          }
        }}>{busy === 'storyboard' ? 'Planning scenes…' : 'Plan images from script'}</button>
      </div>
      {scenes.length > 0 && <>
        <div className="scene-strip" role="group" aria-label="Storyboard scenes">
          {scenes.map((scene, index) => <button key={scene.line} type="button" className="btn ghost"
            aria-pressed={index === selected} onClick={() => onSelect(index)}>
            {scene.image_id && <img src={api.scriptImage(scene.image_id)} alt="" />}
            <span>Scene {index + 1}</span><small>{({ queued: 'Queued', running: 'Generating', failed: 'Failed' })[sceneStatus(index)?.status]
              ?? (scene.asset_id ? 'Video ready' : scene.image_id ? 'Ready' : 'Needs image')}</small>
          </button>)}
        </div>
        {scenes[selected] && <div className="form">
          <label>Scene {selected + 1} prompt
            <textarea rows={4} value={scenes[selected].prompt} maxLength={600} disabled={!!busy || generating}
              onChange={(event) => {
                const value = event.target.value
                setScenes((items) => items.map((item, index) =>
                  index === selected ? { ...item, prompt: value, image_id: null, asset_id: null } : item))
              }} />
          </label>
          {scenes[selected].text && <details>
            <summary>Revise opening narration line</summary>
            <label>Revised line
              <textarea rows={2} maxLength={240}
                value={lineEdit?.index === selected ? lineEdit.text : scenes[selected].text}
                disabled={!!busy || generating}
                onChange={(event) => setLineEdit({ index: selected, text: event.target.value })} />
            </label>
            <button type="button" className="btn ghost"
              disabled={!!busy || generating || lineEdit?.index !== selected || !lineEdit?.text.trim()
                || lineEdit.text.trim() === scenes[selected].text}
              onClick={() => onRevise(selected, lineEdit.text)}>Save line revision</button>
          </details>}
          {selected > 0 && narrationLines?.length ? <label>Start at narration line
            <select value={scenes[selected].line} disabled={!!busy || generating} onChange={(event) => {
              const line = Number(event.target.value)
              setScenes((items) => items.map((item, index) =>
                index === selected ? { ...item, line, text: narrationLines[line].text } : item))
            }}>
              {narrationLines.map((line, index) => index > scenes[selected - 1].line
                && index < (scenes[selected + 1]?.line ?? narrationLines.length)
                && <option key={index} value={index}>{index + 1} · {line.text}</option>)}
            </select>
          </label> : <span className="muted small">Starts at line {scenes[selected].line + 1} · {scenes[selected].text}</span>}
          {assets.length > 0 && <label>Replace this scene with local footage
            <select value={scenes[selected].asset_id ?? ''} disabled={!!busy || generating}
              onChange={(event) => {
                const asset = event.target.value || null
                setScenes((items) => items.map((item, index) =>
                  index === selected ? { ...item, asset_id: asset, image_id: asset ? null : item.image_id } : item))
              }}>
              <option value="">Choose footage</option>
              {assets.filter((asset) => !scenes.some((item, index) => index !== selected && item.asset_id === asset.id))
                .map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
            </select>
          </label>}
          {scenes[selected].asset_id && <button type="button" className="btn ghost" disabled={!!busy || generating}
            onClick={() => setScenes((items) => items.map((item, index) =>
              index === selected ? { ...item, asset_id: null } : item))}>Remove matched video</button>}
          {scenes[selected].image_id && <div className="row-btns">
            <label>Scene motion <select value={scenes[selected].motion ?? ''} disabled={!!busy || generating}
              onChange={(event) => {
                const preset = event.target.value || null
                setScenes((items) => items.map((item, index) =>
                  index === selected ? { ...item, motion: preset } : item))
              }}>
              <option value="">Use default</option>
              <option value="still">Still</option>
              <option value="pan-zoom">Pan and zoom</option>
              <option value="push">Slow push</option>
              <option value="pan">Side pan</option>
            </select></label>
            <button type="button" className="btn ghost" disabled={!!busy || generating}
              onClick={() => run('reference', async () => {
                const id = scenes[selected].image_id
                const description = await api.describeVisualReference(id)
                setReference({ image: id, reference: description.reference })
              })}>{busy === 'reference' ? 'Describing reference…' : 'Use as visual reference'}</button>
          </div>}
          {sceneStatus(selected)?.error && <p className="err small" role="alert">{sceneStatus(selected).error}</p>}
          {!scenes[selected].asset_id && <button type="button" className="btn ghost"
            disabled={!!busy || generating || !setup?.available || !scenes[selected].prompt.trim()}
            onClick={() => queueImages([selected])}>
            {scenes[selected].image_id ? 'Regenerate scene image' : 'Generate scene image'}
          </button>}
        </div>}
        <div className="row-btns">
          {unqueued.length > 0 && <button type="button" className="btn"
            disabled={!!busy || generating || !setup?.available || unqueued.some((i) => !scenes[i].prompt.trim())}
            onClick={() => queueImages(unqueued)}>
            {busy === 'storyboard-images' ? 'Generating images…' : `Generate ${unqueued.length === scenes.length ? 'storyboard' : 'remaining'} images`}
          </button>}
          {failed.length > 0 && <button type="button" className="btn ghost"
            disabled={!!busy || generating || !setup?.available} onClick={() => queueImages(failed)}>
            Retry failed scenes
          </button>}
          {ready && <button type="button" className="primary" disabled={!!busy || generating || !canCreate}
            onClick={() => run('storyboard-footage', onUseStoryboard)}>
            {busy === 'storyboard-footage' ? 'Preparing narration and footage…' : 'Use storyboard and continue'}
          </button>}
        </div>
        {ready && <p className="muted small">Uses these images in order, timed to the narration. Choose a narration voice to create the short.</p>}
      </>}
    </div>
  </div>
}
