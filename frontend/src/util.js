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

export const STAGES = ['downloading', 'transcribing', 'selecting', 'rendering', 'writing', 'done']

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
    transcript: hasClips || job.learn ? 'ready' : 'locked',
    learn: job.learn ? 'ready' : 'locked',
    pick: hasClips ? 'ready' : 'locked',
    shorts: rendered ? 'ready' : 'locked',
    posts: hasPosts ? 'ready' : 'locked',
    publish: rendered ? 'ready' : 'locked',
  }
}

export function defaultStep(job) {
  if (job.purpose === 'learn') return job.learn ? 'learn' : 'source'
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

// Feed versions rendered next to the 9:16 short (backend render.ASPECTS): file suffix, preview shape, where to post
export const FEED = {
  '4:5': { suffix: '.45', ratio: '4 / 5', use: 'Instagram feed, LinkedIn' },
  '1:1': { suffix: '.11', ratio: '1 / 1', use: 'X, LinkedIn' },
}
export const feedFile = (index, aspect) => `short_${index}${FEED[aspect].suffix}.mp4`

// Hand-set crop (backend reframe.manual_path): keys {t SOURCE s, x centre 0..1, pan}. A key holds until the next
// one; a pan key glides there from the previous key. Before the first key, the first key's x.
export function cropAt(keys, t) {
  if (!keys.length) return 0.5
  const ks = [...keys].sort((a, b) => a.t - b.t)
  let i = -1
  while (i + 1 < ks.length && ks[i + 1].t <= t) i++
  if (i < 0) return ks[0].x
  const next = ks[i + 1]
  if (next && next.pan && next.t > ks[i].t) return ks[i].x + (next.x - ks[i].x) * (t - ks[i].t) / (next.t - ks[i].t)
  return ks[i].x
}

// Where the last render's tracker had the crop at `t` (tracked = [[t, x], …] per change)
export function trackedAt(tracked, t) {
  let x = tracked.length ? tracked[0][1] : 0.5
  for (const [tt, xx] of tracked) { if (tt > t) break; x = xx }
  return x
}

// Starting keys from the tracker: where the clip begins + every jump (a cut), so a hand fix keeps the rest
export function seedKeys(tracked, start, jump = 0.06, max = 30) {
  if (!tracked.length) return []
  const keys = [{ t: start, x: trackedAt(tracked, start), pan: false }]
  for (let i = 1; i < tracked.length && keys.length < max; i++) {
    if (tracked[i][0] > start && Math.abs(tracked[i][1] - tracked[i - 1][1]) > jump) {
      keys.push({ t: tracked[i][0], x: tracked[i][1], pan: false })
    }
  }
  return keys
}

// Set the crop at `t`: moves a key within `near` s, else adds one there
export function upsertKey(keys, t, x, near = 0.15) {
  const r = (v) => Math.round(v * 1000) / 1000
  const hit = keys.find((k) => Math.abs(k.t - t) < near)
  if (hit) return keys.map((k) => (k === hit ? { ...k, x: r(x) } : k))
  return [...keys, { t: r(t), x: r(x), pan: false }].sort((a, b) => a.t - b.t)
}

// Stat cards (inserts): the card's number text — mirrors numbers.fmt / Num.text on the backend
export function statNumber(v, decimals = 0, grouping = 'western') {
  if (decimals) return v.toFixed(decimals)
  const n = Math.round(v)
  if (n < 1000 || grouping === 'none') return String(n)
  if (grouping === 'indian') {
    const s = String(n)
    let head = s.slice(0, -3)
    const parts = []
    while (head.length > 2) { parts.unshift(head.slice(-2)); head = head.slice(0, -2) }
    if (head) parts.unshift(head)
    return [...parts, s.slice(-3)].join(',')
  }
  return n.toLocaleString('en-US')
}
export const statText = (ins, v) =>
  `${ins.prefix || ''}${statNumber(v ?? ins.value, ins.decimals, ins.grouping)}${ins.suffix || ''}`
