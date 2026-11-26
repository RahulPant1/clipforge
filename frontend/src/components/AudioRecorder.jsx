import { useEffect, useRef, useState } from 'react'

export default function AudioRecorder({ disabled, onDone }) {
  const [rec, setRec] = useState(null) // MediaRecorder while recording
  const [secs, setSecs] = useState(0)
  const [err, setErr] = useState('')
  const timer = useRef(0)
  const mounted = useRef(false)
  const streamRef = useRef(null)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      clearInterval(timer.current)
      streamRef.current?.getTracks().forEach((track) => track.stop())
    }
  }, [])
  const start = async () => {
    setErr('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      })
      if (!mounted.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = stream
      const mr = new MediaRecorder(stream)
      const chunks = []
      mr.ondataavailable = (e) => e.data.size && chunks.push(e.data)
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        clearInterval(timer.current)
        if (!mounted.current) return
        const blob = new Blob(chunks, { type: mr.mimeType || 'audio/webm' })
        onDone(new File([blob], `take-${Date.now()}.webm`, { type: blob.type }))
      }
      mr.start()
      setSecs(0)
      timer.current = setInterval(() => setSecs((s) => s + 1), 1000)
      setRec(mr)
    } catch (e) {
      streamRef.current?.getTracks().forEach((track) => track.stop())
      if (!mounted.current) return
      setErr(`Microphone not available: ${e.message}`)
    }
  }
  const stop = () => { rec?.stop(); setRec(null) }
  return (
    <span className="recorder">
      {rec ? (
        <button type="button" className="btn rec-on" onClick={stop}>■ Stop ({secs}s)</button>
      ) : (
        <button type="button" className="btn" disabled={disabled} onClick={start}>● Record</button>
      )}
      {err && <span className="err small">{err}</span>}
    </span>
  )
}
