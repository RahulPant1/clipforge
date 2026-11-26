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
  createVoice: (name, description, consent) => req('/voices', json('POST', { name, description, consent })),
  updateVoice: (id, changes) => req(`/voices/${id}`, json('PATCH', changes)),
  deleteVoice: (id) => req(`/voices/${id}`, { method: 'DELETE' }),
  addRecordings: (id, files, style) => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f, f.name || 'recording.webm')
    fd.append('style', style || '')
    return req(`/voices/${id}/recordings`, { method: 'POST', body: fd })
  },
  editRecording: (id, file, changes) => req(`/voices/${id}/recordings/${file}`, json('PATCH', changes)),
  deleteRecording: (id, file) => req(`/voices/${id}/recordings/${file}`, { method: 'DELETE' }),
  buildVoice: (id, style, sampleText) =>
    req(`/voices/${id}/build`, json('POST', { style: style || '', sample_text: sampleText || '' })),
  speak: (id, text, language, version) =>
    req(`/voices/${id}/speak`, json('POST', { text, language: language || '', version: version ?? null })),
  pronunciations: () => req('/pronunciations'),
  savePronunciations: (aliases) => req('/pronunciations', json('PUT', { aliases })),
  calibrateVoice: (id, version) =>
    req(`/voices/${id}/pace${version ? `?version=${version}` : ''}`, { method: 'POST' }),
  compareVoice: (id, text, versions, language) =>
    req(`/voices/${id}/compare`, json('POST', { text, versions, language: language || '' })),
  voiceAudio: (id, kind, name) => `/api/voices/${id}/audio/${kind}/${name}`,
  scriptShorts: () => req('/script-shorts'),
  planScriptBroll: (previewId, assets) =>
    req('/script-shorts/broll-plan', json('POST', { preview_id: previewId, assets })),
  reviewScriptTake: (id, line) =>
    req(`/script-shorts/narration/${id}/lines/${line}/suggest`, { method: 'POST' }),
  scriptDrafts: () => req('/script-shorts/drafts'),
  saveScriptDraft: (body) => req('/script-shorts/drafts', json('POST', body)),
  scriptNarration: (id) => req(`/script-shorts/narration/${id}`),
  retakeScriptLine: (id, line, body) =>
    req(`/script-shorts/narration/${id}/lines/${line}/retake`, json('POST', body)),
  previewScriptNarration: (body) => req('/script-shorts/narration', json('POST', body)),
  scriptNarrationAudio: (id) => `/api/script-shorts/narration/${id}/audio`,
  createScriptShort: (body) => req('/script-shorts', json('POST', body)),
  scriptShortFile: (id, name) => `/api/script-shorts/${id}/files/${name}`,
  revoiceList: () => req('/revoice'),
  revoice: (id) => req(`/revoice/${id}`),
  createRevoice: (file, name, recorded = false) => {
    const fd = new FormData()
    fd.append('file', file, file.name || 'narration.webm')
    fd.append('name', name || '')
    if (recorded) fd.append('recorded', 'true')
    return req('/revoice', { method: 'POST', body: fd })
  },
  putRevoice: (id, body) => req(`/revoice/${id}`, json('PUT', body)),
  generateRevoice: (id, lines) => req(`/revoice/${id}/generate`, json('POST', { lines: lines ?? null })),
  reviewRevoiceTake: (id, line) => req(`/revoice/${id}/lines/${line}/review-take`, { method: 'POST' }),
  pickupRevoice: (id, line, text) => req(`/revoice/${id}/lines/${line}/pickup`, json('POST', { expected_text: text })),
  pickupRevoiceBatch: (id, texts) => req(`/revoice/${id}/pickups`, json('POST', { expected_texts: texts })),
  deleteRevoice: (id) => req(`/revoice/${id}`, { method: 'DELETE' }),
  revoiceFile: (id, name, v) => `/api/revoice/${id}/files/${name}${v ? `?v=${v}` : ''}`,
  removeLogo: () => req('/brand-kit/logo', { method: 'DELETE' }),
  logoUrl: (v) => `/api/brand-kit/logo.png?v=${v}`,
  endCardUrl: (v) => `/api/brand-kit/end-card.png?v=${v}`,
  saveBrandVoice: (voice) => req('/brand-voice', json('PUT', voice)),
  voiceSuggestions: () => req('/brand-voice/suggestions'),
  listJobs: () => req('/jobs'),
  eventsUrl: () => '/api/events',
  createJob: (url, num_clips, mode, topic = '', hinglish = true, known = {}) =>
    req('/jobs', json('POST', { url, num_clips, mode, topic, hinglish, ...known })),
  createBatch: (urls, num_clips, mode, topic = '', hinglish = true, info = {}, whisper_model = null) =>
    req('/jobs/batch', json('POST', { urls, num_clips, mode, topic, hinglish, info, whisper_model })),
  batchExpand: (text) => req('/batch/expand', json('POST', { text })),
  uploadJob: (file, num_clips, mode, topic = '', hinglish = true, purpose = 'create', whisperModel = null, recorded = false) => {
    const fd = new FormData()
    fd.append('purpose', purpose)
    if (recorded) fd.append('recorded', 'true')
    fd.append('file', file)
    fd.append('num_clips', num_clips)
    fd.append('mode', mode)
    fd.append('topic', topic)
    fd.append('hinglish', hinglish)
    if (whisperModel) fd.append('whisper_model', whisperModel)
    return req('/jobs/upload', { method: 'POST', body: fd })
  },
  scriptState: (id) => req(`/jobs/${id}/script`),
  setScript: (id, hinglish) => req(`/jobs/${id}/script`, json('POST', { hinglish })),
  deleteJob: (id) => req(`/jobs/${id}`, { method: 'DELETE' }),
  downloader: () => req('/downloader'),
  rollbackDownloader: () => req('/downloader/rollback', { method: 'POST' }),
  updateDownloaderAndRetry: (id) => req(`/jobs/${id}/downloader-update`, { method: 'POST' }),
  recoverSource: (id, file) => {
    const form = new FormData()
    form.append('file', file)
    return req(`/jobs/${id}/recover-source`, { method: 'POST', body: form })
  },
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
  produce: (id, confirm = false, originalAudio = false) =>
    req(`/jobs/${id}/produce`, json('POST', { confirm, original_audio: originalAudio })),
  validation: (id) => req(`/jobs/${id}/validation`),
  learnNotes: (id) => req(`/jobs/${id}/learn`),
  makeLearn: (id) => req(`/jobs/${id}/learn`, { method: 'POST' }),
  dub: (id, index) => req(`/jobs/${id}/clips/${index}/dub`),
  putDub: (id, index, body) => req(`/jobs/${id}/clips/${index}/dub`, json('PUT', body)),
  generateDub: (id, index, lines, activate = false) =>
    req(`/jobs/${id}/clips/${index}/dub/generate`, json('POST', { lines: lines ?? null, activate })),
  draftDubs: (id, body) => req(`/jobs/${id}/dub/drafts`, json('POST', body)),
  previewDub: (id, index, pacing, smartMaxSeconds) => req(
    `/jobs/${id}/clips/${index}/dub/preview`, json('POST', { pacing, smart_max_seconds: smartMaxSeconds }),
  ),
  narrationOptions: () => req('/narration/options'),
  exportPreview: (id, index, range) => req(`/jobs/${id}/clips/${index}/export-preview`, json('POST', range)),
  uploadBroll: (id, index, file) => {
    const body = new FormData()
    body.append('file', file)
    return req(`/jobs/${id}/clips/${index}/broll`, { method: 'POST', body })
  },
  suggestBroll: (id, index) => req(`/jobs/${id}/clips/${index}/broll/suggestions`),
  brollAssetUrl: (asset) => `/api/broll/assets/${asset}`,
  useBroll: (id, index, asset) => req(`/jobs/${id}/clips/${index}/broll/use/${asset}`, { method: 'POST' }),
  dubSourceAudio: (id, index, line) => `/api/jobs/${id}/clips/${index}/dub/lines/${line}/source-audio`,
  suggestDubSpeakers: (id, index) =>
    req(`/jobs/${id}/clips/${index}/dub/speaker-suggestions`, { method: 'POST' }),
  reviewDubTake: (id, index, line) =>
    req(`/jobs/${id}/clips/${index}/dub/lines/${line}/review-take`, { method: 'POST' }),
  suggestDubLine: (id, index, line, shorten = false) =>
    req(`/jobs/${id}/clips/${index}/dub/lines/${line}/suggest${shorten ? '?shorten=true' : ''}`, { method: 'POST' }),
  queueNarration: (id, body) => req(`/jobs/${id}/dub/batch`, json('POST', body)),
  retryNarration: (id) => req(`/jobs/${id}/dub/batch/retry`, { method: 'POST' }),
  deleteDub: (id, index) => req(`/jobs/${id}/clips/${index}/dub`, { method: 'DELETE' }),
  reviewClip: (id, index) => req(`/jobs/${id}/clips/${index}/review`, { method: 'POST' }),
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
