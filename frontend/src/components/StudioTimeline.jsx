import { useRef, useState } from 'react'
import { api } from '../api.js'

export default function StudioTimeline({ scenes, selected, onSelect, take, approved, music, fullCut }) {
  const audio = useRef(null)
  const [time, setTime] = useState(0)
  const start = (scene, index) => fullCut?.boundaries?.[index] ?? scene.start ?? take?.lines[scene.line]?.start ?? 0
  const duration = fullCut?.boundaries?.at(-1) ?? take?.duration
  const playhead = Math.min(fullCut?.time ?? time, duration ?? 0)
  const seek = (next) => {
    if (fullCut) {
      fullCut.seek(next)
      return
    }
    setTime(next)
    if (audio.current) audio.current.currentTime = next
  }
  return <div className="form studio-timeline">
    <div className="scene-strip" role="group" aria-label="Review scenes">
      {scenes.map((scene, i) => {
        const lines = take?.lines.slice(scene.line, scenes[i + 1]?.line) ?? []
        const state = !lines.length || !(scene.image_id || scene.asset_id) ? 'Needs update'
          : lines.every((line) => approved.includes(line.take_id)) ? 'Approved' : 'Ready'
        const end = scenes[i + 1] ? start(scenes[i + 1], i + 1) : duration
        return <button type="button" key={i} className="btn ghost" aria-pressed={selected === i}
          onClick={() => { onSelect(i); seek(start(scene, i)) }}>
          {scene.image_id && <img src={api.scriptImage(scene.image_id)} alt="" />}
          <span>Scene {i + 1}</span>
          {take && <small>{(end - start(scene, i)).toFixed(1)} s</small>}
          <small>{state}</small>
        </button>
      })}
    </div>
    {take && <div className="form">
      <label>Playhead · {playhead.toFixed(1)} s
        <input type="range" aria-label="Timeline playhead" min={0} max={duration} step="any"
          value={playhead} onChange={(event) => {
            const next = Number(event.target.value)
            const scene = scenes.findLastIndex((item, index) => start(item, index) <= next)
            if (scene >= 0) onSelect(scene)
            seek(next)
          }} />
      </label>
      <div aria-label="Timeline tracks" className="studio-tracks">
        <span>Scenes</span><div className="studio-track">
          {scenes.map((scene, i) => <button key={i} type="button" aria-label={`Seek to scene ${i + 1}`}
            aria-pressed={selected === i}
            style={{ flexGrow: Math.max(0.1, (scenes[i + 1] ? start(scenes[i + 1], i + 1) : duration) - start(scene, i)) }}
            onClick={() => { onSelect(i); seek(start(scene, i)) }}>{i + 1}</button>)}
        </div>
        <span>Narration</span><div className="studio-track">
          {take.lines.map((line, i) => <button type="button" key={i} aria-label={`Seek to line ${i + 1}`}
            style={{ flexGrow: Math.max(0.1, (take.lines[i + 1]?.start ?? take.duration) - line.start) }}
            onClick={() => {
              const scene = scenes.findLastIndex((item) => item.line <= i)
              if (scene >= 0) onSelect(scene)
              seek(line.start)
            }}>{i + 1}</button>)}
        </div>
        {music && <><span>Music</span><div className="studio-track music-track" title={music}>{music}</div></>}
      </div>
      {!fullCut && <audio ref={audio} aria-label="Timeline narration" controls preload="metadata"
        src={api.scriptNarrationAudio(take.id)} onTimeUpdate={(event) => {
          const next = event.currentTarget.currentTime
          setTime(next)
          const scene = scenes.findLastIndex((item, index) => start(item, index) <= next)
          if (!event.currentTarget.paused && scene >= 0 && scene !== selected) onSelect(scene)
        }} />}
    </div>}
  </div>
}
