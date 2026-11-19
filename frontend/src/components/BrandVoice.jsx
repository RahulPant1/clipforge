import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { PLATFORMS } from '../util.js'

// Your own posts → few-shot examples, so Content Studio writes like you. One profile for all jobs.
export default function BrandVoice() {
  const [info, setInfo] = useState(null) // server response: voice + limits
  const [voice, setVoice] = useState(null) // edited copy
  const [sugs, setSugs] = useState({})
  const [platform, setPlatform] = useState('linkedin')
  const [state, setState] = useState('') // '' | saving | saved
  const [err, setErr] = useState('')

  useEffect(() => {
    api.brandVoice().then((r) => {
      setInfo(r)
      setVoice(r.voice)
    }).catch((e) => setErr(e.message))
    api.voiceSuggestions().then(setSugs).catch(() => setSugs({}))
  }, [])

  if (!voice) return <div className="voice-wrap">{err ? <div className="banner error">{err}</div> : 'Loading…'}</div>

  const dirty = JSON.stringify(voice) !== JSON.stringify(info.voice)
  const list = voice.examples[platform] || []
  const full = list.length >= info.max_examples
  const set = (patch) => {
    setVoice({ ...voice, ...patch })
    setState('')
  }
  const setList = (l) => set({ examples: { ...voice.examples, [platform]: l } })
  const offered = (sugs[platform] || []).filter((s) => !list.includes(s))

  const save = async () => {
    setState('saving')
    setErr('')
    try {
      const r = await api.saveBrandVoice(voice)
      setInfo(r)
      setVoice(r.voice)
      setState('saved')
    } catch (e) {
      setErr(e.message)
      setState('')
    }
  }

  return (
    <div className="voice-wrap">
      <div className="step-head">
        <div>
          <h2>Brand voice</h2>
          <p className="muted">
            Paste 3–5 of your best posts per platform. Gemma copies how you write — tone, rhythm, casing, emoji and
            hashtag habits — never their topics or facts; those still come only from the clip. Used for every new
            post and <b>🎲 New angle</b>, in all jobs. A platform with no examples borrows a couple from the others.
          </p>
        </div>
      </div>

      {err && <div className="banner error">{err}</div>}

      <label className="voice-toggle">
        <input type="checkbox" checked={voice.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
        Write posts in my voice
      </label>

      <label className="fl">
        <span>Voice notes <span className="muted">· optional, replaces the default voice line</span></span>
        <input value={voice.notes} placeholder={info.default_notes} maxLength={1000}
               onChange={(e) => set({ notes: e.target.value })} />
      </label>

      <div className="ptabs">
        {PLATFORMS.map((p) => (
          <button key={p.key} className={platform === p.key ? 'on' : ''} onClick={() => setPlatform(p.key)}>
            <span className="picon">{p.icon}</span> {p.label}
            <span className="cnt">{(voice.examples[p.key] || []).length}</span>
          </button>
        ))}
      </div>

      <div className="voice-list">
        {list.map((t, i) => (
          <div key={i} className="voice-ex">
            <div className="fl-head">
              <span className="muted">Example {i + 1}</span>
              <span className="muted small">{t.length}/{info.max_chars}</span>
              <button className="btn sm ghost" onClick={() => setList(list.filter((_, j) => j !== i))}>Remove</button>
            </div>
            <textarea rows={Math.min(12, Math.max(4, t.split('\n').length + 1))} value={t} maxLength={info.max_chars}
                      onChange={(e) => setList(list.map((x, j) => (j === i ? e.target.value : x)))} />
          </div>
        ))}
        {!list.length && <p className="muted">No {PLATFORMS.find((p) => p.key === platform).label} examples yet.</p>}
        <button className="btn" disabled={full} onClick={() => setList([...list, ''])}>
          {full ? `Up to ${info.max_examples} examples` : '+ Paste a post'}
        </button>
      </div>

      {offered.length > 0 && (
        <div className="voice-sugs">
          <p className="hint">From posts you edited by hand or marked posted:</p>
          {offered.map((s, i) => (
            <div key={i} className="voice-sug">
              <p>{s}</p>
              <button className="btn sm" disabled={full} onClick={() => setList([...list, s])}>+ Use</button>
            </div>
          ))}
        </div>
      )}

      <div className="footer-bar">
        <span className="muted">{state === 'saved' && !dirty ? '✓ Saved — new posts use it' : dirty ? 'Unsaved changes' : ''}</span>
        <button className="primary" disabled={!dirty || state === 'saving'} onClick={save}>
          {state === 'saving' ? 'Saving…' : 'Save voice'}
        </button>
      </div>
    </div>
  )
}
