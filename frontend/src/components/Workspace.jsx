import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { defaultStep, isBusy, overallProgress, stepStates } from '../util.js'
import LearnStep from './steps/LearnStep.jsx'
import PickStep from './steps/PickStep.jsx'
import PostsStep from './steps/PostsStep.jsx'
import PublishStep from './steps/PublishStep.jsx'
import ShortsStep from './steps/ShortsStep.jsx'
import SourceStep from './steps/SourceStep.jsx'
import TranscriptStep from './steps/TranscriptStep.jsx'

const STEPS = [
  { key: 'source', label: 'Source', hint: 'video in' },
  { key: 'transcript', label: 'Transcript', hint: 'read & add clips' },
  { key: 'pick', label: 'Pick clips', hint: 'trim & approve' },
  { key: 'shorts', label: 'Shorts', hint: 'look & re-render' },
  { key: 'posts', label: 'Posts', hint: 'edit copy' },
  { key: 'publish', label: 'Publish', hint: 'track posting' },
]
const LEARN = { key: 'learn', label: '📚 Learn', hint: 'study notes & quiz' }

// Learn jobs: video → transcript → study notes. Shorts jobs get the Learn step once they have notes too.
const stepsFor = (job) => (job.purpose === 'learn' ? [STEPS[0], STEPS[1], LEARN] : job.learn ? [...STEPS, LEARN] : STEPS)

export default function Workspace({ job, onChanged }) {
  const [step, setStep] = useState(() => defaultStep(job))
  const [transcript, setTranscript] = useState(null)
  const [actionErr, setActionErr] = useState('')
  const prevStatus = useRef(job.status)
  const states = stepStates(job)
  const busy = isBusy(job)

  // Transcript is shared by Transcript + Pick steps
  useEffect(() => {
    if ((job.clips.length || job.learn) && transcript === null) {
      api.transcript(job.id).then(setTranscript).catch(() => setTranscript([]))
    }
  }, [job.id, job.clips.length, job.learn, transcript])

  // Move you forward automatically when a background phase finishes
  useEffect(() => {
    const was = prevStatus.current
    prevStatus.current = job.status
    if (was === job.status) return
    if (job.status === 'review' && (step === 'source' || step === 'transcript')) setStep('pick')
    if (job.status === 'done' && (was === 'running' || was === 'queued') && step === 'pick') setStep('shorts')
    if (job.status === 'done' && step === 'source') setStep(job.purpose === 'learn' ? 'learn' : 'shorts')
  }, [job.status, step])

  // Wrap every action: show errors in one place, refresh after
  const act = async (fn) => {
    setActionErr('')
    try {
      const r = await fn()
      await onChanged()
      return r
    } catch (e) {
      setActionErr(e.message)
      throw e
    }
  }

  const go = (k) => states[k] !== 'locked' && setStep(k)
  const reloadTranscript = () => setTranscript(null) // after a caption fix (the effect above refetches)
  const props = { job, busy, act, go, transcript, onChanged, reloadTranscript }

  return (
    <div className="workspace">
      {job.title && (
        <div className="ws-title" title={job.source}>
          <b>{job.title}</b>{job.channel && <span className="muted"> · {job.channel}</span>}
        </div>
      )}
      <nav className="stepper">
        {stepsFor(job).map((s, i) => {
          const st = states[s.key]
          return (
            <button
              key={s.key}
              className={`step ${step === s.key ? 'on' : ''} ${st}`}
              onClick={() => go(s.key)}
              disabled={st === 'locked'}
              title={st === 'locked' ? 'Not available yet' : s.hint}
            >
              <span className="num">{i + 1}</span>
              <span className="lbl">
                {s.label}
                <small>{s.hint}</small>
              </span>
            </button>
          )
        })}
      </nav>

      {busy && (
        <div className="busy">
          <div className="busy-top">
            <span className="spinner" />
            <b>{job.message || job.stage}</b>
            <span className="muted">{Math.round(overallProgress(job) * 100)}%</span>
          </div>
          <div className="bar">
            <div style={{ width: `${Math.round(overallProgress(job) * 100)}%` }} />
          </div>
        </div>
      )}
      {job.error && !busy && <div className="banner error">⚠ {job.error}</div>}
      {actionErr && (
        <div className="banner error" onClick={() => setActionErr('')}>
          ⚠ {actionErr} <span className="muted">(click to dismiss)</span>
        </div>
      )}

      <div className="stepbody">
        {step === 'source' && <SourceStep {...props} />}
        {step === 'transcript' && <TranscriptStep {...props} />}
        {step === 'pick' && <PickStep {...props} />}
        {step === 'shorts' && <ShortsStep {...props} />}
        {step === 'posts' && <PostsStep {...props} />}
        {step === 'publish' && <PublishStep {...props} />}
        {step === 'learn' && <LearnStep {...props} />}
      </div>
    </div>
  )
}
