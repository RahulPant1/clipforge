import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { Line } from './DubEditor.jsx'
import AudioRecorder from './AudioRecorder.jsx'
import VideoCapture from './VideoCapture.jsx'
import TakeReview from './TakeReview.jsx'

// 🔁 Re-voice a recording: your narration (audio or video) → each sentence in a Voice Library voice at your timing.
export default function RevoicePanel({ onVoiceLibrary }) {
  const [voiceList, setVoices] = useState(null)
  const voices = voiceList ?? []
  const [list, setList] = useState(null)
  const [sel, setSel] = useState(null)
  const [item, setItem] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [pickups, setPickups] = useState(null)
  const fileRef = useRef(null)
  const reload = () => api.revoiceList().then(setList)
  useEffect(() => {
    reload().catch(() => setList({ items: [], max_seconds: 180 }))
    api.voices().then(({ voices: items }) => setVoices(items)).catch((error) => setErr(error.message))
  }, [])
  useEffect(() => {
    setPickups(null)
    if (!sel) return setItem(null)
    api.revoice(sel).then(setItem).catch((e) => setErr(e.message))
  }, [sel])

  const run = async (fn, label) => {
    setErr('')
    setBusy(label)
    try {
      const r = await fn()
      if (r?.script) setItem(r)
      await reload()
      return r
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }
  const upload = (f, recorded = false) => f && run(() => api.createRevoice(f, '', recorded), 'Transcribing your recording…').then((r) => r && setSel(r.id))
  const stale = new Set(item?.stale || [])
  const s = item?.script
  const v = voices.find((x) => x.id === s?.voice)
  const picked = (s?.lines ?? []).filter((line) => pickups?.includes(line.i) && line.text.trim())
  const exportPickups = async () => {
    const texts = Object.fromEntries(picked.map((line) => [line.i, line.text]))
    if (await run(() => api.pickupRevoiceBatch(item.id, texts), 'Exporting sentence pickups…')) setPickups(null)
  }

  return (
    <div className="card vl-step revoice">
      <h2>Re-voice a recording</h2>
      <p className="muted small">Record or import audio or video up to {Math.round((list?.max_seconds || 180) / 60)} minutes.
        Re-voice the whole recording, or edit one sentence and choose Replace only this sentence to keep the rest of the original audio.</p>
      <div className="row-btns">
        <AudioRecorder disabled={!!busy} onDone={upload} />
        <button className="btn ghost" disabled={!!busy} onClick={() => fileRef.current?.click()}>⬆ Import narration</button>
        <input ref={fileRef} type="file" accept="audio/*,video/*" hidden onChange={(e) => { upload(e.target.files?.[0]); e.target.value = '' }} />
        {list?.items?.length > 0 && (
          <select value={sel || ''} disabled={!!busy} onChange={(e) => setSel(e.target.value || null)}>
            <option value="">Earlier recordings…</option>
            {list.items.map((x) => <option key={x.id} value={x.id}>{x.name} ({Math.round(x.duration)} s)</option>)}
          </select>
        )}
        {busy && <span className="muted small">{busy}</span>}
      </div>
      <VideoCapture disabled={!!busy} maxSeconds={Math.max(1, (list?.max_seconds || 180) - 1)}
        onUse={(recording) => upload(recording, true)} />
      {voiceList && !voices.some((voice) => voice.builds?.some((build) => build.prompt)) &&
        <p className="muted small">To re-voice a recording, <button type="button" className="btn sm ghost"
          onClick={onVoiceLibrary}>set up a voice in Voice Library</button>.</p>}
      {err && <p className="err small">{err}</p>}
      {item && s && (
        <>
          <div className="row-btns">
            <b>{item.name}</b>
            <select value={s.voice || ''} disabled={!!busy}
                    onChange={(e) => run(() => api.putRevoice(item.id, { voice: e.target.value }), 'Saving…')}>
              <option value="">Speak it in…</option>
              {voices.map((x) => <option key={x.id} value={x.id} disabled={!x.builds?.length}>{x.name}{x.builds?.length ? '' : ' (not built)'}</option>)}
            </select>
            {v && (
              <select value={s.version ?? ''} disabled={!!busy}
                      onChange={(e) => run(() => api.putRevoice(item.id, { voice: s.voice, version: e.target.value === '' ? null : Number(e.target.value) }), 'Saving…')}>
                <option value="">newest version</option>
                {[...v.builds].reverse().filter((b) => b.prompt).map((b) => <option key={b.version} value={b.version}>v{b.version}</option>)}
              </select>
            )}
            <button className="primary" disabled={!!busy || !s.voice || !stale.size}
                    onClick={() => run(() => api.generateRevoice(item.id), `Speaking ${stale.size} sentence(s)… (~2 s per second of speech)`)}>
              ▶ Re-voice {stale.size || ''}
            </button>
            <button className="btn ghost" disabled={!!busy}
                    onClick={() => window.confirm('Delete this recording and its exports?')
                      && run(() => api.deleteRevoice(item.id), 'Deleting…').then(() => setSel(null))}>Delete</button>
          </div>
          {s.voice && <div className="row-btns">
            {pickups === null ? <button className="btn ghost" disabled={!!busy} onClick={() => setPickups([])}>
              Select sentences for pickups
            </button> : <>
              <span className="small" role="status">{picked.length} of 20 selected</span>
              <button className="btn" disabled={!!busy || !picked.length} onClick={exportPickups}>Export {picked.length || 'selected'} pickups</button>
              <button className="btn ghost" disabled={!!busy} onClick={() => setPickups(null)}>Cancel selection</button>
            </>}
          </div>}
          <div className="revoice-desktop">
          <ul className="dub-lines">
            {s.lines.map((ln) => (
              <Line key={ln.i} ln={ln} stale={stale.has(ln.i)} busy={!!busy} maxRate={item.max_rate}
                    src={(f) => api.revoiceFile(item.id, f)}
                    onText={(t) => run(() => api.putRevoice(item.id, { texts: { [ln.i]: t } }), 'Saving…')}
                    onRegen={() => run(() => api.generateRevoice(item.id, [ln.i]), 'Speaking 1 sentence…')}
                    selection={pickups !== null && ln.text.trim() ? {
                      checked: pickups.includes(ln.i),
                      onChange: (e) => {
                        const checked = e.target.checked
                        setPickups((previous) => checked && previous.length < 20
                          ? [...previous, ln.i] : previous.filter((id) => id !== ln.i))
                      },
                    } : null}
                    onPickup={s.voice && pickups === null ? (text) => run(() => api.pickupRevoice(item.id, ln.i, text),
                      'Replacing this sentence…') : null}>
                <TakeReview issues={item.take_issues?.[ln.i]} heard={ln.heard_text} busy={busy}
                  revision={s.track_version} onSuggest={() => api.reviewRevoiceTake(item.id, ln.i)} />
              </Line>
            ))}
          </ul>
          <div className="revoice-out">
            <span className="muted small">Original</span>
            {item.has_video
              ? <video controls preload="metadata" src={api.revoiceFile(item.id, item.source)} />
              : <audio controls preload="none" src={api.revoiceFile(item.id, item.source)} />}
            {item.output && (
              <>
                <span className="muted small">🤖 Re-voiced</span>
                {item.has_video
                  ? <video controls preload="metadata" src={api.revoiceFile(item.id, item.output, s.track_version)} />
                  : <audio controls src={api.revoiceFile(item.id, item.output, s.track_version)} />}
                <a className="btn sm" href={api.revoiceFile(item.id, item.output, s.track_version)} download={`${item.name}-revoiced${item.has_video ? '.mp4' : '.wav'}`}>Download full recording</a>
              </>
            )}
            {item.pickup_output && (
              <>
                <span className="muted small">Sentence pickups · {(item.pickup_lines ?? [item.pickup_line])
                  .map((index) => index + 1).join(', ')}</span>
                {item.has_video
                  ? <video controls preload="metadata" src={api.revoiceFile(item.id, item.pickup_output, item.pickup_updated)} />
                  : <audio controls src={api.revoiceFile(item.id, item.pickup_output, item.pickup_updated)} />}
                <a className="btn sm" href={api.revoiceFile(item.id, item.pickup_output, item.pickup_updated)}
                  download={`${item.name}-pickup${item.has_video ? '.mp4' : '.wav'}`}>Download sentence pickup</a>
              </>
            )}
          </div>
          </div>
        </>
      )}
    </div>
  )
}
