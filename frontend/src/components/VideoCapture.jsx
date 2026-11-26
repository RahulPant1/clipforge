import { useEffect, useRef, useState } from 'react'

const VIDEO = { width: { ideal: 1280, max: 1280 }, height: { ideal: 720, max: 720 }, frameRate: { ideal: 24, max: 24 } }
const MIC = { echoCancellation: true, noiseSuppression: true }
const FORMATS = ['video/mp4', 'video/webm;codecs=vp8,opus', 'video/webm']

export default function VideoCapture({ onUse, disabled, maxSeconds = 600 }) {
  const session = useRef(null)
  const [phase, setPhase] = useState('')
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState('')
  const [error, setError] = useState('')
  const devices = navigator.mediaDevices

  const dispose = (capture) => {
    clearTimeout(capture.timer)
    capture.streams.flatMap((stream) => stream.getTracks()).forEach((track) => track.stop())
  }
  useEffect(() => () => {
    if (session.current) {
      const capture = session.current
      session.current = null
      if (capture.recorder?.state === 'recording') capture.recorder.stop()
      dispose(capture)
    }
  }, [])
  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  const start = async (kind) => {
    if (session.current) return
    setError('')
    setPhase('requesting')
    const capture = { streams: [], chunks: [] }
    session.current = capture
    const addStream = (stream) => {
      capture.streams.push(stream)
      if (session.current !== capture) {
        dispose(capture)
        return false
      }
      return true
    }
    try {
      let stream
      if (kind === 'screen') {
        const screen = await devices.getDisplayMedia({ video: VIDEO, audio: false })
        if (!addStream(screen)) return
        const mic = await devices.getUserMedia({ audio: MIC })
        if (!addStream(mic)) return
        const video = screen.getVideoTracks()[0]
        if (video.readyState !== 'live') throw new Error('Screen sharing ended before recording started.')
        stream = new MediaStream([video, ...mic.getAudioTracks()])
      } else {
        stream = await devices.getUserMedia({ video: VIDEO, audio: MIC })
        if (!addStream(stream)) return
      }
      if (!stream.getAudioTracks().length) throw new Error('A microphone is required for narration.')
      const mimeType = FORMATS.find((type) => MediaRecorder.isTypeSupported(type))
      const recorder = new MediaRecorder(stream, { ...(mimeType && { mimeType }), videoBitsPerSecond: 2_500_000 })
      capture.recorder = recorder
      capture.stop = () => {
        if (session.current === capture && recorder.state === 'recording') {
          setPhase('finishing')
          recorder.stop()
        }
      }
      recorder.ondataavailable = ({ data }) => {
        if (data.size) capture.chunks.push(data)
      }
      recorder.onerror = () => {
        if (session.current !== capture) return
        session.current = null
        dispose(capture)
        setPhase('')
        setError('Recording failed. Check your camera or microphone and try again.')
      }
      recorder.onstop = () => {
        dispose(capture)
        if (session.current !== capture) return
        session.current = null
        setPhase('')
        const type = recorder.mimeType || capture.chunks[0]?.type || 'video/webm'
        const extension = type.startsWith('video/mp4') ? 'mp4' : 'webm'
        const result = new File(capture.chunks, `${kind}-recording-${Date.now()}.${extension}`, { type })
        if (result.size) setFile(result)
        else setError('No video was captured. Try a longer recording.')
      }
      stream.getTracks().forEach((track) => track.addEventListener('ended', capture.stop, { once: true }))
      recorder.start(1000)
      capture.timer = setTimeout(capture.stop, maxSeconds * 1000)
      setPhase('recording')
    } catch (failure) {
      dispose(capture)
      if (session.current !== capture) return
      session.current = null
      setPhase('')
      setError(failure.name === 'NotAllowedError'
        ? 'Recording permission was declined. Allow access and try again.'
        : failure.message || 'Could not start recording.')
    }
  }

  if (typeof MediaRecorder === 'undefined' || !devices?.getUserMedia) {
    return <p className="muted small">Recording needs a supported browser on localhost or HTTPS. You can upload a video file.</p>
  }
  return <div className="video-capture">
    {!file && <div className="row-btns">
      {phase === 'recording' ? <button type="button" className="btn" onClick={() => session.current?.stop()}>
        Stop recording
      </button> : <>
        {devices.getDisplayMedia && <button type="button" className="btn ghost" disabled={disabled || !!phase}
          onClick={() => start('screen')}>Record screen + mic</button>}
        <button type="button" className="btn ghost" disabled={disabled || !!phase}
          onClick={() => start('webcam')}>Record webcam + mic</button>
      </>}
      {phase && <span role="status" className="small">
        {phase === 'recording' ? 'Recording…' : phase === 'requesting' ? 'Waiting for permissions…' : 'Finishing…'}
      </span>}
    </div>}
    {file && <>
      <video className="srcvid" controls preload="metadata" src={preview || undefined} aria-label="Recording preview" />
      <div className="row-btns">
        <button type="button" className="btn" disabled={disabled} onClick={() => { onUse(file); setFile(null); setPreview('') }}>
          Use recording
        </button>
        <button type="button" className="btn ghost" onClick={() => { setFile(null); setPreview('') }}>Discard recording</button>
      </div>
    </>}
    {error && <p className="err small" role="alert">{error}</p>}
  </div>
}
