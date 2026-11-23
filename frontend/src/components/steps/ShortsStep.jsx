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

// D: who the crop follows — faces found in the clip, click one (Auto = the biggest / both in 2-up and Talker)
function FollowPicker({ job, clip, busy, act, o }) {
  const [people, setPeople] = useState(null) // null = not loaded, 'loading', [] = none found
  const load = () => {
    setPeople('loading')
    api.people(job.id, clip.index).then(setPeople).catch(() => setPeople([]))
  }
  const choose = (x) =>
    act(() => api.editClip(job.id, clip.index, {
      options: { ...o, follow: x, reframe: x == null ? (o.reframe === 'face' ? null : o.reframe) : 'face' },
    })).catch(() => {})
  return (
    <div className="opt-col">
      <span className="muted">
        Follow {o.follow != null ? <b>· the person you chose</b> : '· automatic'}
      </span>
      {people === null && (
        <button className="btn sm ghost" disabled={busy} onClick={load}>👤 Choose who to follow</button>
      )}
      {people === 'loading' && <span className="muted small">Finding faces…</span>}
      {Array.isArray(people) && (
        <div className="people">
          <button className={`person auto ${o.follow == null ? 'on' : ''}`} disabled={busy} onClick={() => choose(null)}>
            Auto
          </button>
          {people.map((p) => (
            <button key={p.x} disabled={busy} onClick={() => choose(p.x)}
                    className={`person ${o.follow != null && Math.abs(o.follow - p.x) < 0.06 ? 'on' : ''}`}
                    title={`In ${Math.round(p.share * 100)}% of the clip`}>
              <img src={p.img} alt={`face at ${Math.round(p.x * 100)}% from the left`} />
            </button>
          ))}
          {!people.length && <span className="muted small">No faces found in this clip.</span>}
        </div>
      )}
    </div>
  )
}

