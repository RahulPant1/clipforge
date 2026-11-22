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
  saveBrandVoice: (voice) => req('/brand-voice', json('PUT', voice)),
  voiceSuggestions: () => req('/brand-voice/suggestions'),
  listJobs: () => req('/jobs'),
  eventsUrl: () => '/api/events',
  createJob: (url, num_clips, mode, topic = '', hinglish = true) =>
    req('/jobs', json('POST', { url, num_clips, mode, topic, hinglish })),
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
  people: (id, index) => req(`/jobs/${id}/clips/${index}/people`),
  coverFrames: (id, index) => req(`/jobs/${id}/clips/${index}/cover/frames`),
  editCover: (id, index, body) => req(`/jobs/${id}/clips/${index}/cover`, json('POST', body)),
  rerender: (id, index) => req(`/jobs/${id}/clips/${index}/render`, { method: 'POST' }),
  regenerate: (id, index, platform) => req(`/jobs/${id}/clips/${index}/content/${platform}`, { method: 'POST' }),
  editPost: (id, index, platform, changes) =>
    req(`/jobs/${id}/clips/${index}/content/${platform}`, json('PATCH', changes)),

  fileUrl: (id, name, v) => `/api/jobs/${id}/files/${name}${v ? `?v=${v}` : ''}`,
  exportUrl: (id) => `/api/jobs/${id}/export.zip`,
  library: () => req('/library'),
  historyUrl: () => '/api/library/history.xlsx',
}
