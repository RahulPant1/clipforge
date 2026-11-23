import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api.js'
import { fmt, fmtPrecise } from '../../util.js'

// Hindi transcripts: Devanagari ⇄ Hinglish (Latin letters). Captions, .srt, posts and the editor all follow.
function ScriptSwitch({ job, busy, act, reloadTranscript }) {
  const [state, setState] = useState(null)
  useEffect(() => { api.scriptState(job.id).then((r) => setState(r.state)).catch(() => setState('')) }, [job.id, job.hinglish])
  if (!state) return null
  const toLatin = state === 'devanagari'
  const flip = () => {
    if (!toLatin && !window.confirm('Back to Devanagari? Word fixes you made in Hinglish will be lost.')) return
    act(() => api.setScript(job.id, toLatin)).then(reloadTranscript).catch(() => {})
  }
  return (
    <div className="scriptswitch">
      <span>{toLatin ? <>Hindi in <b>Devanagari</b> (as Whisper wrote it)</> : <>Hindi shown in <b>Hinglish</b> (Latin letters)</>}</span>
      <button className="btn" disabled={busy} onClick={flip}>{toLatin ? 'Switch to Hinglish' : 'Back to Devanagari'}</button>
      {job.clips.some((c) => c.file) && <span className="muted small">rendered shorts get marked to re-render</span>}
    </div>
  )
}

export default function TranscriptStep({ job, busy, act, go, transcript, reloadTranscript }) {
  const player = useRef(null)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState({ start: null, end: null })
  const [title, setTitle] = useState('')
  const [scenes, setScenes] = useState(null) // { mode, reason, scenes: [...] } from scenes.json
  const [showScenes, setShowScenes] = useState(true)
  useEffect(() => {
    api.scenes(job.id).then(setScenes).catch(() => setScenes(null))
  }, [job.id, job.status])

  // transcript lines + what's on screen (scene index), in time order; search covers both
  const lines = useMemo(() => {
    const t = (transcript || []).map((s) => ({ ...s, kind: 'speech' }))
    const shots = showScenes ? (scenes?.scenes || []).map((x) => ({ ...x, text: x.description, scene: true })) : []
    const all = [...t, ...shots].sort((a, b) => a.start - b.start || (a.scene ? -1 : 1))
    const needle = q.trim().toLowerCase()
    return needle ? all.filter((s) => s.text.toLowerCase().includes(needle)) : all
  }, [transcript, scenes, showScenes, q])
  const sceneCount = scenes?.scenes?.length || 0

  const clipAt = (s) => job.clips.find((c) => s.end > c.start && s.start < c.end)

  const seek = (t) => {
    const v = player.current
    if (!v) return
    v.currentTime = t
    v.play().catch(() => {})
  }

  const playhead = () => Math.round((player.current?.currentTime ?? 0) * 100) / 100
  const noSpeech = !(transcript || []).length || job.clips.some((c) => c.visual)

  const dur = sel.start !== null && sel.end !== null ? sel.end - sel.start : null
  const valid = dur !== null && dur >= 3 && dur <= 180

  const add = async () => {
    await act(() => api.addClip(job.id, { start: sel.start, end: sel.end, title: title || 'My clip' }))
    setSel({ start: null, end: null })
    setTitle('')
  }

  if (transcript === null) return <div className="card muted">Loading transcript…</div>

  return (
    <div className="transcript-step">
      <div className="tplayer">
        <video ref={player} src={api.fileUrl(job.id, 'source.mp4')} controls preload="metadata" />
        <div className="card addclip">
          <h3>Make your own clip</h3>
          <p className="muted">
            {noSpeech
              ? 'Hardly any speech here: play the video and mark start and end at the playhead.'
              : <>Click <b>⇤ start</b> and <b>end ⇥</b> on transcript lines, or mark them at the playhead.</>}
          </p>
          <div className="selrow">
            <button className="btn" onClick={() => setSel((x) => ({ ...x, start: playhead() }))}>⇤ start at playhead</button>
            <button className="btn" onClick={() => setSel((x) => ({ ...x, end: playhead() }))}>end ⇥ at playhead</button>
          </div>
          <div className="selrow">
            <span>Start <b>{sel.start !== null ? fmtPrecise(sel.start) : '—'}</b></span>
            <span>End <b>{sel.end !== null ? fmtPrecise(sel.end) : '—'}</b></span>
            {dur !== null && <span className={dur > 59 ? 'warn' : ''}>{dur.toFixed(1)}s</span>}
          </div>
          <input placeholder="Title (optional)" value={title} onChange={(e) => setTitle(e.target.value)} />
          <button className="primary" disabled={!valid || busy} onClick={add}>
            + Add clip
          </button>
          {dur !== null && !valid && <p className="err">Clips must be 3–180 s (end after start).</p>}
          {dur > 59 && <p className="hint warn">Over 59 s: Shorts with any copyright claim get blocked.</p>}
          <button className="btn ghost" onClick={() => go('pick')}>Go to Pick clips →</button>
        </div>
      </div>

      <div className="card tlist">
        <ScriptSwitch job={job} busy={busy} act={act} reloadTranscript={reloadTranscript} />
        <div className="tlist-head">
          <h3>Transcript</h3>
          <input className="search" placeholder={sceneCount ? 'Search what is said or shown…' : 'Search…'} value={q}
                 onChange={(e) => setQ(e.target.value)} />
        </div>
        {sceneCount > 0 && (
          <label className="scene-toggle muted small" title={`Described by ${scenes.model || 'Gemma'} from one frame per scene`}>
            <input type="checkbox" checked={showScenes} onChange={(e) => setShowScenes(e.target.checked)} />
            🎬 Show what's on screen ({sceneCount} scenes) — Gemma reads them when it picks clips (all but plain
            talking-head shots)
          </label>
        )}
        {scenes?.mode === 'skipped' && (
          <p className="muted small">🎬 No scene descriptions: {scenes.reason}.</p>
        )}
        <div className="legend">
          <span className="lg approved" /> approved clip <span className="lg cand" /> candidate
        </div>
        {lines.map((s, i) => {
          if (s.scene) {
            return (
              <div key={`sc${i}`} className="tline scene">
                <button className="ts" onClick={() => seek(s.t ?? s.start)} title="Play this scene">{fmt(s.start)}</button>
                <span className="ttext"><span className="skind">🎬 {s.kind.replace('_', ' ')}</span> {s.text}</span>
                <span className="tact">
                  <button onClick={() => setSel((x) => ({ ...x, start: s.start }))} title="Start clip at this scene">⇤ start</button>
                </span>
              </div>
            )
          }
          const c = clipAt(s)
          const inSel = sel.start !== null && s.start >= sel.start && s.end <= (sel.end ?? sel.start)
          return (
            <div key={i} className={`tline ${c ? (c.approved ? 'approved' : 'cand') : ''} ${inSel ? 'insel' : ''}`}>
              <button className="ts" onClick={() => seek(s.start)} title="Play from here">
                {fmt(s.start)}
              </button>
              <span className="ttext">
                {c && <span className="cbadge">#{c.index}</span>}
                {s.text}
              </span>
              <span className="tact">
                <button onClick={() => setSel((x) => ({ ...x, start: s.start }))} title="Start clip here">⇤ start</button>
                <button onClick={() => setSel((x) => ({ ...x, end: s.end }))} title="End clip here">end ⇥</button>
              </span>
            </div>
          )
        })}
        {!lines.length && <p className="muted">No lines match.</p>}
      </div>
    </div>
  )
}