// K: reel / Shorts cover — text + frame, made after every render, editable here
function CoverPanel({ job, clip, busy, act, defaultText }) {
  const [text, setText] = useState(clip.cover_text)
  const [frames, setFrames] = useState(null)
  useEffect(() => setText(clip.cover_text), [clip.cover_text])
  const edit = (body) => act(() => api.editCover(job.id, clip.index, body)).catch(() => {})
  const url = clip.cover && api.fileUrl(job.id, clip.cover, clip.cover_version)
  return (
    <div className="opt-col cover">
      <span className="muted">Cover <span className="small">· reel / Shorts thumbnail, text stays inside Instagram's grid crop</span></span>
      <Seg label="Style" value={clip.cover_style ?? 'cutout'} disabled={busy || !clip.file}
           options={[['cutout', 'Cutout'], ['frame', 'Frame']]} onChange={(v) => edit({ style: v })} />
      {clip.cover && (clip.cover_style ?? 'cutout') === 'cutout' && !clip.cover_cutout && (
        <span className="muted small">No clear person in this frame, so it's a frame cover — try another frame.</span>
      )}
      <div className="cover-row">
        {url ? <a href={url} target="_blank" rel="noreferrer"><img src={url} alt="cover" /></a>
             : <div className="cover-empty muted small">Made after rendering</div>}
        <div className="cover-ctl">
          <input value={text} maxLength={80} disabled={busy} placeholder={defaultText || 'Cover text'}
                 onChange={(e) => setText(e.target.value)}
                 onBlur={() => text !== clip.cover_text && edit({ text })} />
          <div className="row-btns">
            <button className="btn sm ghost" disabled={busy}
                    onClick={() => { setFrames('loading'); api.coverFrames(job.id, clip.index).then(setFrames).catch(() => setFrames(null)) }}>
              🖼 Other frame
            </button>
            {clip.cover_t != null && <button className="btn sm ghost" disabled={busy} onClick={() => edit({ auto: true })}>Best frame</button>}
            {url && <a className="btn sm ghost" href={url} download={`short_${clip.index}_cover.jpg`}>Download</a>}
          </div>
        </div>
      </div>
      {frames === 'loading' && <span className="muted small">Finding the best frames…</span>}
      {Array.isArray(frames) && (
        <div className="frames">
          {frames.map((f) => (
            <button key={f.t} disabled={busy} title={`at ${fmt(f.t)}`}
                    className={clip.cover_t != null && Math.abs(clip.cover_t - f.t) < 0.05 ? 'on' : ''}
                    onClick={() => { edit({ t: f.t }); setFrames(null) }}>
              <img src={f.img} alt={`frame at ${fmt(f.t)}`} />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// Quote cards: a line said in the clip (or your own), 1:1 for X and 4:5 for LinkedIn / Instagram
function QuotePanel({ job, clip, busy, act }) {
  const [text, setText] = useState(clip.quote)
  useEffect(() => setText(clip.quote), [clip.quote])
  const edit = (t) => act(() => api.editQuote(job.id, clip.index, t)).catch(() => {})
  const cards = clip.quote_cards || []
  return (
    <div className="opt-col cover">
      <span className="muted">Quote cards <span className="small">· for X (1:1) and LinkedIn / Instagram (4:5) posts</span></span>
      <div className="cover-row">
        {cards.length ? (
          <div className="quote-thumbs">
            {cards.map((f) => {
              const url = api.fileUrl(job.id, f, clip.quote_version)
              return <a key={f} href={url} target="_blank" rel="noreferrer"><img src={url} alt="quote card" /></a>
            })}
          </div>
        ) : (
          <div className="cover-empty muted small">
            {!clip.file ? 'Made after rendering'
              : clip.quote_version > 0 ? 'No line to quote (no speech)'
              : <button className="btn sm" disabled={busy} onClick={() => edit('')}>Make quote cards</button>}
          </div>
        )}
        <div className="cover-ctl">
          <textarea rows={3} value={text} maxLength={300} disabled={busy || !clip.file}
                    placeholder="A line from the clip — picked for you"
                    onChange={(e) => setText(e.target.value)}
                    onBlur={() => text.trim() !== (clip.quote || '') && edit(text)} />
          <div className="row-btns">
            {clip.quote_custom && (
              <button className="btn sm ghost" disabled={busy} onClick={() => edit('')}>Pick from the clip</button>
            )}
            {cards.map((f) => (
              <a key={f} className="btn sm ghost" href={api.fileUrl(job.id, f, clip.quote_version)} download={f}>
                ⬇ {f.includes('quote45') ? '4:5' : '1:1'}
              </a>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function ShortCard({ job, clip, busy, act, presets, kit, sounds }) {
  const o = clip.options || {}
  const setOpt = (k, v) => act(() => api.editClip(job.id, clip.index, { options: { ...o, [k]: v } })).catch(() => {})
  const src = clip.file && api.fileUrl(job.id, clip.file, clip.version)
  const poster = clip.file && api.fileUrl(job.id, clip.file.replace('.mp4', '.jpg'), clip.version)
  const noCaptions = (o.caption_style ?? presets?.style) === 'none'
  const kitOn = kit?.enabled && (kit.logo || kit.end_card)
  // rendered with another version of the brand kit (or before it existed) → offer a re-render
  const oldKit = !!clip.file && !!kit && (kit.enabled ? clip.brand_rev !== kit.rev : clip.brand_rev >= 0)

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
          {clip.cut_seconds > 0.5 ? (
            <span className="cut"> · ✂ {clip.cut_seconds.toFixed(1)}s</span>
          ) : (
            clip.file && clip.options.jumpcut !== false && (
              <span className="muted" title="Jump cuts found no pause longer than MAX_PAUSE in this clip"> · ✂ no pauses</span>
            )
          )}
          {clip.reframe === 'face' && <span className="cut"> · 🎯 face</span>}
          {clip.reframe === 'split' && <span className="cut"> · 👥 2 speakers</span>}
          {clip.reframe === 'speaker' && <span className="cut"> · 🎙 follows talker</span>}
          {clip.reframe === 'fit' && <span className="cut" title="No face to follow: the whole frame over a blurred copy"> · 🖼 whole frame</span>}
          {clip.reframe !== 'fit' && clip.fit_seconds > 0 && (
            <span className="cut" title="Shots with no face (screen, slides, action) are shown whole over a blurred copy">
              {' '}· 🖼 {clip.fit_seconds.toFixed(0)}s shown whole
            </span>
          )}
        </p>

        <Seg label="Jump cuts" value={o.jumpcut ?? true} disabled={busy}
             options={[[true, 'On'], [false, 'Off']]} onChange={(v) => setOpt('jumpcut', v)} />
        <Seg label="Zoom" value={o.zoom ?? false} disabled={busy || o.jumpcut === false}
             options={[[false, 'Off'], [true, 'Punch-in']]} onChange={(v) => setOpt('zoom', v)} />
        <Seg label="Framing" value={o.reframe ?? 'auto'} disabled={busy}
             options={[['auto', 'Auto'], ['face', 'Face'], ['split', '2-up'], ['speaker', 'Talker'], ['fit', 'Fit'], ['center', 'Centre']]}
             onChange={(v) => setOpt('reframe', v)} />
        <FollowPicker job={job} clip={clip} busy={busy} act={act} o={o} />
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

        {kitOn && (
          <>
            <Seg label="Logo" value={o.watermark ?? true} disabled={busy || !kit.logo}
                 options={[[true, 'On'], [false, 'Off']]} onChange={(v) => setOpt('watermark', v)} />
            <Seg label="End card" value={o.end_card ?? kit.end_card} disabled={busy}
                 options={[[true, 'On'], [false, 'Off']]} onChange={(v) => setOpt('end_card', v)} />
          </>
        )}

        {sounds && (
          <>
            <Seg label="Sound fx" value={o.sfx ?? sounds.sfx_default} disabled={busy}
                 options={[[false, 'Off'], [true, 'Whoosh + pop']]} onChange={(v) => setOpt('sfx', v)} />
            <label className="opt">
              <span className="muted">Music</span>
              <select value={o.music ?? sounds.music_default} disabled={busy}
                      onChange={(e) => setOpt('music', e.target.value)}
                      title={sounds.music.length ? 'Quieter whenever someone speaks' : `Add tracks to ${sounds.folder}`}>
                <option value="">None</option>
                {sounds.music.map((m) => <option key={m} value={m}>{m.replace(/\.[^.]+$/, '')}</option>)}
                {o.music && !sounds.music.includes(o.music) && <option value={o.music}>{o.music} (missing)</option>}
              </select>
            </label>
            {!sounds.music.length && (
              <p className="muted small">No music yet — drop royalty-free tracks into <code>{sounds.folder}</code>.</p>
            )}
          </>
        )}

        <CoverPanel job={job} clip={clip} busy={busy} act={act}
                    defaultText={clip.content?.instagram?.extra?.cover_text || clip.hook} />
        <QuotePanel job={job} clip={clip} busy={busy} act={act} />

        {clip.render_error && <p className="err">Render failed: {clip.render_error}</p>}
        <div className="actions">
          {(clip.stale || !clip.file || clip.render_error || oldKit) && (
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
        {!clip.stale && oldKit && (
          <p className="hint">Made with {kit.enabled ? 'an older' : 'a switched-off'} brand kit — re-render for the
            current look.</p>
        )}
      </div>
    </div>
  )
}

export default function ShortsStep({ job, busy, act, go }) {
  const clips = job.clips.filter((c) => c.approved)
  const stale = clips.filter((c) => c.stale).length
  const [presets, setPresets] = useState(null)
  const [kit, setKit] = useState(null)
  const [sounds, setSounds] = useState(null)
  useEffect(() => {
    api.captionPresets().then(setPresets).catch(() => setPresets(null))
    api.brandKit().then(setKit).catch(() => setKit(null))
    api.sounds().then(setSounds).catch(() => setSounds(null))
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
          <ShortCard key={c.index} job={job} clip={c} busy={busy} act={act} presets={presets} kit={kit} sounds={sounds} />
        ))}
      </div>
      <div className="footer-bar">
        <span className="muted">{stale > 0 ? `${stale} short(s) changed and waiting for re-render` : 'Happy with the cuts?'}</span>
        <button className="primary" onClick={() => go('posts')}>Review posts →</button>
      </div>
    </div>
  )
}
