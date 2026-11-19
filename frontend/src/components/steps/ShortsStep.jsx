import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { fmt } from '../../util.js'

function Seg({ label, value, options, onChange, disabled }) {
  return (
    <div className="opt">
      <span className="muted">{label}</span>
      <div className="seg sm">
        {options.map(([v, l]) => (
          <button key={String(v)} disabled={disabled} className={value === v ? 'on' : ''} onClick={() => onChange(v)}>
            {l}
          </button>
        ))}
      </div>
    </div>
  )
}

// ASS &HAABBGGRR (BGR, AA 00 = opaque) -> CSS rgba
function assColor(c) {
  const h = (c || '&H00FFFFFF').replace('&H', '').padStart(8, '0')
  const [a, b, g, r] = [0, 2, 4, 6].map((i) => parseInt(h.slice(i, i + 2), 16))
  return `rgba(${r},${g},${b},${((255 - a) / 255).toFixed(2)})`
}

function LookChip({ p, on, disabled, onPick }) {
  const tx = (w) => (p.uppercase ? w.toUpperCase() : w)
  const ol = assColor(p.outline_color)
  const w = Math.max(1, Math.min(2, p.outline / 4))
  const sample = {
    fontFamily: `"${p.font}", sans-serif`,
    fontWeight: p.bold ? 700 : 400,
    color: assColor(p.text),
    ...(p.box
      ? { background: ol }
      : { textShadow: `${w}px ${w}px 0 ${ol}, -${w}px ${w}px 0 ${ol}, ${w}px -${w}px 0 ${ol}, -${w}px -${w}px 0 ${ol}`
          + (p.blur ? `, 0 0 ${p.blur * 2}px ${ol}` : '') }),
  }
  return (
    <button className={`look ${on ? 'on' : ''}`} disabled={disabled} onClick={onPick} title={`${p.description} · ${p.font}`}>
      <span className="sample" style={sample}>
        {tx('Make')} <span style={{ color: assColor(p.active) }}>{tx('it')}</span>{' '}
        <span style={{ color: assColor(p.keyword) }}>{tx('pop')}</span>
      </span>
      <small>{p.label}</small>
    </button>
  )
}

function ShortCard({ job, clip, busy, act, presets }) {
  const o = clip.options || {}
  const setOpt = (k, v) => act(() => api.editClip(job.id, clip.index, { options: { ...o, [k]: v } })).catch(() => {})
  const src = clip.file && api.fileUrl(job.id, clip.file, clip.version)
  const poster = clip.file && api.fileUrl(job.id, clip.file.replace('.mp4', '.jpg'), clip.version)
  const noCaptions = (o.caption_style ?? presets?.style) === 'none'

  return (
    <div className={`short ${clip.stale ? 'is-stale' : ''}`}>
      {src ? (
        <video key={src} src={src} poster={poster} controls preload="none" />
      ) : (
        <div className="placeholder">Not rendered yet</div>
      )}
      <div className="clip-body">
        <div className="clip-head">
          <h4>#{clip.index} {clip.title}</h4>
          <span className="score">{clip.score}/10</span>
        </div>
        <p className="meta">
          {fmt(clip.start)}→{fmt(clip.end)} · {(clip.out_duration ?? clip.end - clip.start).toFixed(0)}s
          {clip.cut_seconds > 0.5 && <span className="cut"> · ✂ {clip.cut_seconds.toFixed(1)}s</span>}
          {clip.reframe === 'face' && <span className="cut"> · 🎯 face</span>}
          {clip.reframe === 'split' && <span className="cut"> · 👥 2 speakers</span>}
          {clip.reframe === 'speaker' && <span className="cut"> · 🎙 follows talker</span>}
        </p>

        <Seg label="Jump cuts" value={o.jumpcut ?? true} disabled={busy}
             options={[[true, 'On'], [false, 'Off']]} onChange={(v) => setOpt('jumpcut', v)} />
        <Seg label="Zoom" value={o.zoom ?? false} disabled={busy || o.jumpcut === false}
             options={[[false, 'Off'], [true, 'Punch-in']]} onChange={(v) => setOpt('zoom', v)} />
        <Seg label="Framing" value={o.reframe ?? 'auto'} disabled={busy}
             options={[['auto', 'Auto'], ['face', 'Face'], ['split', '2-up'], ['speaker', 'Talker'], ['center', 'Centre']]}
             onChange={(v) => setOpt('reframe', v)} />
        <Seg label="Hook title" value={o.hook ?? true} disabled={busy || !clip.hook}
             options={[[true, 'On'], [false, 'Off']]} onChange={(v) => setOpt('hook', v)} />
        <Seg label="Captions" value={o.caption_style ?? presets?.style ?? 'karaoke'} disabled={busy}
             options={[['karaoke', 'Animated'], ['simple', 'Simple'], ['none', 'Off']]} onChange={(v) => setOpt('caption_style', v)} />
        {presets && (
          <>
            <div className="opt-col">
              <span className="muted">Caption look</span>
              <div className="looks">
                {presets.presets.map((p) => (
                  <LookChip key={p.id} p={p} on={(o.caption_preset ?? presets.default) === p.id}
                            disabled={busy || noCaptions} onPick={() => setOpt('caption_preset', p.id)} />
                ))}
              </div>
            </div>
            <Seg label={clip.reframe === 'split' ? 'Position (2-up: on the seam)' : 'Position'}
                 value={o.caption_position ?? presets.position} disabled={busy || noCaptions || clip.reframe === 'split'}
                 options={[['bottom', 'Bottom'], ['middle', 'Middle']]}
                 onChange={(v) => setOpt('caption_position', v)} />
          </>
        )}

        {clip.render_error && <p className="err">Render failed: {clip.render_error}</p>}
        <div className="actions">
          {(clip.stale || !clip.file || clip.render_error) && (
            <button className="btn primary-link" disabled={busy}
                    onClick={() => act(() => api.rerender(job.id, clip.index)).catch(() => {})}>
              ↻ {clip.file ? 'Re-render' : 'Render'}
            </button>
          )}
          {src && (
            <>
              <a className="btn" href={src} download={`short_${clip.index}.mp4`}>Download</a>
              <a className="btn ghost" href={api.fileUrl(job.id, `short_${clip.index}.srt`)} download>.srt</a>
            </>
          )}
        </div>
        {clip.stale && <p className="hint warn">Changed since last render, so re-render to see it.</p>}
      </div>
    </div>
  )
}

export default function ShortsStep({ job, busy, act, go }) {
  const clips = job.clips.filter((c) => c.approved)
  const stale = clips.filter((c) => c.stale).length
  const [presets, setPresets] = useState(null)
  useEffect(() => {
    api.captionPresets().then(setPresets).catch(() => setPresets(null))
  }, [])
  return (
    <div>
      <div className="step-head">
        <div>
          <h2>Shorts</h2>
          <p className="muted">
            Watch each short. Toggle jump cuts, framing or the caption look, then re-render just that one. To change
            timing or titles, go back to <a onClick={() => go('pick')}>Pick clips</a>.
          </p>
        </div>
      </div>
      <div className="shorts-grid">
        {clips.map((c) => (
          <ShortCard key={c.index} job={job} clip={c} busy={busy} act={act} presets={presets} />
        ))}
      </div>
      <div className="footer-bar">
        <span className="muted">{stale > 0 ? `${stale} short(s) changed and waiting for re-render` : 'Happy with the cuts?'}</span>
        <button className="primary" onClick={() => go('posts')}>Review posts →</button>
      </div>
    </div>
  )
}
