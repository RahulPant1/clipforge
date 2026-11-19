export const fmt = (s) => {
  const t = Math.max(0, s || 0)
  return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`
}
export const fmtPrecise = (s) => `${fmt(s)}.${Math.floor(((s || 0) % 1) * 10)}`

export const PLATFORMS = [
  { key: 'youtube', label: 'YouTube', icon: '▶' },
  { key: 'instagram', label: 'Instagram', icon: '◎' },
  { key: 'x', label: 'X', icon: '𝕏' },
  { key: 'linkedin', label: 'LinkedIn', icon: 'in' },
]

export const isBusy = (job) => job.status === 'queued' || job.status === 'running'

export const clipText = (transcript, clip) =>
  (transcript || [])
    .filter((s) => s.end > clip.start && s.start < clip.end)
    .map((s) => s.text)
    .join(' ')

export const STAGES = ['downloading', 'transcribing', 'selecting', 'writing', 'rendering', 'done']

export function overallProgress(job) {
  if (job.status === 'done' || job.status === 'review') return 1
  const i = STAGES.indexOf(job.stage)
  if (i < 0) return 0
  return (i + job.progress) / (STAGES.length - 1)
}

// Which steps are reachable, and where a job should open
export function stepStates(job) {
  const hasClips = job.clips.length > 0
  const rendered = job.clips.some((c) => c.file)
  const hasPosts = job.clips.some((c) => Object.keys(c.content || {}).length > 0)
  return {
    source: 'ready',
    transcript: hasClips ? 'ready' : 'locked',
    pick: hasClips ? 'ready' : 'locked',
    shorts: rendered ? 'ready' : 'locked',
    posts: hasPosts ? 'ready' : 'locked',
    publish: rendered ? 'ready' : 'locked',
  }
}

export function defaultStep(job) {
  if (!job.clips.length) return 'source'
  if (job.status === 'review') return 'pick'
  if (job.clips.some((c) => c.file)) return 'shorts'
  return 'pick'
}
