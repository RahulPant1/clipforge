import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { fmtPrecise } from '../util.js'

export function fitChip(ln, stale, maxRate) {
  if (!ln.text.trim()) return <span className="chip">silence</span>
  if (stale) return <span className="chip warnchip">{ln.file ? 'changed — generate' : 'not generated'}</span>
  if (ln.fit === 'too_long') return <span className="chip badchip" title={`Doesn't fit even at ${maxRate}× — it's cut at the end. Shorten the text.`}>⚠ {ln.over ? `${ln.over} s ` : ''}too long — shorten</span>
  if (ln.fit === 'stretched') return <span className="chip" title="Sped up to fit its time">⏩ {ln.rate}×</span>
  if (ln.fit === 'ok') return <span className="chip goodchip">✓ fits</span>
  return null
}

export function Line({ ln, stale, busy, maxRate, onText, onRegen, src }) {
  const [text, setText] = useState(ln.text)
  useEffect(() => setText(ln.text), [ln.text])
  return (
    <li className="dub-line">
      <span className="muted small dub-t">{fmtPrecise(ln.start)}</span>
      <div className="dub-body">
        <span className="muted small dub-orig" title="What was said">{ln.original}</span>
        <textarea rows={2} value={text} maxLength={2000} disabled={busy} placeholder="(silence here)"
                  onChange={(e) => setText(e.target.value)} onBlur={() => text !== ln.text && onText(text)} />
        <div className="row-btns">
          {fitChip(ln, stale, maxRate)}
          {ln.file && !stale && <audio controls preload="none" src={src(ln.file)} />}
          <button className="btn sm ghost" disabled={busy || !ln.text.trim()} onClick={onRegen}
                  title="Speak just this line again (another take)">↻ this line</button>
        </div>
      </div>
    </li>
  )
}

