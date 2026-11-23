import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

// Lines to read: different moods and lengths give the voice more to learn from. Read naturally, ~5-15 s each.
const SCRIPT = [
  { style: 'calm', text: 'Hi, I make videos about tech, careers and the small habits that change how you work. Today I want to show you something simple that saved me hours every week.' },
  { style: 'energetic', text: 'Okay, this one honestly blew my mind! I tried it for seven days, and the results were way better than I expected.' },
  { style: 'storytelling', text: 'Three years ago I was stuck in a job I did not enjoy. Every evening I told myself I would start tomorrow. Then one day, I actually did.' },
  { style: 'serious', text: 'Here is the mistake most people make. They copy what works for someone else, without asking if it fits their own life.' },
  { style: 'calm', text: 'If you found this useful, save it for later and share it with a friend who needs to hear it. See you in the next one.' },
  { style: 'hinglish', text: 'Dekho, sabse important cheez hai consistency. Agar tum roz thoda thoda karoge, toh results khud dikhenge.' },
  { style: 'hinglish', text: 'Yeh tip maine apne first job mein seekhi thi, aur sach bataun, isne meri life change kar di.' },
  { style: 'energetic', text: 'Wait for it. The last step is the one nobody talks about, and it makes all the difference!' },
]

const LANGS = ['Auto', 'English', 'Chinese', 'Japanese', 'Korean', 'German', 'French', 'Russian', 'Portuguese',
  'Spanish', 'Italian']

function Recorder({ disabled, onDone }) {
  const [rec, setRec] = useState(null) // MediaRecorder while recording
  const [secs, setSecs] = useState(0)
  const [err, setErr] = useState('')
  const timer = useRef(0)
  const start = async () => {
    setErr('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      })
      const mr = new MediaRecorder(stream)
      const chunks = []
      mr.ondataavailable = (e) => e.data.size && chunks.push(e.data)
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        clearInterval(timer.current)
        const blob = new Blob(chunks, { type: mr.mimeType || 'audio/webm' })
        onDone(new File([blob], `take-${Date.now()}.webm`, { type: blob.type }))
      }
      mr.start()
      setSecs(0)
      timer.current = setInterval(() => setSecs((s) => s + 1), 1000)
      setRec(mr)
    } catch (e) {
      setErr(`Microphone not available: ${e.message}`)
    }
  }
  const stop = () => { rec?.stop(); setRec(null) }
  return (
    <span className="recorder">
      {rec ? (
        <button className="btn rec-on" onClick={stop}>■ Stop ({secs}s)</button>
      ) : (
        <button className="btn" disabled={disabled} onClick={start}>● Record</button>
      )}
      {err && <span className="err small">{err}</span>}
    </span>
  )
}

function RecordingRow({ v, r, busy, run, minSim }) {
  const [text, setText] = useState(r.text)
  useEffect(() => setText(r.text), [r.text])
  const edit = (ch) => run(() => api.editRecording(v.id, r.file, ch))
  return (
    <li className={`vrec ${r.use ? '' : 'off'} ${r.outlier ? 'outlier' : ''}`}>
      <audio controls preload="none" src={api.voiceAudio(v.id, 'recordings', r.file)} />
      <div className="vrec-body">
        <textarea rows={2} value={text} maxLength={600} disabled={busy}
                  title="What is said in this recording — fix it if Whisper misheard (the voice uses these words)"
                  onChange={(e) => setText(e.target.value)}
                  onBlur={() => text.trim() !== r.text && edit({ text: text.trim() })} />
        <div className="vrec-meta muted small">
          <span title="Clean audio, confident words, 5-15 s">quality {Math.round(r.score)}/100</span>
          <span>{r.seconds.toFixed(1)} s</span>
          <span title="Loud vs quiet parts: under ~25 dB the room is noisy">SNR {r.snr_db} dB</span>
          {r.similarity != null && (
            <span className={r.outlier ? 'warn' : ''}
                  title={`How much it sounds like your other recordings (under ${minSim} is left out)`}>
              match {Math.round(r.similarity * 100)}%{r.outlier ? ' — left out' : ''}
            </span>
          )}
          {r.language && <span>{r.language}</span>}
          {v.built?.best === r.file && <b title="Its delivery (pace, tone) is the model's reference">★ reference</b>}
        </div>
      </div>
      <div className="vrec-ctl">
        <select value={r.style || ''} disabled={busy} onChange={(e) => edit({ style: e.target.value })}>
          {['', 'calm', 'energetic', 'hinglish', 'storytelling', 'serious'].map((s) => (
            <option key={s} value={s}>{s || 'style…'}</option>
          ))}
        </select>
        <label className="small"><input type="checkbox" checked={r.use} disabled={busy}
                                        onChange={(e) => edit({ use: e.target.checked })} /> use</label>
        <button className="x" disabled={busy} aria-label="Delete recording"
                onClick={() => window.confirm('Delete this recording?') && run(() => api.deleteRecording(v.id, r.file))}>×</button>
      </div>
    </li>
  )
}

