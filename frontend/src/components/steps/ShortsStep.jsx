import { useEffect, useState } from 'react'
import { api } from '../../api.js'
import { LookChip, Seg } from '../CaptionControls.jsx'
import CropEditor from '../CropEditor.jsx'
import BrollEditor from '../BrollEditor.jsx'
import DubEditor from '../DubEditor.jsx'
import ExportPreview from '../ExportPreview.jsx'
import InsertEditor from '../InsertEditor.jsx'
import { FEED, feedFile, fmt } from '../../util.js'

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
  const formats = (clip.formats || []).filter((a) => FEED[a])
  const [view, setView] = useState('9:16')
  const [inspector, setInspector] = useState('Picture')
  const [cropOpen, setCropOpen] = useState(false)
  const shown = view !== '9:16' && formats.includes(view) ? view : '9:16'
  const file = clip.file && (shown === '9:16' ? clip.file : feedFile(clip.index, shown))
  const src = file && api.fileUrl(job.id, file, clip.version)
  const poster = clip.file && api.fileUrl(job.id, clip.file.replace('.mp4', '.jpg'), clip.version)
  const aspects = o.aspects ?? presets?.aspects ?? []
  const toggleAspect = (a) => setOpt('aspects', (presets?.feed_aspects || Object.keys(FEED))
    .filter((x) => (x === a ? !aspects.includes(a) : aspects.includes(x))))
  const noCaptions = (o.caption_style ?? presets?.style) === 'none'
  const kitOn = kit?.enabled && (kit.logo || kit.end_card)
  // rendered with another version of the brand kit (or before it existed) → offer a re-render
  const oldKit = !!clip.file && !!kit && (kit.enabled ? clip.brand_rev !== kit.rev : clip.brand_rev >= 0)

  return (
    <div className={`short short-desktop ${clip.stale ? 'is-stale' : ''}`}>
      <div className="short-monitor">
      {formats.length > 0 && (
        <div className="seg sm aspect-tabs">
          {['9:16', ...formats].map((a) => (
            <button key={a} className={shown === a ? 'on' : ''} onClick={() => setView(a)}
                    title={a === '9:16' ? 'Shorts, Reels, TikTok' : FEED[a].use}>{a}</button>
          ))}
        </div>
      )}
      {src ? (
        <video key={src} src={src} poster={shown === '9:16' ? poster : undefined} controls preload="none"
               style={shown === '9:16' ? undefined : { aspectRatio: FEED[shown].ratio }} />
      ) : (
        <div className="placeholder">Not rendered yet</div>
      )}
      </div>
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
            clip.file && clip.options.jumpcut !== false && !o.dub && (
              <span className="muted" title="Jump cuts found no pause longer than MAX_PAUSE in this clip"> · ✂ no pauses</span>
            )
          )}
          {o.dub && <span className="cut" title="The dialogue is AI-generated (Replace voice)"> · 🤖 AI voice</span>}
          {clip.reframe === 'face' && <span className="cut"> · 🎯 face</span>}
          {clip.reframe === 'split' && <span className="cut"> · 👥 2 speakers</span>}
          {clip.reframe === 'speaker' && <span className="cut"> · 🎙 follows talker</span>}
          {clip.reframe === 'manual' && <span className="cut"> · ✋ your crop</span>}
          {clip.reframe === 'fit' && <span className="cut" title="No face to follow: the whole frame over a blurred copy"> · 🖼 whole frame</span>}
          {clip.reframe !== 'fit' && clip.fit_seconds > 0 && (
            <span className="cut" title="Shots with no face (screen, slides, action) are shown whole over a blurred copy">
              {' '}· 🖼 {clip.fit_seconds.toFixed(0)}s shown whole
            </span>
          )}
        </p>

        <nav className="row-btns" aria-label="Short properties">
          {['Picture', 'Captions', 'Audio', 'Extras'].map((tab) => <button key={tab} type="button"
            className="btn ghost" aria-pressed={inspector === tab} onClick={() => setInspector(tab)}>{tab}</button>)}
        </nav>
        <div hidden={inspector !== 'Picture'}>
        <Seg label="Jump cuts" value={o.jumpcut ?? true} disabled={busy}
             options={[[true, 'On'], [false, 'Off']]} onChange={(v) => setOpt('jumpcut', v)} />
        <Seg label="Zoom" value={o.zoom ?? false} disabled={busy || o.jumpcut === false}
             options={[[false, 'Off'], [true, 'Punch-in']]} onChange={(v) => setOpt('zoom', v)} />
        <Seg label="Framing" value={o.reframe ?? 'auto'} disabled={busy}
             options={[['auto', 'Auto'], ['face', 'Face'], ['split', '2-up'], ['speaker', 'Talker'], ['fit', 'Fit'],
               ['center', 'Centre'], ...(o.crop_keys?.length ? [['manual', '✋ Yours']] : [])]}
             onChange={(v) => setOpt('reframe', v)} />
        {o.reframe !== 'manual' && <FollowPicker job={job} clip={clip} busy={busy} act={act} o={o} />}
        <div className="opt-col">
          <button className="btn sm ghost" disabled={busy} onClick={() => setCropOpen(true)}
                  title="Drag the crop where face tracking picked the wrong spot">
            ✋ {o.reframe === 'manual' ? 'Edit your crop' : 'Adjust crop by hand'}
          </button>
        </div>
        {cropOpen && <CropEditor job={job} clip={clip} busy={busy} act={act} o={o} onClose={() => setCropOpen(false)} />}
        <Seg label="Hook title" value={o.hook ?? true} disabled={busy || !clip.hook}
             options={[[true, 'On'], [false, 'Off']]} onChange={(v) => setOpt('hook', v)} />
        {!clip.visual && <InsertEditor job={job} clip={clip} busy={busy} act={act}
          accent={kit?.enabled && kit.accent ? kit.accent : undefined} />}
        <BrollEditor jobId={job.id} clip={clip} busy={busy} act={act} />
        </div>
        <div hidden={inspector !== 'Captions'}>
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
            <Seg label="Emoji" value={o.emoji ?? presets.emoji ?? false} disabled={busy}
                 options={[[false, 'Off'], [true, 'Colour emoji']]} onChange={(v) => setOpt('emoji', v)} />
            {(o.emoji ?? presets.emoji) && (
              <p className="muted small">Above a caption line when a word has one (money 💰, gym 🏋️ — or type one into the
                transcript), and the hook's emoji above the title card.
                {!presets.emoji_ready && ' The open-source Noto Color Emoji set (11 MB) downloads on the first render.'}</p>
            )}
            <Seg label={clip.reframe === 'split' ? 'Position (2-up: on the seam)' : 'Position'}
                 value={o.caption_position ?? presets.position} disabled={busy || noCaptions || clip.reframe === 'split'}
                 options={[['bottom', 'Bottom'], ['middle', 'Middle']]}
                 onChange={(v) => setOpt('caption_position', v)} />
          </>
        )}

        </div>
        <div hidden={inspector !== 'Extras'}>
        {kitOn && (
          <>
            <Seg label="Logo" value={o.watermark ?? true} disabled={busy || !kit.logo}
                 options={[[true, 'On'], [false, 'Off']]} onChange={(v) => setOpt('watermark', v)} />
            <Seg label="End card" value={o.end_card ?? kit.end_card} disabled={busy}
                 options={[[true, 'On'], [false, 'Off']]} onChange={(v) => setOpt('end_card', v)} />
          </>
        )}

        <div className="opt">
          <span className="muted" title="Same short, framed wider for feeds. Each one adds a render.">Feed versions</span>
          <div className="seg sm">
            {(presets?.feed_aspects || Object.keys(FEED)).map((a) => (
              <button key={a} disabled={busy} className={aspects.includes(a) ? 'on' : ''} onClick={() => toggleAspect(a)}
                      title={FEED[a].use}>{a}</button>
            ))}
          </div>
        </div>
        {aspects.length > 0 && (
          <p className="muted small">{aspects.map((a) => `${a} for ${FEED[a].use}`).join(' · ')}. Its own crop (more of
            the scene), captions and logo, same cuts.</p>
        )}

        <CoverPanel job={job} clip={clip} busy={busy} act={act}
                    defaultText={clip.content?.instagram?.extra?.cover_text || clip.hook} />
        <QuotePanel job={job} clip={clip} busy={busy} act={act} />

        </div>
        <div hidden={inspector !== 'Audio'}>
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

          {!clip.visual && <DubEditor job={job} clip={clip} busy={busy} act={act} setOpt={setOpt} />}
        </div>
        {clip.render_error && <p className="err">Render failed: {clip.render_error}</p>}
        <ExportPreview key={job.updated_at} jobId={job.id} index={clip.index} act={act} busy={busy} />
        <div className="actions">
          {(clip.stale || !clip.file || clip.render_error || oldKit) && (
            <button className="btn primary-link" disabled={busy}
                    onClick={() => act(() => api.rerender(job.id, clip.index)).catch(() => {})}>
              ↻ {clip.file ? 'Re-render' : 'Render'}
            </button>
          )}
          {src && (
            <>
              <a className="btn" href={src} download={file}>Download{formats.length > 0 ? ` ${shown}` : ''}</a>
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
  const [selected, setSelected] = useState(null)
  const [batch, setBatch] = useState([])
  const [applying, setApplying] = useState(false)
  const active = clips.find((clip) => clip.index === selected) ?? clips[0]
  const stale = clips.filter((c) => c.stale).length
  const [presets, setPresets] = useState(null)
  const [kit, setKit] = useState(null)
  const [sounds, setSounds] = useState(null)
  useEffect(() => {
    api.captionPresets().then(setPresets).catch(() => setPresets(null))
    api.brandKit().then(setKit).catch(() => setKit(null))
    api.sounds().then(setSounds).catch(() => setSounds(null))
  }, [])
  const targets = clips.filter((clip) => clip.index !== active?.index && batch.includes(clip.index))
  const applyCaptionLook = async () => {
    setApplying(true)
    try {
      const { caption_style, caption_preset, caption_position } = active.options || {}
      await act(async () => {
        for (const clip of targets) {
          await api.editClip(job.id, clip.index, { options: { ...clip.options,
            caption_style: caption_style ?? null, caption_preset: caption_preset ?? null,
            caption_position: caption_position ?? null } })
        }
      })
      setBatch([])
    } catch {
      // The workspace shows the failed action.
    } finally {
      setApplying(false)
    }
  }
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
      <div className="scene-strip" role="group" aria-label="Approved shorts">
        {clips.map((clip) => <button key={clip.index} type="button" className="btn ghost"
          aria-pressed={active?.index === clip.index} onClick={() => setSelected(clip.index)}>
          {clip.file && <img src={api.fileUrl(job.id, clip.file.replace('.mp4', '.jpg'), clip.version)} alt="" />}
          <span>{clip.title}</span><small>{clip.stale ? 'Needs render' : clip.file ? 'Rendered' : 'Approved'}</small>
        </button>)}
      </div>
      {clips.length > 1 && <details className="card">
        <summary>Apply caption styling to other shorts</summary>
        <div className="row-btns">
          {clips.filter((clip) => clip.index !== active.index).map((clip) => <label key={clip.index}>
            <input type="checkbox" checked={batch.includes(clip.index)} disabled={busy || applying}
              onChange={(event) => setBatch((current) => event.target.checked
                ? [...current, clip.index] : current.filter((index) => index !== clip.index))} /> {clip.title}
          </label>)}
          <button className="btn" type="button" disabled={!targets.length || busy || applying} onClick={applyCaptionLook}>
            Apply current caption look to selected
          </button>
        </div>
      </details>}
      {active && <ShortCard key={active.index} job={job} clip={active} busy={busy || applying} act={act}
        presets={presets} kit={kit} sounds={sounds} />}
      <div className="footer-bar">
        <span className="muted">{stale > 0 ? `${stale} short(s) changed and waiting for re-render` : 'Happy with the cuts?'}</span>
        <button className="primary" onClick={() => go('posts')}>Review posts →</button>
      </div>
    </div>
  )
}
