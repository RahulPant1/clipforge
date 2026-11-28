import { useState } from 'react'

export default function StudioReviewQueue({ scenes, take, approved, busy, onJump }) {
  const [selectedFilter, setFilter] = useState(null)
  if (!take) return null

  const unapproved = scenes.flatMap((scene, index) => {
    const lines = take.lines.slice(scene.line, scenes[index + 1]?.line)
    const remaining = lines.map((line, offset) => approved.includes(line.take_id) ? null : scene.line + offset)
      .filter((line) => line !== null)
    return remaining.length ? [{ scene: index, line: remaining[0], count: remaining.length }] : []
  })
  const flags = take.lines.flatMap((line, index) => {
    if (!line.issues?.length || approved.includes(line.take_id)) return []
    const scene = Math.max(0, scenes.findLastIndex((item) => item.line <= index))
    return [{ scene, line: index, detail: line.issues[0] }]
  })
  const filter = selectedFilter ?? (flags.length ? 'flagged' : 'unapproved')
  const items = filter === 'flagged' ? flags : unapproved
  return <section className="card form" aria-label="Review queue">
    <h3>Review queue</h3>
    <div className="row-btns" role="group" aria-label="Review filters">
      <button type="button" className="btn ghost" aria-pressed={filter === 'flagged'}
        onClick={() => setFilter('flagged')}>Flagged lines ({flags.length})</button>
      <button type="button" className="btn ghost" aria-pressed={filter === 'unapproved'}
        onClick={() => setFilter('unapproved')}>Unapproved scenes ({unapproved.length})</button>
    </div>
    {items.length ? <ul className="form studio-review-items">{items.map(({ scene, line, count, detail }) => <li key={line}>
      <button type="button" className="btn ghost" disabled={!!busy} onClick={() => onJump(scene, line)}>
        {filter === 'flagged' ? `Scene ${scene + 1} · Line ${line + 1}`
          : `Scene ${scene + 1} · ${count} ${count === 1 ? 'take' : 'takes'} to review`}
      </button>
      {detail && <span className="small warn">{detail}</span>}
    </li>)}</ul> : <p className="small muted">{filter === 'flagged' ? 'No flagged lines awaiting review.'
      : 'No unapproved scenes.'}</p>}
  </section>
}
