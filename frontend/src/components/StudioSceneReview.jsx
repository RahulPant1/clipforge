import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import TakeReview from './TakeReview.jsx'

function TakeEditor({ clip, index, take, approved, busy, onApprove, onRevise, onRetake, onRestore, target }) {
  const hear = useRef(null)
  useEffect(() => {
    if (target?.line === index) hear.current?.focus()
  }, [target, index])
  const [text, setText] = useState(clip.text)
  const [listening, setListening] = useState(null)
  const lock = approved.includes(clip.take_id)
  const changed = text.trim() !== clip.text
  const play = (id, line) => setListening({ id, line, key: Date.now() })

  return <fieldset className="card form" disabled={!!busy}>
    <legend>Line {index + 1} · {clip.start.toFixed(1)} s · {lock ? 'Approved' : 'Ready'}</legend>
    <label>Narration line <textarea rows={2} maxLength={240} value={text} disabled={lock}
      onChange={(event) => setText(event.target.value)} /></label>
    <div className="row-btns">
      <button ref={hear} type="button" className="btn ghost" onClick={() => play(take.id, index)}>Hear take</button>
      <button type="button" className="btn ghost" disabled={lock || !text.trim()}
        onClick={() => changed ? onRevise(index, text) : onRetake(index)}>
        {changed ? 'Save and regenerate line' : 'Regenerate take'}
      </button>
      <button type="button" className="btn ghost" disabled={changed || !clip.take_id}
        onClick={() => onApprove(clip.take_id)}>{lock ? 'Unlock take' : 'Approve take'}</button>
    </div>
    {listening && <audio aria-label="Selected narration take" key={listening.key} controls autoPlay
      src={api.scriptLineAudio(listening.id, listening.line)} />}
    <TakeReview issues={clip.issues} heard={clip.heard} revision={take.id} />
    {!!clip.history?.length && <details>
      <summary>Take history · {clip.history.length}</summary>
      <ol>{clip.history.map((entry, i) => <li key={entry.take_id}>
        <div className="row-btns">
          <span>Take {i + 1} · {entry.text}</span>
          <button type="button" className="btn sm ghost" onClick={() => play(entry.preview_id, entry.line)}>Listen</button>
          <button type="button" className="btn sm ghost" disabled={lock || changed || entry.text !== clip.text}
            onClick={() => onRestore(index, entry)}>Restore</button>
        </div>
      </li>)}</ol>
    </details>}
  </fieldset>
}

export default function StudioSceneReview({ scene, index, nextScene, take, busy, approved, onApprove,
  onRevise, onRetake, onRestore, onPrepare, onReplace, assets, voice, target, onPreviewScene, canPreviewScene }) {
  if (!scene) return null
  const lines = (take?.lines ?? []).slice(scene.line, nextScene?.line)
  const end = take?.lines[nextScene?.line]?.start ?? take?.duration
  const start = lines[0]?.start
  return <section className="card form" aria-label="Selected scene controls">
    <h3>Review scene {index + 1}</h3>
    {start != null && end != null && <p className="muted small">{start.toFixed(1)}–{end.toFixed(1)} s · {voice}</p>}
    <div><button type="button" className="btn" disabled={!!busy || !canPreviewScene} onClick={onPreviewScene}>
      {busy === 'scene-cut' ? 'Preparing scene preview…' : 'Preview this scene'}
    </button></div>
    {onReplace && <label>Scene footage <select value={scene.asset_id ?? ''} disabled={!!busy}
      onChange={(event) => onReplace(event.target.value)}>
      <option value="" disabled={!scene.image_id}>{scene.image_id ? 'Current storyboard image' : 'Choose footage'}</option>
      {assets.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
    </select></label>}
    {!take && <button type="button" className="btn" disabled={!!busy} onClick={onPrepare}>Prepare narration</button>}
    {lines.map((line, i) => <TakeEditor key={line.take_id ?? line.text + i} clip={line} index={scene.line + i}
      take={take} approved={approved} busy={busy} onApprove={onApprove} onRevise={onRevise} onRetake={onRetake}
      onRestore={onRestore} target={target} />)}
  </section>
}
