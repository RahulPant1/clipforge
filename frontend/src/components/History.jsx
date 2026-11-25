import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { CopyBtn } from './PostEditor.jsx'

// Sidebar: the history spreadsheet of every video + short, and where its folders are (backend library.py)
export default function History({ revision }) {
  const [info, setInfo] = useState(null)
  useEffect(() => {
    let active = true
    api.library().then((data) => { if (active) setInfo(data) }).catch(() => {})
    return () => { active = false }
  }, [revision])
  const where = info && (info.windows_dir || info.dir)
  const cache = info?.source_cache
  const mb = (bytes) => (bytes / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 1 })
  return (
    <div className="history">
      <a className="voicebtn" href={api.historyUrl()} download>📒 History (.xlsx)</a>
      {where && (
        <div className="history-dir" title={where}>
          <span>📁 {where}</span>
          <CopyBtn text={where} label="Copy path" />
        </div>
      )}
      {cache?.bytes > 0 && (
        <div className="history-dir" title={cache.dir}>
          <span>Source cache · {mb(cache.reclaimable_bytes)} MB reclaimable</span>
        </div>
      )}
    </div>
  )
}
