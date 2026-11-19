import { useEffect, useState } from 'react'
import { api } from '../api.js'

const STATUSES = [
  ['draft', 'Draft'],
  ['approved', 'Approved'],
  ['posted', 'Posted'],
]

export function CopyBtn({ text, label = 'Copy' }) {
  const [done, setDone] = useState(false)
  return (
    <button
      className="btn ghost sm"
      onClick={() => {
        navigator.clipboard.writeText(text || '')
        setDone(true)
        setTimeout(() => setDone(false), 1200)
      }}
    >
      {done ? '✓ Copied' : label}
    </button>
  )
}

const fromPost = (p) => ({ text: p?.text || '', title: p?.title || '', parts: p?.parts?.length ? p.parts : [''] })

export default function PostEditor({ job, clip, platform, busy, act }) {
  const post = clip.content?.[platform]
  const [d, setD] = useState(fromPost(post))
  const [working, setWorking] = useState(false)
  useEffect(() => setD(fromPost(post)), [post?.text, post?.title, post?.parts?.join('\u0000'), post?.angle])

  const dirty =
    post &&
    (platform === 'x'
      ? d.parts.join('\u0000') !== (post.parts || []).join('\u0000')
      : d.text !== post.text || (platform === 'youtube' && d.title !== (post.title || '')))

  const save = () => {
    const body = platform === 'x' ? { parts: d.parts } : platform === 'youtube' ? { text: d.text, title: d.title } : { text: d.text }
    return act(() => api.editPost(job.id, clip.index, platform, body)).catch(() => {})
  }
  const setStatus = (status) => act(() => api.editPost(job.id, clip.index, platform, { status })).catch(() => {})
  const newAngle = async () => {
    if ((post?.edited || dirty) && !confirm('Replace your edited text with a fresh Gemma version?')) return
    setWorking(true)
    try {
      await act(() => api.regenerate(job.id, clip.index, platform))
    } catch {
      /* shown by Workspace */
    } finally {
      setWorking(false)
    }
  }

  if (!post) {
    return (
      <div className="editor empty-ed">
        <span className="muted">No {platform} post yet.</span>
        <button className="btn sm" disabled={busy || working} onClick={newAngle}>
          {working ? 'Gemma is writing…' : '✍ Write it'}
        </button>
      </div>
    )
  }

  return (
    <div className={`editor st-${post.status}`}>
      <div className="ed-bar">
        <span className="angle" title="Creative angle">🎨 {post.angle_label || post.angle}</span>
        {post.edited && <span className="chip">edited</span>}
        <div className="seg sm status">
          {STATUSES.map(([k, l]) => (
            <button key={k} className={post.status === k ? `on ${k}` : ''} disabled={busy} onClick={() => setStatus(k)}>
              {l}
            </button>
          ))}
        </div>
      </div>

      {platform === 'youtube' && (
        <label className="fl">
          <span className="fl-head">
            Title <span className={d.title.length > 60 ? 'warn' : 'muted'}>{d.title.length}/60</span>
            <CopyBtn text={d.title} />
          </span>
          <input value={d.title} onChange={(e) => setD({ ...d, title: e.target.value })} />
        </label>
      )}

      {platform === 'x' ? (
        <div className="thread">
          {d.parts.map((t, i) => (
            <div key={i} className="tweet">
              <div className="tweet-head">
                {d.parts.length > 1 && <span className="muted">{i + 1}/{d.parts.length}</span>}
                <span className={`count ${t.length > 280 ? 'over' : ''}`}>{t.length}/280</span>
                <CopyBtn text={t} />
                {d.parts.length > 1 && (
                  <button className="btn ghost sm" title="Remove post"
                          onClick={() => setD({ ...d, parts: d.parts.filter((_, j) => j !== i) })}>✕</button>
                )}
              </div>
              <textarea rows={3} value={t}
                        onChange={(e) => setD({ ...d, parts: d.parts.map((x, j) => (j === i ? e.target.value : x)) })} />
            </div>
          ))}
          <button className="btn ghost sm addtweet" onClick={() => setD({ ...d, parts: [...d.parts, ''] })}>
            + add post to thread
          </button>
        </div>
      ) : (
        <label className="fl">
          <span className="fl-head">
            {platform === 'youtube' ? 'Description' : platform === 'instagram' ? 'Caption' : 'Post'}
            <span className={platform === 'linkedin' && d.text.length > 3000 ? 'warn' : 'muted'}>
              {d.text.length}{platform === 'linkedin' && '/3000'}
            </span>
            <CopyBtn text={d.text} />
          </span>
          <textarea rows={platform === 'linkedin' ? 10 : 7} value={d.text} onChange={(e) => setD({ ...d, text: e.target.value })} />
        </label>
      )}

      {platform === 'youtube' && (
        <div className="extras">
          {post.extra?.pinned_comment && (
            <div className="xrow"><span className="muted">Pinned comment</span><p>{post.extra.pinned_comment}</p>
              <CopyBtn text={post.extra.pinned_comment} /></div>
          )}
          {post.extra?.tags?.length > 0 && (
            <div className="xrow"><span className="muted">Tags</span><p>{post.extra.tags.join(', ')}</p>
              <CopyBtn text={post.extra.tags.join(', ')} /></div>
          )}
        </div>
      )}
      {platform === 'instagram' && (
        <div className="extras">
          {post.extra?.cover_text && (
            <div className="xrow"><span className="muted">Reel cover</span><p>{post.extra.cover_text}</p></div>
          )}
          {post.carousel?.length > 0 && (
            <div className="carousel">
              {post.carousel.map((f) => (
                <a key={f} href={api.fileUrl(job.id, f)} target="_blank" rel="noreferrer" download>
                  <img src={api.fileUrl(job.id, f, post.angle)} alt={f} />
                </a>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="ed-actions">
        <button className="btn ghost sm" disabled={busy || working} onClick={newAngle}>
          {working ? 'Gemma is writing…' : '🎲 New angle'}
        </button>
        {dirty && (
          <>
            <button className="btn ghost sm" onClick={() => setD(fromPost(post))}>Undo</button>
            <button className="btn primary-link sm" disabled={busy} onClick={save}>Save changes</button>
          </>
        )}
      </div>
    </div>
  )
}
