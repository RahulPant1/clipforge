import { useState } from 'react'
import { api } from '../api.js'
import { fmt } from '../util.js'

// New job → "Batch": paste links / playlists / channels → tick the videos to queue (one job each, same settings).
// Videos you already did start unticked. `items` live in NewJob so Find videos can hand its picks over.
export default function BatchList({ items, setItems }) {
  const [text, setText] = useState('')
  const [state, setState] = useState('')
  const [errors, setErrors] = useState([])
  const [more, setMore] = useState(false)

  const list = async () => {
    setState('listing')
    setErrors([])
    try {
      const r = await api.batchExpand(text)
      const have = new Set(items.map((v) => v.url))
      setItems([...items, ...r.videos.filter((v) => !have.has(v.url)).map((v) => ({ ...v, on: !v.done }))])
      setErrors(r.errors)
      setMore(r.more)
      setText('')
    } catch (e) {
      setErrors([{ url: '', error: e.message }])
    } finally {
      setState('')
    }
  }

  const toggle = (url) => setItems(items.map((v) => (v.url === url ? { ...v, on: !v.on } : v)))
  const all = (on) => setItems(items.map((v) => ({ ...v, on })))
  const picked = items.filter((v) => v.on).length
  const minutes = Math.round(items.filter((v) => v.on).reduce((a, v) => a + (v.duration || 0), 0) / 60)

  return (
    <div className="batch">
      <textarea rows={4} value={text} autoFocus onChange={(e) => setText(e.target.value)}
                placeholder={'Paste links, one per line — videos, a playlist or a channel:\n'
                  + 'https://www.youtube.com/watch?v=…\nhttps://www.youtube.com/playlist?list=…'} />
      <div className="batch-bar">
        <button type="button" className="btn" disabled={!text.trim() || state === 'listing'} onClick={list}>
          {state === 'listing' ? 'Listing…' : '＋ Add to the list'}
        </button>
        <span className="muted small">Playlists and channels are listed (no download) — untick what you don't want.</span>
      </div>
      {errors.map((e) => <p key={e.url} className="err small">{e.url && <>{e.url}: </>}{e.error}</p>)}
      {more && <p className="hint warn">Only the first 50 videos are listed — that's already days of GPU time.</p>}

      {items.length > 0 && (
        <>
          <div className="batch-bar">
            <b>{picked} of {items.length} ticked</b>
            {minutes > 0 && <span className="muted small">· {minutes} min of video</span>}
            <span className="grow" />
            <button type="button" className="linkbtn" onClick={() => all(true)}>tick all</button>
            <button type="button" className="linkbtn" onClick={() => all(false)}>untick all</button>
            <button type="button" className="linkbtn" onClick={() => setItems([])}>clear</button>
          </div>
          <ul className="batch-list">
            {items.map((v) => (
              <li key={v.url} className={v.on ? '' : 'off'}>
                <label className="checkline">
                  <input type="checkbox" checked={v.on} onChange={() => toggle(v.url)} />
                  {v.id && <img src={`https://i.ytimg.com/vi/${v.id}/default.jpg`} alt="" loading="lazy" />}
                  <span className="batch-name">
                    <a href={v.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                      {v.title || v.url}
                    </a>
                    <span className="muted small">
                      {[v.channel, v.duration && fmt(v.duration), v.from && `from “${v.from}”`].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                  {v.done > 0 && <span className="fbadge warn" title="You already made shorts from this video">
                    ⚠ done {v.done}×</span>}
                </label>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
