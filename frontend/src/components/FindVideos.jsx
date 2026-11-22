import { useRef, useState } from 'react'
import { api } from '../api.js'
import { fmt } from '../util.js'

const LENGTHS = [
  ['8-30', '8–30 min', 8, 30],
  ['30-90', '30–90 min', 30, 90],
  ['8-90', 'Any (8–90)', 8, 90],
]
const PARALLEL = 4 // captions fetched at once (each ~3 s)

const compact = (n) => (n == null ? '' : Intl.NumberFormat(undefined, { notation: 'compact' }).format(n))
const moment = (url, t) => `${url}&t=${t}s`

function scoreClass(s) {
  if (s == null) return 'none'
  return s >= 70 ? 'hi' : s >= 40 ? 'mid' : 'lo'
}

// New job → "Find videos": YouTube search for a topic, long-form only, optional Creative Commons filter,
// each result scored by its captions (how much of it is about the topic, and where)
export default function FindVideos({ initialTopic = '', onUse }) {
  const [topic, setTopic] = useState(initialTopic)
  const [cc, setCc] = useState(true)
  const [len, setLen] = useState('8-90')
  const [results, setResults] = useState(null)
  const [scores, setScores] = useState({}) // id -> score result | {error}
  const [state, setState] = useState('') // '' | searching | scoring | done
  const [err, setErr] = useState('')
  const run = useRef(0) // a newer search cancels the scoring of an older one

  const search = async () => {
    const t = topic.trim()
    if (!t) return
    const my = ++run.current
    const [, , lo, hi] = LENGTHS.find((l) => l[0] === len)
    setErr('')
    setResults(null)
    setScores({})
    setState('searching')
    let found
    try {
      found = (await api.discover(t, cc, lo, hi)).results
    } catch (e) {
      if (my === run.current) { setErr(e.message); setState('') }
      return
    }
    if (my !== run.current) return
    setResults(found)
    setState('scoring')
    let next = 0
    const worker = async () => {
      while (next < found.length && my === run.current) {
        const r = found[next++]
        let s
        try { s = await api.discoverScore(r.id, t) } catch (e) { s = { error: e.message } }
        if (my === run.current) setScores((x) => ({ ...x, [r.id]: s }))
      }
    }
    await Promise.all(Array.from({ length: PARALLEL }, worker))
    if (my === run.current) setState('done')
  }

  const scored = Object.keys(scores).length
  const list = results && (state === 'done'
    ? [...results].sort((a, b) => (scores[b.id]?.score ?? -1) - (scores[a.id]?.score ?? -1))
    : results)

  return (
    <div className="finder">
      <div className="finder-bar">
        <input value={topic} maxLength={120} autoFocus placeholder="Topic, e.g. salary negotiation, startup fundraising"
               onChange={(e) => setTopic(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); search() } }} />
        <button type="button" className="primary" disabled={!topic.trim() || state === 'searching'} onClick={search}>
          {state === 'searching' ? 'Searching…' : '🔎 Search'}
        </button>
      </div>
      <div className="finder-opts">
        <label className="checkline">
          <input type="checkbox" checked={cc} onChange={(e) => setCc(e.target.checked)} />
          <span>Creative Commons only <span className="muted">· reusable with credit; other videos risk copyright claims,
            which block Shorts</span></span>
        </label>
        <div className="seg sm">
          {LENGTHS.map(([k, label]) => (
            <button type="button" key={k} className={len === k ? 'on' : ''} onClick={() => setLen(k)}>{label}</button>
          ))}
        </div>
      </div>

      {err && <p className="err">{err}</p>}
      {state === 'scoring' && (
        <p className="muted small">Reading captions to score each video for “{topic.trim()}” — {scored}/{results.length}…</p>
      )}
      {state === 'done' && <p className="muted small">Sorted by how much of each video is about your topic.</p>}
      {list && list.length === 0 && (
        <p className="muted">No long videos found{cc ? ' with a Creative Commons licence' : ''}. Try a broader topic
          {cc ? ' or untick Creative Commons' : ''}.</p>
      )}

      <div className="finds">
        {(list || []).map((r) => {
          const s = scores[r.id]
          return (
            <div key={r.id} className="find">
              <a href={r.url} target="_blank" rel="noreferrer" className="find-thumb">
                <img src={r.thumbnail} alt="" loading="lazy" />
                <span>{fmt(r.duration)}</span>
              </a>
              <div className="find-body">
                <a href={r.url} target="_blank" rel="noreferrer" className="find-title">{r.title}</a>
                <span className="muted small">
                  {r.channel} · {compact(s?.views ?? r.views)} views{s?.upload_date && ` · ${s.upload_date.slice(0, 4)}`}
                  {s?.language && ` · ${s.language}`}
                </span>
                <span className="find-badges">
                  {s && !s.error && (s.score == null
                    ? <span className="fbadge none">no captions — can't score</span>
                    : <span className={`fbadge ${scoreClass(s.score)}`} title={`${s.hits} mentions · on topic in ${Math.round(s.coverage * 100)}% of its minutes`}>
                        topic {s.score}/100
                      </span>)}
                  {!s && <span className="fbadge none">scoring…</span>}
                  {s?.error && <span className="fbadge none" title={s.error}>couldn't read</span>}
                  {s?.cc && <span className="fbadge cc">CC licence</span>}
                  {s && !s.error && r.cc_search && !s.cc && <span className="fbadge warn">licence not CC</span>}
                  {r.done > 0 && <span className="fbadge warn">⚠ done {r.done}×</span>}
                </span>
                {s?.moments?.length > 0 && (
                  <span className="find-moments">
                    {s.moments.map((m) => (
                      <a key={m.t} href={moment(r.url, m.t)} target="_blank" rel="noreferrer" title={m.text}>
                        ▶ {fmt(m.t)} <span className="muted">{m.text.slice(0, 48)}…</span>
                      </a>
                    ))}
                  </span>
                )}
              </div>
              <button type="button" className="btn" onClick={() => onUse(r.url, topic.trim())}>Use this video</button>
            </div>
          )
        })}
      </div>
    </div>
  )
}
