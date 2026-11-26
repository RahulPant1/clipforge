import { useEffect, useState } from 'react'

export default function TakeReview({ issues, heard, onSuggest, busy = false, revision }) {
  const [advice, setAdvice] = useState('')
  const [error, setError] = useState('')
  const [working, setWorking] = useState(false)
  useEffect(() => { setAdvice(''); setError('') }, [heard, revision])
  if (!issues?.length) return null

  const suggest = async () => {
    setWorking(true)
    setError('')
    try {
      const response = await onSuggest()
      setAdvice(response.suggestion)
    } catch (failure) {
      setError(failure.message)
    } finally {
      setWorking(false)
    }
  }
  return <div className="small">
    {issues.map((issue) => <div key={issue} className="warn">{issue}</div>)}
    <p className="muted">{heard ? `Whisper heard “${heard}”. Listen to confirm.` : 'Listen to confirm whether the line was spoken.'}</p>
    {onSuggest && <button type="button" className="btn sm ghost" disabled={!!busy || working} onClick={suggest}>
      {working ? 'Checking with Gemma…' : 'Suggest a narration fix'}
    </button>}
    {advice && <p role="status">{advice}</p>}
    {error && <p className="err" role="alert">{error}</p>}
  </div>
}
