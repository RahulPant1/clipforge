async function req(path, opts = {}) {
  const r = await fetch(`/api${path}`, opts)
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    const detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join(', ') : body.detail
    throw new Error(detail || `${r.status} ${r.statusText}`)
  }
  return r.status === 204 ? null : r.json()
}

const json = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  health: () => req('/health'),
  captionPresets: () => req('/caption-presets'),
  brandVoice: () => req('/brand-voice'),
  sounds: () => req('/sounds'),
  brandKit: () => req('/brand-kit'),
  saveBrandKit: (kit) => req('/brand-kit', json('PUT', kit)),
  uploadLogo: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return req('/brand-kit/logo', { method: 'POST', body: fd })
  },
  ttsStatus: (refresh) => req(`/tts/status${refresh ? '?refresh=true' : ''}`),
  voices: () => req('/voices'),
  createVoice: (name) => req('/voices', json('POST', { name })),
  deleteVoice: (id) => req(`/voices/${id}`, { method: 'DELETE' }),
  addRecordings: (id, files, style) => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f, f.name || 'recording.webm')
    fd.append('style', style || '')
    return req(`/voices/${id}/recordings`, { method: 'POST', body: fd })
  },
  editRecording: (id, file, changes) => req(`/voices/${id}/recordings/${file}`, json('PATCH', changes)),
  deleteRecording: (id, file) => req(`/voices/${id}/recordings/${file}`, { method: 'DELETE' }),
  buildVoice: (id, style) => req(`/voices/${id}/build`, json('POST', { style: style || '' })),
  speak: (id, text, language) => req(`/voices/${id}/speak`, json('POST', { text, language: language || '' })),
  voiceAudio: (id, kind, name) => `/api/voices/${id}/audio/${kind}/${name}`,
  removeLogo: () => req('/brand-kit/logo', { method: 'DELETE' }),
  logoUrl: (v) => `/api/brand-kit/logo.png?v=${v}`,
  endCardUrl: (v) => `/api/brand-kit/end-card.png?v=${v}`,
  saveBrandVoice: (voice) => req('/brand-voice', json('PUT', voice)),
  voiceSuggestions: () => req('/brand-voice/suggestions'),
  listJobs: () => req('/jobs'),
  eventsUrl: () => '/api/events',
  createJob: (url, num_clips, mode, topic = '', hinglish = true, known = {}) =>
    req('/jobs', json('POST', { url, num_clips, mode, topic, hinglish, ...known })),
  createBatch: (urls, num_clips, mode, topic = '', hinglish = true, info = {}) =>
    req('/jobs/batch', json('POST', { urls, num_clips, mode, topic, hinglish, info })),
  batchExpand: (text) => req('/batch/expand', json('POST', { text })),
  uploadJob: (file, num_clips, mode, topic = '', hinglish = true) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('num_clips', num_clips)
    fd.append('mode', mode)
    fd.append('topic', topic)
    fd.append('hinglish', hinglish)
    return req('/jobs/upload', { method: 'POST', body: fd })
  },
  scriptState: (id) => req(`/jobs/${id}/script`),
  setScript: (id, hinglish) => req(`/jobs/${id}/script`, json('POST', { hinglish })),
  deleteJob: (id) => req(`/jobs/${id}`, { method: 'DELETE' }),
  retry: (id, topic) => req(`/jobs/${id}/retry`, topic == null ? { method: 'POST' } : json('POST', { topic })),
  transcript: (id) => req(`/jobs/${id}/transcript`),
  waveform: (id) => req(`/jobs/${id}/waveform`),
  words: (id, start, end) => req(`/jobs/${id}/words?start=${start}&end=${end}`),
  editWord: (id, edit) => req(`/jobs/${id}/words`, json('PATCH', edit)),
  replaceWords: (id, body) => req(`/jobs/${id}/transcript/replace`, json('POST', body)),
  vocabulary: () => req('/vocabulary'),
  saveVocabulary: (terms) => req('/vocabulary', json('PUT', { terms })),

  // review & editing
  editClip: (id, index, changes) => req(`/jobs/${id}/clips/${index}`, json('PATCH', changes)),
  addClip: (id, clip) => req(`/jobs/${id}/clips`, json('POST', clip)),
  produce: (id) => req(`/jobs/${id}/produce`, { method: 'POST' }),
  hookVariants: (id, index) => req(`/jobs/${id}/clips/${index}/hooks`, { method: 'POST' }),
  scenes: (id) => req(`/jobs/${id}/scenes`),
  suggestInserts: (id, index) => req(`/jobs/${id}/clips/${index}/inserts/suggest`, { method: 'POST' }),
  people: (id, index) => req(`/jobs/${id}/clips/${index}/people`),
  crop: (id, index) => req(`/jobs/${id}/clips/${index}/crop`),
  coverFrames: (id, index) => req(`/jobs/${id}/clips/${index}/cover/frames`),
  editCover: (id, index, body) => req(`/jobs/${id}/clips/${index}/cover`, json('POST', body)),
  editQuote: (id, index, text) => req(`/jobs/${id}/clips/${index}/quote`, json('POST', { text })),
  rerender: (id, index) => req(`/jobs/${id}/clips/${index}/render`, { method: 'POST' }),
  regenerate: (id, index, platform) => req(`/jobs/${id}/clips/${index}/content/${platform}`, { method: 'POST' }),
  editPost: (id, index, platform, changes) =>
    req(`/jobs/${id}/clips/${index}/content/${platform}`, json('PATCH', changes)),

  fileUrl: (id, name, v) => `/api/jobs/${id}/files/${name}${v ? `?v=${v}` : ''}`,
  exportUrl: (id) => `/api/jobs/${id}/export.zip`,
  library: () => req('/library'),
  lookup: (url) => req(`/library/lookup?url=${encodeURIComponent(url)}`),
  discover: (topic, cc, min, max, queries = []) => {
    const q = new URLSearchParams({ topic, cc, min_minutes: min, max_minutes: max })
    queries.forEach((x) => q.append('queries', x))
    return req(`/discover?${q}`)
  },
  discoverExpand: (topic) => req('/discover/expand', json('POST', { topic })),
  discoverJudge: (id, topic) => req('/discover/judge', json('POST', { id, topic })),
  discoverScore: (id, topic) => req(`/discover/score?${new URLSearchParams({ id, topic })}`),
  historyUrl: () => '/api/library/history.xlsx',
}
