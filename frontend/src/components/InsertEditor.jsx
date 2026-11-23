import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { fmtPrecise, statText } from '../util.js'

const POSITIONS = [['auto', 'Auto'], ['upper', 'Top'], ['lower', 'Low']]
const NUDGE = 0.2

// The card as it animates in the short: counts up (ease-out, 0.8 s) — replays on click
function StatPreview({ ins, accent }) {
  const [v, setV] = useState(ins.value)
  const raf = useRef(0)
  const play = () => {
    cancelAnimationFrame(raf.current)
    const t0 = performance.now()
    const tick = (now) => {
      const p = Math.min((now - t0) / 800, 1)
      setV(ins.value * (1 - (1 - p) ** 3))
      if (p < 1) raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
  }
  useEffect(() => { play(); return () => cancelAnimationFrame(raf.current) }, [ins.value, ins.decimals]) // eslint-disable-line
  return (
    <button className="stat-preview" style={{ '--stat': accent }} onClick={play} title="Replay the count-up">
      <b>{statText(ins, v)}</b>
      {ins.label && <small>{ins.label}</small>}
    </button>
  )
}

function Row({ ins, clip, busy, save, remove, accent }) {
  const [label, setLabel] = useState(ins.label)
  const [num, setNum] = useState({ prefix: ins.prefix, value: String(ins.value), suffix: ins.suffix })
  useEffect(() => setLabel(ins.label), [ins.label])
  useEffect(() => setNum({ prefix: ins.prefix, value: String(ins.value), suffix: ins.suffix }),
    [ins.prefix, ins.value, ins.suffix])
  const commitNum = () => {
    const value = Number(num.value)
    if (!Number.isFinite(value)) return setNum({ ...num, value: String(ins.value) })
    const decimals = Math.min(3, (num.value.split('.')[1] || '').length)
    if (value !== ins.value || num.prefix !== ins.prefix || num.suffix !== ins.suffix || decimals !== ins.decimals)
      save({ prefix: num.prefix.slice(0, 4), value, decimals, suffix: num.suffix.slice(0, 12) })
  }
  const at = ins.t - clip.start
  const inside = ins.t >= clip.start && ins.t < clip.end
  return (
    <li className={`stat-row ${ins.approved ? 'on' : ''}`}>
      <StatPreview ins={ins} accent={accent} />
      <div className="stat-ctl">
        <div className="stat-line">
          <span className="muted small" title="When it shows: seconds into the clip, before jump cuts">
            {ins.custom ? '✋ yours' : `“${ins.spoken}”`} at {fmtPrecise(at)}{!inside && ' · outside the clip now'}
          </span>
          <span className="nudge">
            <button className="btn sm ghost" disabled={busy || at - NUDGE < 0}
                    onClick={() => save({ t: +(ins.t - NUDGE).toFixed(3) })} title="Earlier" aria-label="Earlier">−</button>
            <button className="btn sm ghost" disabled={busy || ins.t + NUDGE >= clip.end}
                    onClick={() => save({ t: +(ins.t + NUDGE).toFixed(3) })} title="Later" aria-label="Later">+</button>
          </span>
        </div>
        {ins.custom && (
          <div className="stat-num">
            <input value={num.prefix} maxLength={4} placeholder="₹ $" disabled={busy} aria-label="before the number"
                   onChange={(e) => setNum({ ...num, prefix: e.target.value })} onBlur={commitNum} />
            <input value={num.value} inputMode="decimal" disabled={busy} aria-label="number"
                   onChange={(e) => setNum({ ...num, value: e.target.value })} onBlur={commitNum} />
            <input value={num.suffix} maxLength={12} placeholder="% x lakh" disabled={busy} aria-label="after the number"
                   onChange={(e) => setNum({ ...num, suffix: e.target.value })} onBlur={commitNum} />
          </div>
        )}
        <input value={label} maxLength={40} disabled={busy} placeholder="Label under the number"
               onChange={(e) => setLabel(e.target.value)}
               onBlur={() => label.trim() !== ins.label && save({ label: label.trim() })} />
        <div className="stat-line">
          <div className="seg sm">
            {POSITIONS.map(([v, l]) => (
              <button key={v} disabled={busy} className={ins.position === v ? 'on' : ''}
                      title={v === 'auto' ? 'Above the captions (top when two speakers are stacked)' : undefined}
                      onClick={() => save({ position: v })}>{l}</button>
            ))}
          </div>
          <div className="seg sm">
            <button disabled={busy} className={ins.approved ? 'on' : ''} onClick={() => save({ approved: true })}>
              Show
            </button>
            <button disabled={busy} className={!ins.approved ? 'on' : ''} onClick={() => save({ approved: false })}>
              {ins.custom ? 'Hide' : 'Suggested'}
            </button>
          </div>
          <button className="x" disabled={busy} onClick={remove} title="Remove this card" aria-label="Remove this card">×</button>
        </div>
      </div>
    </li>
  )
}

// Stat cards: a spoken number counts up on a card when it's said. Gemma suggests (after the posts are written, or
// "Find stats"); only cards set to Show are burned in, so changing one asks for a re-render.
export default function InsertEditor({ job, clip, busy, act, accent }) {
  const [asking, setAsking] = useState(false)
  const list = clip.inserts || []
  const put = (next) => act(() => api.editClip(job.id, clip.index, { inserts: next })).catch(() => {})
  const find = async () => {
    setAsking(true)
    try {
      await act(() => api.suggestInserts(job.id, clip.index))
    } catch {
      /* shown by act() */
    } finally {
      setAsking(false)
    }
  }
  const add = () => put([...list, {
    t: +(clip.start + Math.min(1, (clip.end - clip.start) / 2)).toFixed(3), value: 10, decimals: 0, prefix: '',
    suffix: '%', label: '', custom: true, approved: false,
  }].sort((a, b) => a.t - b.t))
  const shown = list.filter((i) => i.approved).length
  return (
    <div className="opt-col stats">
      <span className="muted">
        Stat cards {shown > 0 ? <b>· {shown} shown</b> : list.length ? '· suggestions, not shown yet' : ''}
        <span className="small"> · a number from the clip counts up on screen when it's said</span>
      </span>
      {list.length > 0 && (
        <ul className="stat-list">
          {list.map((ins) => (
            <Row key={ins.id} ins={ins} clip={clip} busy={busy} accent={accent}
                 save={(ch) => put(list.map((x) => (x.id === ins.id ? { ...x, ...ch } : x)))}
                 remove={() => put(list.filter((x) => x.id !== ins.id))} />
          ))}
        </ul>
      )}
      {clip.inserts_checked && !list.length && !asking && (
        <span className="muted small">No number in this clip worth a card.</span>
      )}
      <div className="row-btns">
        <button className="btn sm ghost" disabled={busy || asking} onClick={find}>
          {asking ? '✨ Gemma is reading the numbers…' : list.some((i) => !i.custom) ? '✨ Find again' : '✨ Find stats'}
        </button>
        <button className="btn sm ghost" disabled={busy || list.length >= 8} onClick={add}>+ Your own</button>
      </div>
      {shown > 2 && <span className="muted small">At most 2 cards show per short, at least 3 s apart (INSERTS_MAX).</span>}
    </div>
  )
}
