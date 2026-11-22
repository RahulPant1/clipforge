import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { CopyBtn } from './PostEditor.jsx'

// Sidebar: the history spreadsheet of every video + short, and where its folders are (backend library.py)
export default function History() {
  const [info, setInfo] = useState(null)
  useEffect(() => { api.library().then(setInfo).catch(() => {}) }, [])
  const where = info && (info.windows_dir || info.dir)
  return (
    <div className="history">
      <a className="voicebtn" href={api.historyUrl()} download>📒 History (.xlsx)</a>
      {where && (
        <div className="history-dir" title={where}>
          <span>📁 {where}</span>
          <CopyBtn text={where} label="Copy path" />
        </div>
      )}
    </div>
  )
}
