import { useMemo, useRef, useState } from 'react'
import { api } from '../../api.js'
import { fmt, fmtPrecise } from '../../util.js'

export default function TranscriptStep({ job, busy, act, go, transcript }) {
  const player = useRef(null)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState({ start: null, end: null })
  const [title, setTitle] = useState('')

  const lines = useMemo(() => {
    const t = transcript || []
    const needle = q.trim().toLowerCase()
    return needle ? t.filter((s) => s.text.toLowerCase().includes(needle)) : t
  }, [transcript, q])

  const clipAt = (s) => job.clips.find((c) => s.end > c.start && s.start < c.end)

  const seek = (t) => {
    const v = player.current
    if (!v) return
    v.currentTime = t
    v.play().catch(() => {})
  }

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
          <p className="muted">Click <b>⇤ start</b> and <b>end ⇥</b> on transcript lines.</p>
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
        <div className="tlist-head">
          <h3>Transcript</h3>
          <input className="search" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="legend">
          <span className="lg approved" /> approved clip <span className="lg cand" /> candidate
        </div>
        {lines.map((s, i) => {
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
