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

// ---------------------------------------------------------------- Pick step timeline

export const MIN_CLIP = 3 // the API refuses shorter clips
export const MAX_SHORT = 59 // longer is allowed but warned about (Content ID)
const round2 = (t) => Math.round(t * 100) / 100

// Visible span around a clip: its length plus 40% each side (≥ 8 s), inside the video
export function timelineWindow(start, end, total) {
  const pad = Math.max(8, (end - start) * 0.4)
  return [Math.max(0, start - pad), total ? Math.min(total, end + pad) : end + pad]
}

// Nearest edge within tol seconds, else t
export function snapTime(t, edges, tol) {
  let best = t
  let dist = tol
  for (const e of edges) {
    const d = Math.abs(e - t)
    if (d <= dist) {
      best = e
      dist = d
    }
  }
  return best
}

// New [start, end] after moving one edge ('start' | 'end') or the whole clip ('move') to/by t.
// Keeps ≥ MIN_CLIP long and inside [0, total].
export function moveEdge(kind, t, start, end, total) {
  const max = total || Infinity
  if (kind === 'start') return [round2(Math.min(Math.max(0, t), end - MIN_CLIP)), end]
  if (kind === 'end') return [start, round2(Math.max(Math.min(max, t), start + MIN_CLIP))]
  const d = Math.min(Math.max(t, -start), max - end) // 'move': t is the offset
  return [round2(start + d), round2(end + d)]
}

// Waveform bars for [lo, hi]: the loudest level in each of n bins (0..100)
export function waveBars(levels, rate, lo, hi, n) {
  if (!levels?.length || hi <= lo) return []
  const out = []
  for (let i = 0; i < n; i++) {
    const a = Math.floor((lo + ((hi - lo) * i) / n) * rate)
    const b = Math.max(a + 1, Math.floor((lo + ((hi - lo) * (i + 1)) / n) * rate))
    let m = 0
    for (let j = a; j < b && j < levels.length; j++) m = Math.max(m, levels[j])
    out.push(m)
  }
  return out
}
