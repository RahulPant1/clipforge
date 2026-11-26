import { useState } from 'react'
import { api } from '../../api.js'
import { PLATFORMS } from '../../util.js'
import PostEditor from '../PostEditor.jsx'

export default function PostsStep({ job, busy, act, go, selectedClip, onSelectClip }) {
  const [platform, setPlatform] = useState('youtube')
  const clips = job.clips.filter((c) => c.approved && (c.file || Object.keys(c.content || {}).length))

  const count = (p, status) => clips.filter((c) => c.content?.[p] && (!status || c.content[p].status !== 'draft')).length

  return (
    <div>
      <div className="step-head">
        <div>
          <h2>Posts</h2>
          <p className="muted">
            Edit the copy, try a <b>🎲 New angle</b> if it feels samey, and mark each one <b>Approved</b> when it's
            ready. Add examples under <b>Brand → Writing style</b> to guide the tone.
          </p>
        </div>
      </div>

      <div className="ptabs big">
        {PLATFORMS.map((p) => (
          <button key={p.key} className={platform === p.key ? 'on' : ''} onClick={() => setPlatform(p.key)}>
            <span className="picon">{p.icon}</span> {p.label}
            <span className="cnt">{count(p.key, 'ok')}/{clips.length}</span>
          </button>
        ))}
      </div>

      <label>Short <select value={selectedClip ?? ''}
        onChange={(event) => onSelectClip(event.target.value ? Number(event.target.value) : null)}>
        <option value="">All shorts</option>
        {clips.map((clip) => <option key={clip.index} value={clip.index}>{clip.title}</option>)}
      </select></label>
      <div className="post-rows">
        {clips.filter((c) => selectedClip == null || c.index === selectedClip).map((c) => (
          <div key={c.index} className="post-row">
            <div className="post-clip">
              {c.file ? (
                <img src={api.fileUrl(job.id, c.file.replace('.mp4', '.jpg'), c.version)} alt="" />
              ) : (
                <div className="placeholder sm">not rendered</div>
              )}
              <b>#{c.index} {c.title}</b>
              <span className="muted">“{c.hook}”</span>
            </div>
            <PostEditor job={job} clip={c} platform={platform} busy={busy} act={act} />
          </div>
        ))}
      </div>

      <div className="footer-bar">
        <span className="muted">Approved posts move to the Publish checklist.</span>
        <button className="primary" onClick={() => go('publish')}>Publish checklist →</button>
      </div>
    </div>
  )
}
