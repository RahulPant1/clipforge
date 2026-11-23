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

function RecordingRow({ v, r, busy, run }) {
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
                  title="How close it sounds to your other recordings — a take clearly below the rest is left out of the voice">
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

const when = (t) => (t ? new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—')

function NewVoice({ busy, onCreate, onCancel }) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [consent, setConsent] = useState(false)
  return (
    <div className="card vl-new">
      <h3>New voice</h3>
      <input placeholder="Name, e.g. My voice, Calm narrator" maxLength={60} value={name} onChange={(e) => setName(e.target.value)} />
      <input placeholder="Description (optional), e.g. my normal speaking voice, headset mic" maxLength={300}
             value={desc} onChange={(e) => setDesc(e.target.value)} />
      <label className="small"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />{' '}
        This is my voice, or its owner agreed to it being cloned</label>
      <div className="row-btns">
        <button className="primary" disabled={busy || !name.trim() || !consent} onClick={() => onCreate(name, desc)}>Create</button>
        <button className="btn ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

function Profile({ v, busy, run, languages }) {
  const [name, setName] = useState(v.name)
  const [desc, setDesc] = useState(v.description || '')
  useEffect(() => { setName(v.name); setDesc(v.description || '') }, [v.id, v.name, v.description])
  const save = (ch) => run(() => api.updateVoice(v.id, ch))
  return (
    <div className="card vl-profile">
      <input className="vl-name" value={name} maxLength={60} disabled={busy} aria-label="Voice name"
             onChange={(e) => setName(e.target.value)} onBlur={() => name.trim() && name !== v.name && save({ name })} />
      <input value={desc} maxLength={300} disabled={busy} placeholder="Add a description" aria-label="Description"
             onChange={(e) => setDesc(e.target.value)} onBlur={() => desc !== (v.description || '') && save({ description: desc })} />
      <div className="vl-meta muted small">
        <span>Created {when(v.created)}</span>
        <span>Updated {when(v.updated)}</span>
        <span>{v.built ? <>Current: <b>v{v.built.version}</b> · {v.built.model}</> : 'Not built yet'}</span>
        {v.dirty && v.built && <span className="warn">Recordings changed since v{v.built.version} — build again for a new version</span>}
        <label>Language{' '}
          <select value={v.language || 'Auto'} disabled={busy} onChange={(e) => save({ language: e.target.value })}>
            {languages.map((l) => <option key={l}>{l}</option>)}
          </select>
        </label>
      </div>
    </div>
  )
}

function Versions({ v, busy, run, sampleText }) {
  const [text, setText] = useState('')
  const builds = [...(v.builds || [])].reverse()
  const live = builds.filter((b) => b.prompt)
  const [a, setA] = useState(null)
  const [b, setB] = useState(null)
  const [line, setLine] = useState(sampleText)
  const [pair, setPair] = useState(null)
  const va = a ?? live[1]?.version
  const vb = b ?? live[0]?.version
  const compare = async () => {
    const r = await run(() => api.compareVoice(v.id, line, [va, vb]), `Speaking v${va} and v${vb}…`)
    if (r?.items) setPair({ text: line, items: r.items })
  }
  return (
    <>
      <div className="card vl-step">
        <h3>2 · Build a version</h3>
        <p className="muted small">Averages the voice over every used recording and takes the best one's pace and tone
          as the reference — a <b>reference voice prompt</b>, cached for reuse. The model itself is not trained or
          changed. Each build is kept as a new version with a test sample, so you can hear what new recordings did.</p>
        <div className="row-btns">
          <button className="btn" disabled={busy || !v.recordings.some((r) => r.use)}
                  onClick={() => run(() => api.buildVoice(v.id, '', text), 'Building a new version…')}>
            {v.built ? `Build v${v.built.version + 1}` : 'Build v1'}
          </button>
          <input className="grow" value={text} maxLength={300} placeholder={`Test sentence (default: “${sampleText}”)`}
                 onChange={(e) => setText(e.target.value)} />
        </div>
        {builds.length > 0 && (
          <ul className="vl-versions">
            {builds.map((x) => (
              <li key={x.version} className={x.prompt ? '' : 'gone'}>
                <b>v{x.version}</b>
                <span className="muted small">{when(x.at)} · {x.used} recording{x.used === 1 ? '' : 's'}
                  {x.best && <> · reference {x.best}</>}{!x.prompt && ' · removed (too old)'}</span>
                {x.sample
                  ? <audio controls preload="none" src={api.voiceAudio(v.id, 'samples', x.sample)} />
                  : <span className="muted small">no sample</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      {live.length >= 2 && (
        <div className="card vl-step">
          <h3>Compare versions</h3>
          <div className="row-btns">
            <select value={va} onChange={(e) => setA(Number(e.target.value))}>
              {live.map((x) => <option key={x.version} value={x.version}>v{x.version}</option>)}
            </select>
            <span className="muted">vs</span>
            <select value={vb} onChange={(e) => setB(Number(e.target.value))}>
              {live.map((x) => <option key={x.version} value={x.version}>v{x.version}</option>)}
            </select>
            <input className="grow" value={line} maxLength={600} onChange={(e) => setLine(e.target.value)} />
            <button className="btn" disabled={busy || va === vb || !line.trim()} onClick={compare}>▶ Compare</button>
          </div>
          {pair && (
            <div className="vl-pair">
              {pair.items.map((it) => (
                <div key={it.file}><b>v{it.version}</b> <span className="muted small">{it.seconds} s</span>
                  <audio controls src={api.voiceAudio(v.id, 'samples', it.file)} /></div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  )
}

// 🗣 Voice Library: several voice profiles (yours, or others with consent). Recordings → a versioned reference voice
// prompt (no training) → type anything and hear it in that voice. Runs on the GPU only when asked.
export default function VoiceLab() {
  const [status, setStatus] = useState(null)
  const [list, setList] = useState(null)
  const [vid, setVid] = useState(null)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState('') // what's running, shown next to the buttons
  const [err, setErr] = useState('')
  const [style, setStyle] = useState('')
  const [line, setLine] = useState(0)
  const [say, setSay] = useState('Here is the one habit that changed how I work. Try it for a week.')
  const [lang, setLang] = useState('Auto')
  const [ver, setVer] = useState('') // '' = newest
  const [samples, setSamples] = useState([])
  const fileRef = useRef(null)

  const reload = () => api.voices().then((r) => {
    setList(r)
    setVid((cur) => (cur && r.voices.some((x) => x.id === cur) ? cur : r.voices[0]?.id ?? null))
  })
  useEffect(() => {
    reload().catch((e) => setErr(e.message))
    api.ttsStatus().then(setStatus).catch(() => setStatus({ installed: false, reason: 'status check failed' }))
  }, [])

  const v = list?.voices.find((x) => x.id === vid)
  const run = async (fn, label = 'Saving…') => {
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
    const r = await run(() => api.speak(v.id, say, lang === 'Auto' ? '' : lang, ver === '' ? null : Number(ver)),
      v.built ? 'Speaking…' : 'Building v1, then speaking…')
    if (r?.file) setSamples((s) => [{ ...r, text: say, version: ver || r.voice?.built?.version }, ...s].slice(0, 8))
  }

  if (!list) return <div className="voice-wrap">{err ? <div className="banner error">{err}</div> : 'Loading…'}</div>
  const used = v ? v.recordings.filter((r) => r.use) : []
  const total = used.reduce((a, r) => a + r.seconds, 0)

  return (
    <div className="voice-wrap voicelab">
      <div>
        <h2>🗣 Voice Library</h2>
        <p className="muted">Voices made from recordings — yours, or anyone who agreed. Each voice is a reference-based
          clone (no training): add recordings, build a new version, test and compare. Everything stays on your laptop
          and the GPU is only used while it speaks. Audio made here is AI-generated — label it as such when you publish.</p>
      </div>

      {status && !status.installed && <div className="banner error">Voice engine not ready: {status.reason}</div>}
      {status?.installed && (
        <p className="muted small">Engine: {status.model} on {status.gpu}. English and 9 other languages; Hindi isn't one
          of them, so Hinglish lines may sound off.</p>
      )}
      {err && <div className="banner error">{err}</div>}

      <div className="vl-head">
        <select value={vid || ''} onChange={(e) => { setVid(e.target.value); setSamples([]); setVer('') }}
                disabled={!list.voices.length} aria-label="Voice">
          {list.voices.map((x) => <option key={x.id} value={x.id}>{x.name}{x.built ? ` (v${x.built.version})` : ''}</option>)}
          {!list.voices.length && <option value="">No voices yet</option>}
        </select>
        <button className="btn" disabled={!!busy} onClick={() => setCreating(true)}>+ New voice</button>
        {v && <button className="btn ghost" disabled={!!busy}
                      onClick={() => window.confirm(`Delete “${v.name}”, its recordings and all versions?`)
                        && run(() => api.deleteVoice(v.id)).then(() => setVid(null))}>Delete</button>}
        {busy && <span className="muted small">{busy}</span>}
      </div>

      {creating && (
        <NewVoice busy={!!busy} onCancel={() => setCreating(false)}
                  onCreate={(name, desc) => run(() => api.createVoice(name, desc, true)).then((x) => {
                    if (x) { setVid(x.id); setCreating(false) }
                  })} />
      )}

      {v && (
        <>
          <Profile v={v} busy={!!busy} run={run} languages={list.languages || ['Auto']} />

          <div className="card vl-step">
            <h3>1 · Recordings ({used.length} used · {Math.round(total)} s)</h3>
            <p className="muted small">Quiet room, mic 15–20 cm away, a normal speaking voice. 6–10 takes of 5–15 s in
              different moods (1–2 minutes in all). Add more any time, then build a new version. Long takes are split at
              sentence ends.</p>
            <div className="vl-script">
              <span className="muted small">Read this ({SCRIPT[line].style}):</span>
              <blockquote>{SCRIPT[line].text}</blockquote>
              <button className="btn sm ghost" onClick={() => setLine((line + 1) % SCRIPT.length)}>Next line →</button>
            </div>
            <div className="row-btns">
              <Recorder disabled={!!busy} onDone={(f) => add([f])} />
              <button className="btn ghost" disabled={!!busy} onClick={() => fileRef.current?.click()}>⬆ Import audio</button>
              <input ref={fileRef} type="file" accept="audio/*,video/*" multiple hidden
                     onChange={(e) => { add([...e.target.files]); e.target.value = '' }} />
              <select value={style} onChange={(e) => setStyle(e.target.value)} title="Tag the next takes">
                <option value="">style: as the line</option>
                {list.styles.filter(Boolean).map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            {v.recordings.length > 0 && (
              <ul className="vrecs">
                {v.recordings.map((r) => (
                  <RecordingRow key={r.file} v={v} r={r} busy={!!busy} run={run} />
                ))}
              </ul>
            )}
          </div>

          <Versions key={v.id} v={v} busy={!!busy} run={run} sampleText={list.sample_text || ''} />

          <div className="card vl-step">
            <h3>3 · Try it</h3>
            <textarea rows={3} value={say} maxLength={2000} onChange={(e) => setSay(e.target.value)} />
            <div className="row-btns">
              <select value={ver} onChange={(e) => setVer(e.target.value)} title="Which version speaks">
                <option value="">newest version</option>
                {[...(v.builds || [])].reverse().filter((b) => b.prompt).map((b) => (
                  <option key={b.version} value={b.version}>v{b.version}</option>
                ))}
              </select>
              <select value={lang} onChange={(e) => setLang(e.target.value)}>
                {(list.languages || LANGS).map((l) => <option key={l}>{l}</option>)}
              </select>
              <button className="primary" disabled={!!busy || !used.length || !say.trim()} onClick={speak}>
                ▶ Say it in this voice
              </button>
            </div>
            {samples.map((s) => (
              <div key={s.file} className="vl-sample">
                <audio controls autoPlay={s === samples[0]} src={api.voiceAudio(v.id, 'samples', s.file)} />
                <span className="muted small">🤖 AI voice{s.version ? ` v${s.version}` : ''} · {s.seconds} s of speech in
                  {' '}{s.took} s — “{s.text.slice(0, 80)}”</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
