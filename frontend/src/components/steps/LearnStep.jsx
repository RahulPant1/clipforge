import { useEffect, useRef, useState } from 'react'
import { api } from '../../api.js'
import { fmt } from '../../util.js'

// YouTube chapter lines: "0:00 Title", hours when the video is that long
const stamp = (t) => {
  const s = Math.floor(t)
  return s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}` : fmt(s)
}

function Quiz({ quiz, seek }) {
  const [picked, setPicked] = useState({}) // question index → chosen choice index
  const right = quiz.filter((q, i) => picked[i] === q.answer).length
  const done = Object.keys(picked).length
  return (
    <div className="quiz">
      {quiz.map((q, i) => {
        const p = picked[i]
        return (
          <div key={i} className="qz">
            <p><b>{i + 1}.</b> {q.q}</p>
            <div className="qz-choices">
              {q.choices.map((c, k) => (
                <button key={k} disabled={p != null}
                        className={p == null ? '' : k === q.answer ? 'right' : k === p ? 'wrong' : 'dim'}
                        onClick={() => setPicked({ ...picked, [i]: k })}>
                  {'ABCDE'[k]}) {c}
                </button>
              ))}
            </div>
            {p != null && (
              <p className="small">
                {p === q.answer ? '✓ Right. ' : '✕ Not quite. '}{q.explain}{' '}
                <a onClick={() => seek(q.t)}>Watch at {fmt(q.t)} →</a>
              </p>
            )}
          </div>
        )
      })}
      {done > 0 && (
        <p className="muted small">{right}/{done} right{done < quiz.length ? ` — ${quiz.length - done} to go` : ''}
          {done === quiz.length && <> · <a onClick={() => setPicked({})}>try again</a></>}</p>
      )}
    </div>
  )
}

// 📚 Learn: study notes from the video — every point jumps to where it's said
export default function LearnStep({ job, busy, act }) {
  const [data, setData] = useState(null)
  const [copied, setCopied] = useState(false)
  const [view, setView] = useState('Summary')
  const player = useRef(null)
  useEffect(() => {
    api.learnNotes(job.id).then(setData).catch(() => setData({ notes: null }))
  }, [job.id, job.updated_at])

  const seek = (t) => {
    const v = player.current
    if (!v) return
    v.currentTime = t
    v.play().catch(() => {})
    v.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }
  const At = ({ t }) => <button className="ts" onClick={() => seek(t)} title="Play from here">{fmt(t)}</button>

  if (!data) return <div className="card muted">Loading study notes…</div>
  const n = data.notes
  if (!n) {
    return (
      <div className="card">
        <p className="muted">No study notes yet.</p>
        <button className="primary" disabled={busy} onClick={() => act(() => api.makeLearn(job.id)).catch(() => {})}>
          📚 Make study notes
        </button>
      </div>
    )
  }
  const chapters = n.chapters.map((c) => `${stamp(c.t)} ${c.title}`).join('\n')
  const copy = async () => {
    try { await navigator.clipboard.writeText(chapters); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* ignore */ }
  }

  return (
    <div className="learn learn-desktop">
      <div className="learn-top">
        <video ref={player} src={api.fileUrl(job.id, 'source.mp4')} controls preload="metadata" />
        <div className="card">
          <div className="learn-h"><h3>Chapters</h3>
            <button className="btn sm ghost" onClick={copy} title="YouTube description format">{copied ? 'Copied' : 'Copy for YouTube'}</button>
          </div>
          <ul className="learn-list">{n.chapters.map((c, i) => <li key={i}><At t={c.t} /> {c.title}</li>)}</ul>
        </div>
      </div>
      <div className="learn-study form">
        <nav className="row-btns" aria-label="Study views">
          {['Summary', 'Notes', 'Quiz'].map((tab) => <button key={tab} type="button" className="btn ghost"
            disabled={tab === 'Quiz' && !n.quiz.length} aria-pressed={view === tab}
            onClick={() => setView(tab)}>{tab}</button>)}
        </nav>
        <div className="form" hidden={view !== 'Summary'}>
        <div className="card">
          <h3>Summary</h3>
          <p>{n.summary || <span className="muted">No summary (the summary step failed — notes below are per part).</span>}</p>
          <div className="row-btns">
            <a className="btn" href={api.fileUrl(job.id, 'notes.md', Date.parse(n.made_at))} download="notes.md">⬇ notes.md</a>
            <button className="btn ghost" disabled={busy}
                    onClick={() => window.confirm('Write the study notes again? These are replaced.')
                      && act(() => api.makeLearn(job.id)).catch(() => {})}>↻ Again</button>
          </div>
          <p className="muted small">By {n.model || 'Gemma'} from the transcript — check anything important against the video.</p>
        </div>
        <div className="card">
          <h3>Key concepts</h3>
          <ul className="learn-list">
            {n.concepts.map((c, i) => <li key={i}><At t={c.t} /> <b>{c.name}</b> — {c.explanation}</li>)}
          </ul>
        </div>
      </div>

      <div className="form" hidden={view !== 'Notes'}>
      <div className="card">
        <h3>Notes</h3>
        <ul className="learn-list">{n.notes.map((x, i) => <li key={i}><At t={x.t} /> <b>{x.title}</b> — {x.note}</li>)}</ul>
      </div>

      <div className="learn-grid">
        {n.hands_on.length > 0 && (
          <div className="card"><h3>Try it yourself</h3><ul>{n.hands_on.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
        )}
        {n.next_steps.length > 0 && (
          <div className="card"><h3>Learn next</h3><ul>{n.next_steps.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
        )}
      </div>
      </div>
      {n.quiz.length > 0 && (
        <div className="card" hidden={view !== 'Quiz'}>
          <h3>Quiz</h3>
          <Quiz quiz={n.quiz} seek={seek} />
        </div>
      )}

      </div>
    </div>
  )
}
