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

function ShortCard({ job, clip, busy, act }) {
  const o = clip.options || {}
  const setOpt = (k, v) => act(() => api.editClip(job.id, clip.index, { options: { ...o, [k]: v } })).catch(() => {})
  const src = clip.file && api.fileUrl(job.id, clip.file, clip.version)
  const poster = clip.file && api.fileUrl(job.id, clip.file.replace('.mp4', '.jpg'), clip.version)

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
        </p>

        <Seg label="Jump cuts" value={o.jumpcut ?? true} disabled={busy}
             options={[[true, 'On'], [false, 'Off']]} onChange={(v) => setOpt('jumpcut', v)} />
        <Seg label="Zoom" value={o.zoom ?? false} disabled={busy || o.jumpcut === false}
             options={[[false, 'Off'], [true, 'Punch-in']]} onChange={(v) => setOpt('zoom', v)} />
        <Seg label="Framing" value={o.reframe ?? 'auto'} disabled={busy}
             options={[['auto', 'Auto'], ['face', 'Face'], ['split', '2-up'], ['center', 'Centre']]}
             onChange={(v) => setOpt('reframe', v)} />
        <Seg label="Hook title" value={o.hook ?? true} disabled={busy || !clip.hook}
             options={[[true, 'On'], [false, 'Off']]} onChange={(v) => setOpt('hook', v)} />
        <Seg label="Captions" value={o.caption_style ?? 'karaoke'} disabled={busy}
             options={[['karaoke', 'Animated'], ['simple', 'Simple']]} onChange={(v) => setOpt('caption_style', v)} />

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
  return (
    <div>
      <div className="step-head">
        <div>
          <h2>Shorts</h2>
          <p className="muted">
            Watch each short. Toggle jump cuts, framing or caption style, then re-render just that one. To change
            timing or titles, go back to <a onClick={() => go('pick')}>Pick clips</a>.
          </p>
        </div>
      </div>
      <div className="shorts-grid">
        {clips.map((c) => (
          <ShortCard key={c.index} job={job} clip={c} busy={busy} act={act} />
        ))}
      </div>
      <div className="footer-bar">
        <span className="muted">{stale > 0 ? `${stale} short(s) changed and waiting for re-render` : 'Happy with the cuts?'}</span>
        <button className="primary" onClick={() => go('posts')}>Review posts →</button>
      </div>
    </div>
  )
}
