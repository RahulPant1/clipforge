import { useState } from 'react'
import { api } from '../../api.js'
import { PLATFORMS } from '../../util.js'
import { CopyBtn } from '../PostEditor.jsx'

const NEXT = { draft: 'approved', approved: 'posted', posted: 'draft' }
const ICON = { draft: '○', approved: '✓', posted: '●' }

// What each platform's upload form asks for, one copy button per field
function copies(post) {
  const extra = post.extra || {}
  switch (post.platform) {
    case 'youtube':
      return [
        ['Title', post.title],
        ['Description', post.text],
        ...(extra.tags?.length ? [['Tags', extra.tags.join(', ')]] : []), // Studio's "Tags" box: comma separated
        ...(extra.pinned_comment ? [['Pin comment', extra.pinned_comment]] : []),
      ]
    case 'x':
      return post.parts?.length > 1
        ? post.parts.map((t, i) => [`Post ${i + 1}/${post.parts.length}`, t]) // a thread: one tweet at a time
        : [['Post', post.parts?.[0] || post.text]]
    case 'instagram':
      return [['Caption', post.text]]
    default:
      return [['Post', post.text]]
  }
}

// Files you upload by hand, per short (same files as the zip, one click each)
function Files({ job, c }) {
  const ig = c.content?.instagram
  const dl = (name, label, as) => (
    <a key={name} href={api.fileUrl(job.id, name, c.version)} download={as || name}>{label}</a>
  )
  return (
    <span className="pub-files">
      ⬇ {dl(c.file, 'video', `short_${c.index}.mp4`)}
      {c.cover && <> · {dl(c.cover, 'cover')}</>}
      {' · '}{dl(`short_${c.index}.srt`, 'captions')}
      {ig?.carousel?.length > 0 && <> · carousel {ig.carousel.map((f, i) => <span key={f}>{dl(f, i + 1)} </span>)}</>}
      {' · '}{dl(`short_${c.index}.posts.md`, 'posts')}
    </span>
  )
}

function LiveLink({ job, c, p, post, busy, act }) {
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState(post.url || '')
  const save = () => {
    setOpen(false)
    if (url.trim() !== (post.url || '')) act(() => api.editPost(job.id, c.index, p, { url: url.trim() })).catch(() => {})
  }
  if (open) {
    return (
      <input className="pub-url" autoFocus value={url} placeholder="paste the post's link" disabled={busy}
             onChange={(e) => setUrl(e.target.value)} onBlur={save}
             onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setOpen(false) }} />
    )
  }
  return post.url ? (
    <span className="pub-live">
      <a href={post.url} target="_blank" rel="noreferrer">🔗 live</a>
      <button className="linkbtn" disabled={busy} onClick={() => setOpen(true)} title="Change the link">edit</button>
    </span>
  ) : (
    <button className="linkbtn" disabled={busy} onClick={() => setOpen(true)}
            title="Paste the link once it's live — it's kept in your History spreadsheet">＋ link</button>
  )
}

export default function PublishStep({ job, busy, act }) {
  const clips = job.clips.filter((c) => c.approved && c.file)
  const posts = clips.flatMap((c) => Object.values(c.content || {}))
  const posted = posts.filter((p) => p.status === 'posted').length
  const approved = posts.filter((p) => p.status === 'approved').length
  const pct = posts.length ? Math.round((posted / posts.length) * 100) : 0

  const cycle = (c, p) => {
    const post = c.content[p]
    act(() => api.editPost(job.id, c.index, p, { status: NEXT[post.status] || 'approved' })).catch(() => {})
  }

  return (
    <div>
      <div className="step-head">
        <div>
          <h2>Publish</h2>
          <p className="muted">
            Your posting checklist. Download the files, copy each field into the platform, then click the status to
            move it along: <b>○ draft → ✓ approved → ● posted</b>. Paste the live link and it's kept in your History.
          </p>
        </div>
        <a className="btn primary-link" href={api.exportUrl(job.id)}>⬇ Download pack (.zip)</a>
      </div>

      <div className="pub-stats">
        <div><b>{posted}</b><span>posted</span></div>
        <div><b>{approved}</b><span>ready to post</span></div>
        <div><b>{posts.length - posted - approved}</b><span>drafts</span></div>
        <div className="pub-bar"><div style={{ width: `${pct}%` }} /><span>{pct}% done</span></div>
      </div>

      <div className="pub-table">
        <div className="pub-row head">
          <span>Short</span>
          {PLATFORMS.map((p) => (
            <span key={p.key}><span className="picon">{p.icon}</span> {p.label}</span>
          ))}
        </div>
        {clips.map((c) => (
          <div key={c.index} className="pub-row">
            <span className="pub-clip">
              <img src={api.fileUrl(job.id, c.file.replace('.mp4', '.jpg'), c.version)} alt="" />
              <span>
                <b title={c.title}>#{c.index} {c.title}</b>
                <Files job={job} c={c} />
              </span>
            </span>
            {PLATFORMS.map((p) => {
              const post = c.content?.[p.key]
              return (
                <span key={p.key} className="pub-cell">
                  <span className="pub-plabel"><span className="picon">{p.icon}</span> {p.label}</span>
                  {post ? (
                    <>
                      <span className="pub-status">
                        <button className={`stbtn ${post.status}`} disabled={busy} onClick={() => cycle(c, p.key)}>
                          {ICON[post.status]} {post.status}
                        </button>
                        {post.posted_at && (
                          <span className="muted small" title={new Date(post.posted_at).toLocaleString()}>
                            {new Date(post.posted_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}
                          </span>
                        )}
                        <LiveLink job={job} c={c} p={p.key} post={post} busy={busy} act={act} />
                      </span>
                      <span className="pub-copies">
                        {copies(post).map(([label, text]) => <CopyBtn key={label} label={label} text={text} />)}
                      </span>
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </span>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}
