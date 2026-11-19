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
  listJobs: () => req('/jobs'),
  eventsUrl: () => '/api/events',
  createJob: (url, num_clips, mode) => req('/jobs', json('POST', { url, num_clips, mode })),
  uploadJob: (file, num_clips, mode) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('num_clips', num_clips)
    fd.append('mode', mode)
    return req('/jobs/upload', { method: 'POST', body: fd })
  },
  deleteJob: (id) => req(`/jobs/${id}`, { method: 'DELETE' }),
  retry: (id) => req(`/jobs/${id}/retry`, { method: 'POST' }),
  transcript: (id) => req(`/jobs/${id}/transcript`),

  // review & editing
  editClip: (id, index, changes) => req(`/jobs/${id}/clips/${index}`, json('PATCH', changes)),
  addClip: (id, clip) => req(`/jobs/${id}/clips`, json('POST', clip)),
  produce: (id) => req(`/jobs/${id}/produce`, { method: 'POST' }),
  rerender: (id, index) => req(`/jobs/${id}/clips/${index}/render`, { method: 'POST' }),
  regenerate: (id, index, platform) => req(`/jobs/${id}/clips/${index}/content/${platform}`, { method: 'POST' }),
  editPost: (id, index, platform, changes) =>
    req(`/jobs/${id}/clips/${index}/content/${platform}`, json('PATCH', changes)),

  fileUrl: (id, name, v) => `/api/jobs/${id}/files/${name}${v ? `?v=${v}` : ''}`,
  exportUrl: (id) => `/api/jobs/${id}/export.zip`,
}
