import { useRef, useState } from 'react'
import { api } from '../api.js'
import { fmt } from '../util.js'

const LENGTHS = [
  ['8-30', '8–30 min', 8, 30],
  ['30-90', '30–90 min', 30, 90],
  ['8-90', 'Any (8–90)', 8, 90],
]
const PARALLEL = 4 // captions fetched at once (each ~3 s)
const JUDGE_TOP = 6 // smart search: Gemma reads this many of the best keyword matches

const compact = (n) => (n == null ? '' : Intl.NumberFormat(undefined, { notation: 'compact' }).format(n))
const moment = (url, t) => `${url}&t=${t}s`

function scoreClass(s) {
  if (s == null) return 'none'
  return s >= 70 ? 'hi' : s >= 40 ? 'mid' : 'lo'
}

// New job → "Find videos": YouTube search for a topic, long-form only, optional Creative Commons filter,
// each result scored by its captions (how much of it is about the topic, and where)
export default function FindVideos({ initialTopic = '', onUse, onQueue }) {
  const [topic, setTopic] = useState(initialTopic)
  const [cc, setCc] = useState(true)
  const [smart, setSmart] = useState(false) // Gemma: better searches + judges the best moments
  const [queries, setQueries] = useState([]) // what smart search actually searched
  const [judged, setJudged] = useState({}) // id -> {relevance, why, best_t} | {error}
  const [note, setNote] = useState('')
  const [len, setLen] = useState('8-90')
  const [results, setResults] = useState(null)
  const [scores, setScores] = useState({}) // id -> score result | {error}
  const [state, setState] = useState('') // '' | searching | scoring | done
  const [err, setErr] = useState('')
  const run = useRef(0) // a newer search cancels the scoring of an older one
  const [picked, setPicked] = useState({}) // id -> result, ticked for the batch queue
  const pick = (r) => setPicked((x) => { const y = { ...x }; if (y[r.id]) delete y[r.id]; else y[r.id] = r; return y })
  const picks = Object.values(picked)

  const search = async () => {
    const t = topic.trim()
    if (!t) return
    const my = ++run.current
    const [, , lo, hi] = LENGTHS.find((l) => l[0] === len)
    setErr('')
    setNote('')
    setResults(null)
    setScores({})
    setJudged({})
    setQueries([])
    let qs = []
    if (smart) {
      setState('expanding')
      try {
        qs = (await api.discoverExpand(t)).queries
        if (my !== run.current) return
        setQueries(qs)
      } catch (e) {
        if (my !== run.current) return
        setNote(`Smart search unavailable (${e.message}) — plain search instead.`)
      }
    }
    setState('searching')
    let found
    try {
      found = (await api.discover(t, cc, lo, hi, qs)).results
    } catch (e) {
      if (my === run.current) { setErr(e.message); setState('') }
      return
    }
    if (my !== run.current) return
    setResults(found)
    setState('scoring')
    let next = 0
    const done = {}
    const collect = async () => {
      while (next < found.length && my === run.current) {
        const r = found[next++]
        let s
        try { s = await api.discoverScore(r.id, t) } catch (e) { s = { error: e.message } }
        done[r.id] = s
        if (my === run.current) setScores((x) => ({ ...x, [r.id]: s }))
      }
    }
    await Promise.all(Array.from({ length: PARALLEL }, collect))
    if (my !== run.current) return
    if (qs.length) { // Gemma reads the best keyword matches, one at a time (it holds the GPU)
      const top = found.filter((r) => done[r.id]?.score > 0)
        .sort((a, b) => done[b.id].score - done[a.id].score).slice(0, JUDGE_TOP)
      setState('judging')
      for (const r of top) {
        if (my !== run.current) return
        let v
        try { v = await api.discoverJudge(r.id, t) } catch (e) { v = { error: e.message } }
        if (my === run.current) setJudged((x) => ({ ...x, [r.id]: v }))
      }
    }
    if (my === run.current) setState('done')
  }

  const scored = Object.keys(scores).length
  const rank = (r) => [judged[r.id]?.relevance ?? -1, scores[r.id]?.score ?? -1] // Gemma's verdict first
  const list = results && (state === 'done'
    ? [...results].sort((a, b) => { const [x, y] = [rank(a), rank(b)]; return y[0] - x[0] || y[1] - x[1] })
    : results)
  const busy = ['expanding', 'searching'].includes(state)

  return (
    <div className="finder">
      <div className="finder-bar">
        <input value={topic} maxLength={120} autoFocus placeholder="Topic, e.g. salary negotiation, startup fundraising"
               onChange={(e) => setTopic(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); search() } }} />
        <button type="button" className="primary" disabled={!topic.trim() || busy} onClick={search}>
          {busy ? 'Searching…' : '🔎 Search'}
        </button>
      </div>
      <div className="finder-opts">
        <label className="checkline">
          <input type="checkbox" checked={cc} onChange={(e) => setCc(e.target.checked)} />
          <span>Creative Commons only <span className="muted">· reusable with credit; other videos risk copyright claims,
            which block Shorts</span></span>
        </label>
        <label className="checkline">
          <input type="checkbox" checked={smart} onChange={(e) => setSmart(e.target.checked)} />
          <span>Smart search with Gemma <span className="muted">· better searches + Gemma reads the best moments of the
            top {JUDGE_TOP}. Uses the GPU (waits while a job runs); the first use loads Gemma, ~2 min</span></span>
        </label>
        <div className="seg sm">
          {LENGTHS.map(([k, label]) => (
            <button type="button" key={k} className={len === k ? 'on' : ''} onClick={() => setLen(k)}>{label}</button>
          ))}
        </div>
      </div>

      {err && <p className="err">{err}</p>}
      {note && <p className="hint warn">{note}</p>}
      {state === 'expanding' && <p className="muted small">Gemma is writing searches for “{topic.trim()}” (first use loads the model — up to ~2 min)…</p>}
      {queries.length > 0 && (
        <p className="muted small">Searched: {queries.map((q) => <span key={q} className="fbadge">{q}</span>)}</p>
      )}
      {state === 'judging' && (
        <p className="muted small">Gemma is reading the best moments — {Object.keys(judged).length}/
          {Math.min(JUDGE_TOP, Object.values(scores).filter((s) => s?.score > 0).length)}…</p>
      )}
      {state === 'scoring' && (
        <p className="muted small">Reading captions to score each video for “{topic.trim()}” — {scored}/{results.length}…</p>
      )}
      {state === 'done' && (
        <p className="muted small">
          {Object.keys(judged).length ? "Sorted by Gemma's rating, then by how much of each video is about your topic."
            : 'Sorted by how much of each video is about your topic.'}
        </p>
      )}
      {list && list.length === 0 && (
        <p className="muted">No long videos found{cc ? ' with a Creative Commons licence' : ''}. Try a broader topic
          {cc ? ' or untick Creative Commons' : ''}.</p>
      )}

      {picks.length > 0 && (
        <div className="find-queue">
          <b>{picks.length} ticked</b>
          <button type="button" className="linkbtn" onClick={() => setPicked({})}>untick</button>
          <span className="grow" />
          <button type="button" className="primary" onClick={() => onQueue(picks.map((r) => ({
            id: r.id, url: r.url, title: r.title, channel: r.channel, duration: r.duration, done: r.done,
            from: `search “${topic.trim()}”` })), topic.trim())}>
            Queue {picks.length} as a batch →
          </button>
        </div>
      )}

      <div className="finds">
        {(list || []).map((r) => {
          const s = scores[r.id]
          const g = judged[r.id]
          return (
            <div key={r.id} className={`find ${picked[r.id] ? 'picked' : ''}`}>
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
                  {g?.relevance != null && (
                    <span className={`fbadge gemma ${g.relevance >= 7 ? 'hi' : g.relevance >= 4 ? 'mid' : 'lo'}`}>
                      Gemma {g.relevance}/10
                    </span>
                  )}
                  {g?.error && <span className="fbadge none" title={g.error}>Gemma couldn't judge</span>}
                  {queries.length > 1 && r.found_by && <span className="muted small">via “{r.found_by}”</span>}
                </span>
                {g?.why && <span className="find-why">💬 {g.why}</span>}
                {s?.moments?.length > 0 && (
                  <span className="find-moments">
                    {s.moments.map((m) => (
                      <a key={m.t} href={moment(r.url, m.t)} target="_blank" rel="noreferrer" title={m.text}
                         className={g?.best_t === m.t ? 'best' : ''}>
                        {g?.best_t === m.t ? '★' : '▶'} {fmt(m.t)} <span className="muted">{m.text.slice(0, 48)}…</span>
                      </a>
                    ))}
                  </span>
                )}
              </div>
              <span className="find-acts">
                <button type="button" className="btn" onClick={() => onUse(r, topic.trim())}>Use this video</button>
                <label className="checkline small" title="Tick several, then queue them all as one batch">
                  <input type="checkbox" checked={!!picked[r.id]} onChange={() => pick(r)} /> batch
                </label>
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
