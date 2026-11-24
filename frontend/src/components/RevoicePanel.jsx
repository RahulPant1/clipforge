import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { Line } from './DubEditor.jsx'

// 🔁 Re-voice a recording: your narration (audio or video) → each sentence in a Voice Library voice at your timing.
export default function RevoicePanel({ voices, Recorder }) {
  const [list, setList] = useState(null)
  const [sel, setSel] = useState(null)
  const [item, setItem] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const fileRef = useRef(null)
  const reload = () => api.revoiceList().then(setList)
  useEffect(() => { reload().catch(() => setList({ items: [], max_seconds: 180 })) }, [])
  useEffect(() => {
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
  const upload = (f) => f && run(() => api.createRevoice(f), 'Transcribing your recording…').then((r) => r && setSel(r.id))
  const stale = new Set(item?.stale || [])
  const s = item?.script
  const v = voices.find((x) => x.id === s?.voice)

  return (
    <div className="card vl-step revoice">
      <h3>🔁 Re-voice a recording</h3>
      <p className="muted small">Record or import your narration (audio or a video, up to {Math.round((list?.max_seconds || 180) / 60)} min):
        every sentence is spoken again in a voice from this library, starting where you said it — so your timing and
        pauses stay. Your intonation doesn't: the voice reads the words in its own delivery. A video keeps its picture.</p>
      <div className="row-btns">
        {Recorder && <Recorder disabled={!!busy} onDone={upload} />}
        <button className="btn ghost" disabled={!!busy} onClick={() => fileRef.current?.click()}>⬆ Import narration</button>
        <input ref={fileRef} type="file" accept="audio/*,video/*" hidden onChange={(e) => { upload(e.target.files?.[0]); e.target.value = '' }} />
        {list?.items?.length > 0 && (
          <select value={sel || ''} onChange={(e) => setSel(e.target.value || null)}>
            <option value="">Earlier recordings…</option>
            {list.items.map((x) => <option key={x.id} value={x.id}>{x.name} ({Math.round(x.duration)} s)</option>)}
          </select>
        )}
        {busy && <span className="muted small">{busy}</span>}
      </div>
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
                    onClick={() => window.confirm('Delete this recording and its re-voiced version?')
                      && run(() => api.deleteRevoice(item.id), 'Deleting…').then(() => setSel(null))}>Delete</button>
          </div>
          <ul className="dub-lines">
            {s.lines.map((ln) => (
              <Line key={ln.i} ln={ln} stale={stale.has(ln.i)} busy={!!busy} maxRate={item.max_rate}
                    src={(f) => api.revoiceFile(item.id, f)}
                    onText={(t) => run(() => api.putRevoice(item.id, { texts: { [ln.i]: t } }), 'Saving…')}
                    onRegen={() => run(() => api.generateRevoice(item.id, [ln.i]), 'Speaking 1 sentence…')} />
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
                <a className="btn sm" href={api.revoiceFile(item.id, item.output, s.track_version)} download={`${item.name}-revoiced${item.has_video ? '.mp4' : '.wav'}`}>⬇ Download</a>
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
}
