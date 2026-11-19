import { api } from '../../api.js'
import { PLATFORMS } from '../../util.js'
import { CopyBtn } from '../PostEditor.jsx'

const NEXT = { draft: 'approved', approved: 'posted', posted: 'draft' }
const ICON = { draft: '○', approved: '✓', posted: '●' }

function copyText(post) {
  if (!post) return ''
  if (post.platform === 'youtube') return `${post.title}\n\n${post.text}`
  return post.text
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
            Your posting checklist. Copy, post on the platform, then click the status to move it along:{' '}
            <b>○ draft → ✓ approved → ● posted</b>.
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
                <b>#{c.index} {c.title}</b>
                <a href={api.fileUrl(job.id, c.file, c.version)} download={`short_${c.index}.mp4`}>⬇ video</a>
              </span>
            </span>
            {PLATFORMS.map((p) => {
              const post = c.content?.[p.key]
              return (
                <span key={p.key} className="pub-cell">
                  {post ? (
                    <>
                      <button className={`stbtn ${post.status}`} disabled={busy} onClick={() => cycle(c, p.key)}>
                        {ICON[post.status]} {post.status}
                      </button>
                      <CopyBtn text={copyText(post)} />
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
