import { useMemo, useRef } from 'react'
import { fmt, moveEdge, snapTime, waveBars } from '../util.js'

const BARS = 240
const SNAP_PX = 8 // handles snap to sentence edges / the playhead within this many pixels (hold Alt: no snap)

/**
 * Waveform strip around one clip. Drag an edge to trim, drag the clip to move it, click/drag elsewhere to seek.
 * `win` is fixed while you drag (it's computed from the saved clip), so the strip never slides under the pointer.
 */
export default function Timeline({
  start, end, win, total, wave, edges, others, playhead, active, disabled,
  onDrag, onCommit, onSeek, onActive,
}) {
  const [lo, hi] = win
  const span = hi - lo
  const ref = useRef(null)
  const drag = useRef(null)
  const pct = (t) => `${((Math.min(Math.max(t, lo), hi) - lo) / span) * 100}%`
  const bars = useMemo(() => waveBars(wave?.levels, wave?.rate, lo, hi, BARS), [wave, lo, hi])
  const ticks = useMemo(() => edges.filter((t) => t > lo && t < hi), [edges, lo, hi])

  const tAt = (x) => {
    const r = ref.current.getBoundingClientRect()
    return lo + Math.min(Math.max((x - r.left) / r.width, 0), 1) * span
  }

  const down = (kind) => (e) => {
    if (disabled || e.button > 0) return
    e.stopPropagation()
    e.preventDefault()
    ref.current.setPointerCapture(e.pointerId)
    const t = tAt(e.clientX)
    drag.current = { kind, t0: t, s0: start, e0: end, cur: null }
    if (kind === 'start' || kind === 'end') onActive(kind)
    if (kind === 'seek') onSeek(t)
  }

  const move = (e) => {
    const d = drag.current
    if (!d) return
    const t = tAt(e.clientX)
    if (d.kind === 'seek') return onSeek(t)
    const tol = e.altKey ? 0 : (SNAP_PX / ref.current.clientWidth) * span
    const snapEdges = playhead != null ? [...ticks, playhead] : ticks
    d.cur = d.kind === 'move'
      ? moveEdge('move', t - d.t0, d.s0, d.e0, total)
      : moveEdge(d.kind, snapTime(t, snapEdges, tol), d.s0, d.e0, total)
    onDrag(...d.cur)
  }

  const up = () => {
    const d = drag.current
    drag.current = null
    if (d?.cur) onCommit(...d.cur)
  }

  const path = bars
    .map((h, i) => {
      const v = Math.max(h, 2)
      return `M${i} ${50 - v / 2}h0.7v${v}h-0.7z`
    })
    .join('')

  return (
    <div className="tl-wrap">
      <div
        ref={ref}
        className={`tl ${disabled ? 'off' : ''}`}
        onPointerDown={down('seek')}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={up}
      >
        <svg className="tl-wave" viewBox={`0 0 ${BARS} 100`} preserveAspectRatio="none" aria-hidden="true">
          <path d={path} />
        </svg>
        {ticks.map((t, i) => <i key={i} className="tl-tick" style={{ left: pct(t) }} />)}
        {others
          .filter(([s, e]) => e > lo && s < hi)
          .map(([s, e], i) => (
            <div key={i} className="tl-other" style={{ left: pct(s), width: `calc(${pct(e)} - ${pct(s)})` }} />
          ))}
        <div
          className="tl-sel"
          style={{ left: pct(start), width: `calc(${pct(end)} - ${pct(start)})` }}
          onPointerDown={down('move')}
          title="Drag to move the clip"
        >
          <span className={`tl-h start ${active === 'start' ? 'on' : ''}`} onPointerDown={down('start')}
                title="Drag to set the start" />
          <span className={`tl-h end ${active === 'end' ? 'on' : ''}`} onPointerDown={down('end')}
                title="Drag to set the end" />
        </div>
        {playhead != null && playhead >= lo && playhead <= hi && (
          <div className="tl-play" style={{ left: pct(playhead) }} />
        )}
      </div>
      <div className="tl-scale muted">
        <span>{fmt(lo)}</span>
        <span>{fmt(hi)}</span>
      </div>
    </div>
  )
}