// 🎙 Replace voice: the clip's sentences re-spoken in a Voice Library voice, laid out where the originals were.
export default function DubEditor({ job, clip, busy, act, setOpt, fixedVoice = false, previewPacing }) {
  const [data, setData] = useState(null)
  const [voices, setVoices] = useState([])
  const [working, setWorking] = useState('')
  const [open, setOpen] = useState(fixedVoice || !!clip.options?.dub)
  const [preview, setPreview] = useState('')
  const [ambienceAvailable, setAmbienceAvailable] = useState(false)
  const pacing = previewPacing ?? clip.options?.dub_pacing ?? 'source'
  const narrationStatus = job.narration?.items[clip.index]?.status
  const load = () => api.dub(job.id, clip.index).then(setData)
  useEffect(() => {
    if (!open) return
    load().catch(() => setData(null))
    if (!fixedVoice) {
      api.voices().then((r) => setVoices(r.voices)).catch(() => setVoices([]))
      api.narrationOptions().then((r) => setAmbienceAvailable(r.ambience_available)).catch(() => {})
    }
  }, [open, job.id, clip.index, clip.start, clip.end, clip.version, fixedVoice, narrationStatus])
  useEffect(() => setPreview(''), [pacing, clip.start, clip.end])

  const run = async (fn, label) => {
    setPreview('')
    setWorking(label)
    try { const r = await act(fn); if (r?.script) setData(r); else await load() } catch { /* shown by act() */ } finally { setWorking('') }
  }
  const s = data?.script
  const stale = new Set(data?.stale || [])
  const voice = voices.find((v) => v.id === s?.voice)
  const on = !!clip.options?.dub
  const faceFraming = ['face', 'speaker', 'split'].includes(clip.reframe)
  const tooLong = s ? s.lines.filter((l) => l.fit === 'too_long').length : 0
  const makePreview = async () => {
    setWorking('Making video preview…')
    setPreview('')
    try {
      const { file } = await act(() => api.previewDub(job.id, clip.index, pacing))
      setPreview(api.fileUrl(job.id, file, Date.now()))
    } catch { /* shown by act() */ } finally { setWorking('') }
  }

  if (!open) {
    return (
      <div className="opt-col">
        <button className="btn sm ghost" disabled={busy} onClick={() => setOpen(true)}>🎙 Replace voice…</button>
      </div>
    )
  }
  return (
    <div className="opt-col dub">
      <span className="muted">🎙 Replace voice <span className="small">· speech from a saved Voice Library voice.</span></span>
      {faceFraming && <span className="muted small warn">The speaker is on screen: lips won't match the new words.</span>}
      {!fixedVoice && !voices.length && <span className="muted small">No voices yet — make one in 🗣 Voice Library first.</span>}
      {s && (
        <>
          <div className="row-btns">
            {!fixedVoice && <select value={s.voice || ''} disabled={busy || !!working}
                    onChange={(e) => run(() => api.putDub(job.id, clip.index, { voice: e.target.value }), 'Saving…')}>
              <option value="">Choose a voice…</option>
              {voices.map((v) => <option key={v.id} value={v.id} disabled={!v.builds?.length}>
                {v.name}{v.builds?.length ? '' : ' (not built)'}</option>)}
            </select>}
            {voice && (
              <select value={s.version ?? ''} disabled={busy || !!working}
                      onChange={(e) => run(() => api.putDub(job.id, clip.index,
                        { voice: s.voice, version: e.target.value === '' ? null : Number(e.target.value) }), 'Saving…')}>
                <option value="">newest version</option>
                {[...voice.builds].reverse().filter((b) => b.prompt).map((b) => <option key={b.version} value={b.version}>v{b.version}</option>)}
              </select>
            )}
            {!fixedVoice && <button className="btn sm" disabled={busy || !!working || !s.voice || !stale.size}
                    onClick={() => run(() => api.generateDub(job.id, clip.index), `Speaking ${stale.size} line(s)…`)}>
              ▶ Generate {stale.size || ''} line{stale.size === 1 ? '' : 's'}
            </button>}
            <button className="btn sm" disabled={busy || !!working || !s.voice} onClick={makePreview}>
              Preview first line over video
            </button>
            {working && <span className="muted small">{working} (~2 s per second of speech + ~20 s to load)</span>}
          </div>
          {preview && <video key={preview} aria-label="First narration line over source video"
                             controls playsInline preload="metadata" style={{ width: '100%', maxWidth: 480 }}
                             src={preview} />}
          <ul className="dub-lines">
            {s.lines.filter((l) => l.start >= clip.start && l.start < clip.end).map((ln) => (
              <Line key={ln.i} ln={ln} stale={stale.has(ln.i)} busy={busy || !!working} maxRate={data.max_rate}
                    src={(f) => api.fileUrl(job.id, f)}
                    onText={(t) => run(() => api.putDub(job.id, clip.index, { texts: { [ln.i]: t } }), 'Saving…')}
                    onRegen={() => run(() => api.generateDub(job.id, clip.index, [ln.i]), 'Speaking 1 line…')} />
            ))}
          </ul>
          {tooLong > 0 && <p className="err small">{tooLong} line(s) don't fit their time and get cut — shorten them.</p>}
          {s.track && (
            <div className="dub-track">
              <span className="muted small">🤖 AI voice track</span>
              <audio controls preload="none" src={`${api.fileUrl(job.id, s.track)}?v=${s.track_version}`} />
            </div>
          )}
          {!fixedVoice && <div className="row-btns">
            <select aria-label="Narration pacing" value={pacing} disabled={busy || !!working}
                    onChange={(e) => setOpt('dub_pacing', e.target.value)}>
              <option value="source">Match source timing</option>
              <option value="natural">Natural pacing · flexible picture</option>
            </select>
            <label className="small"><input type="checkbox" checked={!!clip.options?.dub_ambience}
                                            disabled={busy || !!working || (!ambienceAvailable && !clip.options?.dub_ambience)}
                                            onChange={(e) => setOpt('dub_ambience', e.target.checked)} />{' '}
              Keep source ambience {!ambienceAvailable && '(Demucs required)'}</label>
            <label className="small"><input type="checkbox" checked={on} disabled={busy || !s.track}
                                            onChange={(e) => setOpt('dub', e.target.checked || null)} />{' '}
              Use in this short {on && '(re-render to hear it)'}</label>
            <button className="btn sm ghost" disabled={busy || !!working}
                    onClick={() => window.confirm('Start again from what is said in the clip now? Your edited lines are reset.')
                      && run(() => api.putDub(job.id, clip.index,
                        { reset: true, voice: s.voice || null, version: s.version }), 'Resetting…')}>
              Reset lines
            </button>
            {data.saved && <button className="btn sm ghost" disabled={busy || !!working}
                                   onClick={() => window.confirm('Remove the replaced voice and go back to the original?')
                                     && act(() => api.deleteDub(job.id, clip.index)).then(() => { setData(null); setOpen(false) }).catch(() => {})}>
              Remove</button>}
          </div>}
        </>
      )}
    </div>
  )
}