// 🗣 Your voice: record yourself a few times → ClipForge learns the voice (no training: averaged speaker embedding of
// every recording + the best one's delivery) → type anything and hear it in your voice. Runs only when you ask.
export default function VoiceLab() {
  const [status, setStatus] = useState(null)
  const [list, setList] = useState(null)
  const [vid, setVid] = useState(null)
  const [busy, setBusy] = useState('') // what's running, shown on the button
  const [err, setErr] = useState('')
  const [style, setStyle] = useState('')
  const [line, setLine] = useState(0)
  const [say, setSay] = useState('Here is the one habit that changed how I work. Try it for a week.')
  const [lang, setLang] = useState('Auto')
  const [samples, setSamples] = useState([])
  const fileRef = useRef(null)

  const reload = () => api.voices().then((r) => {
    setList(r)
    setVid((cur) => cur ?? r.voices[0]?.id ?? null)
  })
  useEffect(() => {
    reload().catch((e) => setErr(e.message))
    api.ttsStatus().then(setStatus).catch(() => setStatus({ installed: false, reason: 'status check failed' }))
  }, [])

  const v = list?.voices.find((x) => x.id === vid)
  const run = async (fn, label = 'saving') => {
    setErr('')
    setBusy(label)
    try {
      const r = await fn()
      await reload()
      return r
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }
  const add = (files) => files.length && run(() => api.addRecordings(v.id, files, style || SCRIPT[line]?.style),
    'Cleaning + transcribing…').then(() => setLine((l) => (l + 1) % SCRIPT.length))
  const speak = async () => {
    const r = await run(() => api.speak(v.id, say, lang === 'Auto' ? '' : lang),
      v.built ? 'Speaking…' : 'Building your voice, then speaking…')
    if (r?.file) setSamples((s) => [{ ...r, text: say }, ...s].slice(0, 8))
  }

  if (!list) return <div className="voice-wrap">{err ? <div className="banner error">{err}</div> : 'Loading…'}</div>
  const used = v ? v.recordings.filter((r) => r.use) : []
  const total = used.reduce((a, r) => a + r.seconds, 0)

  return (
    <div className="voice-wrap voicelab">
      <div>
        <h2>🗣 Your voice</h2>
        <p className="muted">Record yourself a few times — ClipForge learns your voice from all of them (no training)
          and can then say anything in it. Runs on your GPU only when you ask; it never leaves your laptop. Use your
          own voice only.</p>
      </div>

      {status && !status.installed && (
        <div className="banner error">Voice engine not ready: {status.reason}</div>
      )}
      {status?.installed && (
        <p className="muted small">Engine: {status.model} on {status.gpu}. The first build downloads the model (~4 GB).
          English and 9 other languages; Hindi isn't one of them, so Hinglish lines may sound off — try it.</p>
      )}
      {err && <div className="banner error">{err}</div>}

      <div className="vl-head">
        <select value={vid || ''} onChange={(e) => setVid(e.target.value)} disabled={!list.voices.length}>
          {list.voices.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          {!list.voices.length && <option value="">No voice yet</option>}
        </select>
        <button className="btn" disabled={!!busy} onClick={() => {
          const name = window.prompt('Name this voice', list.voices.length ? 'My voice 2' : 'My voice')
          if (name) run(() => api.createVoice(name)).then((x) => x && setVid(x.id))
        }}>+ New voice</button>
        {v && <button className="btn ghost" disabled={!!busy}
                      onClick={() => window.confirm(`Delete “${v.name}” and its recordings?`)
                        && run(() => api.deleteVoice(v.id)).then(() => setVid(null))}>Delete</button>}
      </div>

      {v && (
        <>
          <div className="card vl-step">
            <h3>1 · Record ({used.length} used · {Math.round(total)} s)</h3>
            <p className="muted small">Quiet room, phone or headset mic 15–20 cm away, your normal speaking voice. Aim
              for 6–10 takes of 5–15 s in different moods (1–2 minutes in all). Long takes are fine: they're split at
              sentence ends.</p>
            <div className="vl-script">
              <span className="muted small">Read this ({SCRIPT[line].style}):</span>
              <blockquote>{SCRIPT[line].text}</blockquote>
              <button className="btn sm ghost" onClick={() => setLine((line + 1) % SCRIPT.length)}>Next line →</button>
            </div>
            <div className="row-btns">
              <Recorder disabled={!!busy} onDone={(f) => add([f])} />
              <button className="btn ghost" disabled={!!busy} onClick={() => fileRef.current?.click()}>⬆ Upload audio</button>
              <input ref={fileRef} type="file" accept="audio/*,video/*" multiple hidden
                     onChange={(e) => { add([...e.target.files]); e.target.value = '' }} />
              <select value={style} onChange={(e) => setStyle(e.target.value)} title="Tag the next takes">
                <option value="">style: as the line</option>
                {list.styles.filter(Boolean).map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
              {busy && <span className="muted small">{busy}</span>}
            </div>
            {v.recordings.length > 0 && (
              <ul className="vrecs">
                {v.recordings.map((r) => (
                  <RecordingRow key={r.file} v={v} r={r} busy={!!busy} run={run} minSim={list.min_similarity} />
                ))}
              </ul>
            )}
          </div>

          <div className="card vl-step">
            <h3>2 · Build</h3>
            <p className="muted small">Averages your voice over every used recording and takes the best one's delivery
              as the reference. Recordings that don't sound like the rest are left out.</p>
            <div className="row-btns">
              <button className="btn" disabled={!!busy || !used.length} onClick={() => run(() => api.buildVoice(v.id), 'Building your voice…')}>
                {v.built ? '↻ Rebuild' : 'Build my voice'}
              </button>
              {v.built
                ? <span className="muted small">Built from {v.built.used} recording(s) in {v.built.seconds} s
                    {v.built.peak_vram_mb ? ` · ${(v.built.peak_vram_mb / 1024).toFixed(1)} GB GPU` : ''}</span>
                : used.length > 0 && <span className="muted small">Not built yet (or recordings changed)</span>}
            </div>
          </div>

          <div className="card vl-step">
            <h3>3 · Try it</h3>
            <textarea rows={3} value={say} maxLength={2000} onChange={(e) => setSay(e.target.value)} />
            <div className="row-btns">
              <select value={lang} onChange={(e) => setLang(e.target.value)}>
                {LANGS.map((l) => <option key={l}>{l}</option>)}
              </select>
              <button className="primary" disabled={!!busy || !used.length || !say.trim()} onClick={speak}>
                ▶ Say it in my voice
              </button>
            </div>
            {samples.map((s) => (
              <div key={s.file} className="vl-sample">
                <audio controls autoPlay={s === samples[0]} src={api.voiceAudio(v.id, 'samples', s.file)} />
                <span className="muted small">{s.seconds} s of speech in {s.took} s — “{s.text.slice(0, 80)}”</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
