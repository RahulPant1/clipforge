import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { defaultStep, isBusy, overallProgress, stepStates } from '../util.js'
import ExportPreview from './ExportPreview.jsx'
import LearnStep from './steps/LearnStep.jsx'
import PickStep from './steps/PickStep.jsx'
import PostsStep from './steps/PostsStep.jsx'
import PublishStep from './steps/PublishStep.jsx'
import ShortsStep from './steps/ShortsStep.jsx'
import SourceStep from './steps/SourceStep.jsx'
import TranscriptStep from './steps/TranscriptStep.jsx'

const REVIEW = [['source', 'Source'], ['transcript', 'Transcript'], ['pick', 'Pick clips']]
const DELIVER = [['posts', 'Posts'], ['publish', 'Publish tracker']]

export default function Workspace({ job, onChanged }) {
  const sourcePlayer = useRef(null)
  const [playhead, setPlayhead] = useState(0)
  const [previewRange, setPreviewRange] = useState(null)
  const [step, setStep] = useState(() => defaultStep(job))
  const [postClip, setPostClip] = useState(null)
  const [transcript, setTranscript] = useState(null)
  const [actionErr, setActionErr] = useState('')
  const prevStatus = useRef(job.status)
  const hasShort = job.clips.some((clip) => clip.file)
  const prevHasShort = useRef(hasShort)
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
    const hadShort = prevHasShort.current
    prevStatus.current = job.status
    prevHasShort.current = hasShort
    if (!hadShort && hasShort && (step === 'pick' || step === 'source')) setStep('shorts')
    if (was === job.status) return
    if (job.status === 'review' && (step === 'source' || step === 'transcript')) setStep('pick')
    if (job.status === 'done' && job.stage !== 'narrating' && (was === 'running' || was === 'queued') && step === 'pick') setStep('shorts')
    if (job.status === 'done' && step === 'source') setStep(job.purpose === 'learn' ? 'learn' : 'shorts')
  }, [job.status, job.stage, hasShort, step])

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

  const go = (k) => {
    if (states[k] === 'locked') return
    if (k !== step) setPreviewRange(null)
    setStep(k)
  }
  const reloadTranscript = () => setTranscript(null) // after a caption fix (the effect above refetches)
  const groups = [
    ['Review source', job.purpose === 'learn' ? REVIEW.slice(0, 2) : REVIEW],
    ...(job.purpose === 'learn' ? [] : [
      ['Edit shorts', [['shorts', 'Shorts']]],
      ['Deliver', DELIVER],
    ]),
    ...(job.purpose === 'learn' || job.learn ? [['Learn', [['learn', 'Study notes']]]] : []),
  ]
  const currentGroup = groups.find(([, entries]) => entries.some(([key]) => key === step))
  const reviewing = currentGroup?.[0] === 'Review source'
  useEffect(() => {
    if (!reviewing) sourcePlayer.current?.pause()
  }, [reviewing])
  const sourceReady = job.clips.length > 0 || !!job.learn || states.transcript !== 'locked'
  const seekSource = (time, play = false) => {
    const player = sourcePlayer.current
    if (!player) return
    player.currentTime = time
    setPlayhead(time)
    if (play) player.play().catch(() => {})
  }
  const props = { job, busy, act, go, transcript, onChanged, reloadTranscript, sourcePlayer, playhead,
    seekSource, setPreviewRange }
  const updatePlayhead = (event) => {
    const player = event.currentTarget
    if (step === 'pick' && !player.paused && previewRange) {
      const skip = previewRange.removed?.find(([start, end]) => player.currentTime >= start && player.currentTime < end)
      if (skip) player.currentTime = Math.min(skip[1], previewRange.end)
      if (player.currentTime >= previewRange.end) player.pause()
    }
    setPlayhead(player.currentTime)
  }

  return (
    <div className="workspace">
      {job.title && (
        <div className="ws-title" title={job.source}>
          <b>{job.title}</b>{job.channel && <span className="muted"> · {job.channel}</span>}
        </div>
      )}
      <nav className="stepper" aria-label="Project workspace">
        {groups.map(([label, entries]) => {
          const available = entries.find(([key]) => states[key] !== 'locked')
          return <button key={label} type="button" className={`step ${currentGroup?.[0] === label ? 'on' : ''}`}
            disabled={!available} aria-pressed={currentGroup?.[0] === label}
            onClick={() => go(available[0])}>{label}</button>
        })}
      </nav>
      {currentGroup?.[1].length > 1 && <nav className="row-btns workspace-views" aria-label={currentGroup[0]}>
        {currentGroup[1].map(([key, label]) => <button key={key} type="button" className="btn ghost"
          disabled={states[key] === 'locked'} aria-pressed={step === key} onClick={() => go(key)}>{label}</button>)}
      </nav>}

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
      {job.narration && (
        <details className="card" aria-label="Narration queue">
          <summary>Narration queue · {Object.values(job.narration.items).filter((item) => item.status === 'ready').length}
            /{Object.keys(job.narration.items).length} ready</summary>
          <ul>
            {Object.entries(job.narration.items).map(([index, item]) => (
              <li key={index} className={item.status === 'failed' ? 'err' : ''}>
                Short {index} · {item.status === 'queued' ? 'Queued' : item.message}
                {item.status === 'running' && ` (${Math.round(item.progress * 100)}%)`}
                {item.status === 'ready' && step !== 'shorts' && (
                  <>
                    <button className="btn sm ghost" onClick={() => go('shorts')}>Adjust voice timing in Shorts</button>
                    <ExportPreview key={job.updated_at} jobId={job.id} index={index} act={act} busy={busy} />
                  </>
                )}
              </li>
            ))}
          </ul>
          {Object.values(job.narration.items).some((item) => item.status === 'failed') && (
            <button className="btn sm" disabled={busy}
                    onClick={() => act(() => api.retryNarration(job.id)).catch(() => {})}>Retry failed narrations</button>
          )}
          {Object.values(job.narration.items).some((item) => item.status === 'ready') && (
            <p className="muted small">Export ready narration using Create shorts &amp; posts or Re-render.</p>
          )}
        </details>
      )}
      {actionErr && (
        <div className="banner error" onClick={() => setActionErr('')}>
          ⚠ {actionErr} <span className="muted">(click to dismiss)</span>
        </div>
      )}

      <div className={`source-review-layout ${reviewing && sourceReady ? 'with-player' : ''}`}>
        {sourceReady && <section className="card shared-source-player" hidden={!reviewing}>
          <video ref={sourcePlayer} src={api.fileUrl(job.id, 'source.mp4')} controls preload="metadata"
            aria-label="Source review player" onTimeUpdate={updatePlayhead} />
        </section>}
      <div className="stepbody">
        {step === 'source' && <SourceStep {...props} />}
        {step === 'transcript' && <TranscriptStep {...props} />}
        {step === 'pick' && <PickStep {...props} />}
        {step === 'shorts' && <ShortsStep {...props} />}
        {step === 'posts' && <PostsStep {...props} selectedClip={postClip} onSelectClip={setPostClip} />}
        {step === 'publish' && <PublishStep {...props} openPost={(index) => { setPostClip(index); go('posts') }} />}
        {step === 'learn' && <LearnStep {...props} />}
      </div>
      </div>
    </div>
  )
}
