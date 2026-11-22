import { useEffect, useState } from 'react'
import { api } from '../api.js'

const EDITABLE = ['enabled', 'position', 'size', 'opacity', 'end_card', 'end_seconds', 'end_text', 'handle', 'accent',
  'accent_captions']

// Your look on every short: logo in a corner, an end card after the last word, one brand colour for covers,
// quote cards, carousels (and optionally the caption highlight). One kit for all jobs.
export default function BrandKit() {
  const [info, setInfo] = useState(null) // server copy
  const [kit, setKit] = useState(null) // edited copy
  const [state, setState] = useState('')
  const [err, setErr] = useState('')

  const load = (r) => { setInfo(r); setKit(r) }
  useEffect(() => { api.brandKit().then(load).catch((e) => setErr(e.message)) }, [])

  if (!kit) return <div className="voice-wrap">{err ? <div className="banner error">{err}</div> : 'Loading…'}</div>

  const dirty = EDITABLE.some((k) => kit[k] !== info[k])
  const set = (patch) => { setKit({ ...kit, ...patch }); setState('') }
  const run = async (fn) => {
    setErr('')
    setState('saving')
    try { load(await fn()); setState('saved') } catch (e) { setErr(e.message); setState('') }
  }
  const save = () => run(() => api.saveBrandKit(Object.fromEntries([...EDITABLE, 'logo', 'rev'].map((k) => [k, kit[k]]))))
  const upload = (f) => f && run(async () => {
    if (dirty) await api.saveBrandKit(Object.fromEntries([...EDITABLE, 'logo', 'rev'].map((k) => [k, kit[k]])))
    return api.uploadLogo(f)
  })
  const accent = kit.accent || info.default_accent
  const off = !kit.enabled

  return (
    <div className="voice-wrap kit">
      <div>
        <h2>🎨 Brand kit</h2>
        <p className="muted">Your logo, an end card and one brand colour on every short. Burned in when a short is
          rendered — shorts made earlier keep their look until you re-render them (the Shorts step offers it).</p>
      </div>
      {err && <div className="banner error">{err}</div>}

      <label className="checkline">
        <input type="checkbox" checked={kit.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
        <span><b>Use the brand kit</b> <span className="muted">· off = shorts look as they did before</span></span>
      </label>

      <div className={`kit-grid ${off ? 'off' : ''}`}>
        <div className="kit-form">
          <h3>Logo</h3>
          <div className="kit-logo">
            {info.logo ? <img src={api.logoUrl(info.rev)} alt="logo" /> : <span className="muted small">No logo yet</span>}
            <label className="btn">
              {info.logo ? 'Replace…' : 'Upload logo…'}
              <input type="file" accept="image/png,image/jpeg,image/webp" hidden
                     onChange={(e) => { upload(e.target.files?.[0]); e.target.value = '' }} />
            </label>
            {info.logo && <button type="button" className="linkbtn" onClick={() => run(api.removeLogo)}>remove</button>}
          </div>
          <p className="muted small">A PNG with a transparent background looks best. Shown after the hook title, hidden
            on the end card.</p>
          <div className="seg sm">
            {[['top-left', '↖ Top left'], ['top-right', 'Top right ↗']].map(([k, l]) => (
              <button type="button" key={k} className={kit.position === k ? 'on' : ''} disabled={off}
                      onClick={() => set({ position: k })}>{l}</button>
            ))}
          </div>
          <label className="kit-range">Size <input type="range" min="6" max="35" value={kit.size} disabled={off}
                                                   onChange={(e) => set({ size: Number(e.target.value) })} />
            <span>{kit.size}%</span></label>
          <label className="kit-range">Opacity <input type="range" min="0.2" max="1" step="0.05" value={kit.opacity}
                                                      disabled={off} onChange={(e) => set({ opacity: Number(e.target.value) })} />
            <span>{Math.round(kit.opacity * 100)}%</span></label>

          <h3>End card</h3>
          <label className="checkline">
            <input type="checkbox" checked={kit.end_card} disabled={off} onChange={(e) => set({ end_card: e.target.checked })} />
            <span>After the last word: the final frame held and blurred, with your logo, a line and your handle
              <span className="muted"> · skipped when a short is already close to 59 s</span></span>
          </label>
          <label className="fl"><span>Line</span>
            <input value={kit.end_text} maxLength={60} disabled={off || !kit.end_card}
                   onChange={(e) => set({ end_text: e.target.value })} /></label>
          <label className="fl"><span>Handle <span className="muted">· also in post CTAs, quote cards, carousels</span></span>
            <input value={kit.handle} maxLength={40} disabled={off} placeholder={info.default_handle || '@yourname'}
                   onChange={(e) => set({ handle: e.target.value })} /></label>
          <label className="kit-range">Length <input type="range" min="1" max="4" step="0.5" value={kit.end_seconds}
                                                     disabled={off || !kit.end_card}
                                                     onChange={(e) => set({ end_seconds: Number(e.target.value) })} />
            <span>{kit.end_seconds} s</span></label>

          <h3>Brand colour</h3>
          <div className="kit-colour">
            <input type="color" value={accent} disabled={off} onChange={(e) => set({ accent: e.target.value })} />
            <code>{accent}</code>
            {kit.accent && <button type="button" className="linkbtn" disabled={off} onClick={() => set({ accent: '' })}>
              back to default</button>}
          </div>
          <span className="muted small">Covers, quote cards, carousels and the end card handle.</span>
          <label className="checkline">
            <input type="checkbox" checked={kit.accent_captions} disabled={off}
                   onChange={(e) => set({ accent_captions: e.target.checked })} />
            <span>Also for the highlighted word in animated captions</span>
          </label>

          <div className="kit-save">
            <button className="primary" disabled={!dirty || state === 'saving'} onClick={save}>
              {state === 'saving' ? 'Saving…' : 'Save brand kit'}
            </button>
            {state === 'saved' && !dirty && <span className="ok">✓ Saved — new renders use it</span>}
          </div>
        </div>

        <div className="kit-previews">
          <div>
            <span className="muted small">During the short</span>
            <div className="kit-phone">
              {info.logo && kit.enabled && (
                <img className="kit-wm" src={api.logoUrl(info.rev)} alt=""
                     style={{ width: `${kit.size}%`, opacity: kit.opacity,
                       [kit.position === 'top-left' ? 'left' : 'right']: `${(48 / 1080) * 100}%`, top: `${(120 / 1920) * 100}%` }} />
              )}
              <span className="kit-cap">captions <b style={{ color: kit.accent_captions && kit.enabled ? accent : '#ffd400' }}>here</b></span>
            </div>
          </div>
          <div>
            <span className="muted small">End card {dirty && '(save to update)'}</span>
            <div className="kit-phone end">
              {kit.enabled && info.end_card ? <img src={api.endCardUrl(`${info.rev}`)} alt="" />
                : <span className="muted small kit-none">off</span>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
