import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { cropAt, fmtPrecise, seedKeys, trackedAt, upsertKey } from '../util.js'

// Drag the 9:16 crop over the source video where face tracking picked the wrong spot. Each drag sets the crop
// from that moment on (a key); a key can glide from the previous one instead of cutting. Saved as
// options.reframe = "manual" + options.crop_keys (SOURCE seconds) → re-render. Feed versions use the same centre.
export default function CropEditor({ job, clip, busy, act, o, onClose }) {
  const [info, setInfo] = useState(null)
  const [err, setErr] = useState('')
  const [keys, setKeys] = useState([])
  const [t, setT] = useState(clip.start)
  const [playing, setPlaying] = useState(false)
  const [drag, setDrag] = useState(null) // {x} while dragging
  const video = useRef(null)
  const box = useRef(null)
  const grab = useRef(0)

  useEffect(() => {
    api.crop(job.id, clip.index).then((r) => {
      setInfo(r)
      setKeys(r.keys.length ? r.keys : seedKeys(r.tracked, clip.start))
    }).catch((e) => setErr(e.message))
  }, [job.id, clip.index, clip.start])

  useEffect(() => { // smooth playhead while playing; loop inside the clip
    if (!playing) return undefined
    let id
    const tick = () => {
      const v = video.current
      if (v) {
        if (v.currentTime >= clip.end) { v.currentTime = clip.start }
        setT(v.currentTime)
      }
      id = requestAnimationFrame(tick)
    }
    id = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(id)
  }, [playing, clip.start, clip.end])

  useEffect(() => {
    const esc = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])

  const seek = (s) => {
    const v = Math.min(Math.max(s, clip.start), clip.end)
    setT(v)
    if (video.current) video.current.currentTime = v
  }
  const toggle = () => {
    const v = video.current
    if (!v) return
    if (v.paused) { v.play(); setPlaying(true) } else { v.pause(); setPlaying(false) }
  }

  if (err) return <Shell onClose={onClose}><div className="banner error">{err}</div></Shell>
  if (!info) return <Shell onClose={onClose}><p className="muted">Loading…</p></Shell>

  const frac = info.crop['9:16']
  const manual = keys.length > 0
  const cx = drag ? drag.x : manual ? cropAt(keys, t) : trackedAt(info.tracked, t)
  const left = (c, f) => Math.min(Math.max(c - f / 2, 0), 1 - f) * 100
  const saved = JSON.stringify(info.keys)
  const dirty = JSON.stringify(keys) !== saved || (manual && o.reframe !== 'manual')
  const here = keys.find((k) => Math.abs(k.t - t) < 0.15)
  const feeds = (o.aspects ?? []).filter((a) => info.crop[a] && info.crop[a] < 1)
  const span = clip.end - clip.start

  const pos = (e) => {
    const r = box.current.getBoundingClientRect()
    return (e.clientX - r.left) / r.width
  }
  const down = (e) => {
    if (busy) return
    e.currentTarget.setPointerCapture(e.pointerId)
    const p = pos(e)
    grab.current = Math.abs(p - cx) < frac / 2 ? p - cx : 0 // grabbed the frame: keep the offset; else jump there
    if (playing) toggle()
    setDrag({ x: Math.min(Math.max(p - grab.current, frac / 2), 1 - frac / 2) })
  }
  const move = (e) => drag && setDrag({ x: Math.min(Math.max(pos(e) - grab.current, frac / 2), 1 - frac / 2) })
  const up = () => {
    if (!drag) return
    let ks = keys
    if (!ks.length) ks = [{ t: clip.start, x: Math.round(cx * 1000) / 1000, pan: false }] // keep what came before
    setKeys(upsertKey(ks, t, drag.x))
    setDrag(null)
  }
  const save = () => act(() => api.editClip(job.id, clip.index, {
    options: { ...o, reframe: 'manual', crop_keys: keys, follow: null },
  })).then(onClose).catch(() => {})
  const auto = () => act(() => api.editClip(job.id, clip.index, {
    options: { ...o, reframe: o.reframe === 'manual' ? null : o.reframe, crop_keys: null },
  })).then(onClose).catch(() => {})
  const keyNav = (dir) => {
    const ts = keys.map((k) => k.t).filter((k) => (dir > 0 ? k > t + 0.05 : k < t - 0.05))
    if (ts.length) seek(dir > 0 ? Math.min(...ts) : Math.max(...ts))
  }

  return (
    <Shell onClose={onClose}>
      <p className="muted small">
        {manual ? 'Your crop' : info.tracked.length ? 'Where face tracking put the crop' : 'Centre crop'} — drag the
        frame to move it. A drag sets the crop <b>from that moment on</b>; scrub to where the tracker goes wrong first.
      </p>
      <div className="crop-stage" ref={box} style={{ aspectRatio: `${info.src_w} / ${info.src_h}` }}
           onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => setDrag(null)}>
        <video ref={video} src={api.fileUrl(job.id, 'source.mp4')} preload="auto" muted={false} playsInline
               onLoadedMetadata={() => seek(t)} onPause={() => setPlaying(false)} />
        {feeds.map((a) => (
          <div key={a} className="crop-feed" title={`${a} version`}
               style={{ left: `${left(cx, info.crop[a])}%`, width: `${info.crop[a] * 100}%` }}><span>{a}</span></div>
        ))}
        <div className={`crop-rect ${drag ? 'dragging' : ''}`} style={{ left: `${left(cx, frac)}%`, width: `${frac * 100}%` }} />
      </div>

      <div className="crop-time">
        <button className="btn sm" onClick={toggle}>{playing ? '❚❚' : '▶'}</button>
        <div className="crop-track">
          <input type="range" min={clip.start} max={clip.end} step="0.04" value={t}
                 onChange={(e) => { if (playing) toggle(); seek(Number(e.target.value)) }} />
          {keys.map((k) => (
            <button key={k.t} className={`crop-key ${k.pan ? 'pan' : ''} ${here === k ? 'on' : ''}`}
                    style={{ left: `${((Math.max(k.t, clip.start) - clip.start) / span) * 100}%` }}
                    title={`${fmtPrecise(k.t)}${k.pan ? ' · glides' : ''}`} onClick={() => seek(Math.max(k.t, clip.start))} />
          ))}
        </div>
        <span className="muted small mono">{fmtPrecise(t)}</span>
      </div>

      <div className="crop-bar">
        <button className="btn sm ghost" disabled={!keys.length} onClick={() => keyNav(-1)}>◀ key</button>
        <button className="btn sm ghost" disabled={!keys.length} onClick={() => keyNav(1)}>key ▶</button>
        {here ? (
          <>
            <label className="checkline small">
              <input type="checkbox" checked={here.pan} disabled={keys[0] === here}
                     onChange={(e) => setKeys(keys.map((k) => (k === here ? { ...k, pan: e.target.checked } : k)))} />
              <span>Glide here from the previous key <span className="muted">(camera pan, not a cut)</span></span>
            </label>
            <button className="linkbtn" onClick={() => setKeys(keys.filter((k) => k !== here))}>delete key</button>
          </>
        ) : (
          <span className="muted small">{keys.length} key{keys.length === 1 ? '' : 's'}</span>
        )}
      </div>

      <div className="crop-actions">
        {(o.reframe === 'manual' || info.keys.length > 0) && (
          <button className="btn ghost" disabled={busy} onClick={auto}>Back to automatic</button>
        )}
        <span className="grow" />
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="primary" disabled={busy || !manual || !dirty} onClick={save}>Save crop</button>
      </div>
      <p className="muted small">Saving marks the short for re-render{feeds.length ? `; the ${feeds.join(' / ')} version${feeds.length > 1 ? 's' : ''} (dashed) follow the same centre` : ''}.</p>
    </Shell>
  )
}

function Shell({ children, onClose }) {
  return (
    <div className="modal-bg" onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal crop-modal" role="dialog" aria-label="Adjust crop">
        <div className="modal-head">
          <h3>✋ Adjust crop</h3>
          <button className="linkbtn" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  )
}
