import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { fmtPrecise } from '../util.js'
import SceneAnchors from './SceneAnchors.jsx'

export function fitChip(ln, stale, maxRate) {
  if (!ln.text.trim()) return <span className="chip">silence</span>
  if (stale) return <span className="chip warnchip">{ln.file ? 'changed — generate' : 'not generated'}</span>
  if (ln.fit === 'too_long') return <span className="chip badchip" title={`Doesn't fit even at ${maxRate}× — it's cut at the end. Shorten the text.`}>⚠ {ln.over ? `${ln.over} s ` : ''}too long — shorten</span>
  if (ln.fit === 'stretched') return <span className="chip" title="Sped up to fit its time">⏩ {ln.rate}×</span>
  if (ln.fit === 'ok') return <span className="chip goodchip">✓ fits</span>
  return null
}

function LineComparison({ source, generated, stale, sourceSeconds, assignedVoice, takeVoice, takeSeconds, rate }) {
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const sourcePlayer = useRef(null)
  const takePlayer = useRef(null)
  const play = async (player, other) => {
    if (!player.current) return
    setError('')
    other.current?.pause()
    player.current.currentTime = 0
    try {
      await player.current.play()
    } catch (failure) {
      if (failure.name !== 'AbortError') setError('Could not play this audio.')
    }
  }
  const takeLabel = stale ? 'Previous generated take' : 'Generated take'

  return <details onToggle={(e) => setOpen(e.currentTarget.open)}>
    <summary>Compare source and generated audio</summary>
    {open && <>
      {assignedVoice && <div className="muted small">Assigned voice {assignedVoice}</div>}
      <div className="dub-comparison">
        <div>
          <button type="button" className="btn sm ghost" onClick={() => play(sourcePlayer, takePlayer)}>A · Original</button>
          {sourceSeconds > 30 && <span className="muted small">First 30 seconds</span>}
          <audio ref={sourcePlayer} aria-label="Original audio" controls preload="none" src={source}
            onPlay={() => takePlayer.current?.pause()} onError={() => setError('Source audio unavailable.')} />
        </div>
        <div>
          <button type="button" className="btn sm ghost" disabled={!generated}
            onClick={() => play(takePlayer, sourcePlayer)}>B · {takeLabel}</button>
          {generated ? <>
            <span className="muted small">{[
              takeVoice && takeVoice !== assignedVoice ? takeVoice : null,
              takeSeconds != null ? `${takeSeconds.toFixed(1)} s raw` : null,
              rate != null ? `${rate.toFixed(2)}× in fitted track` : null,
            ].filter(Boolean).join(' · ')}</span>
            <audio ref={takePlayer} aria-label={takeLabel} controls preload="none" src={generated}
              onPlay={() => sourcePlayer.current?.pause()} onError={() => setError('Generated audio unavailable.')} />
          </> : <span className="muted small">Generate this line to compare.</span>}
        </div>
      </div>
      {error && <p className="warn small" role="status">{error}</p>}
    </>}
  </details>
}

export function Line({ ln, stale, busy, maxRate, onText, onRegen, onPreview, onSuggest, src, comparisonSource, comparisonMetadata, rowId, children, warnings = [] }) {
  const [text, setText] = useState(ln.text)
  const [suggestion, setSuggestion] = useState(null)
  const [suggesting, setSuggesting] = useState(false)
  useEffect(() => {
    setText(ln.text)
    setSuggestion(null)
  }, [ln.text])
  const suggest = async () => {
    setSuggesting(true)
    setSuggestion(null)
    try { setSuggestion(await onSuggest()) } catch { /* shown by act() */ } finally { setSuggesting(false) }
  }
  return (
    <li className="dub-line" id={rowId} tabIndex={rowId ? -1 : undefined}>
      <span className="muted small dub-t">{fmtPrecise(ln.start)}</span>
      <div className="dub-body">
        <span className="muted small dub-orig" title="What was said">{ln.original}</span>
        <textarea rows={2} value={text} maxLength={2000} disabled={busy} placeholder="(silence here)"
                  onChange={(e) => setText(e.target.value)} onBlur={() => text !== ln.text && onText(text)} />
        {warnings.map((warning) => <span key={warning} className="small warn">{warning}</span>)}
        {warnings.length > 0 && onSuggest && <div>
          <button className="btn sm ghost" disabled={busy || suggesting || text !== ln.text} onClick={suggest}>
            {suggesting ? 'Finding a fix…' : 'Suggest a fix'}
          </button>
          {suggestion && <div>
            <p className="small">{suggestion.text}</p>
            <div className="row-btns">
              <button className="btn sm" disabled={busy || text !== suggestion.original}
                      onClick={async () => {
                        if (await onText(suggestion.text, suggestion.original)) setSuggestion(null)
                      }}>Use suggested wording</button>
              <button className="btn sm ghost" onClick={() => setSuggestion(null)}>Dismiss</button>
            </div>
          </div>}
        </div>}
        <div className="row-btns">
          {fitChip(ln, stale, maxRate)}
          {ln.file && !stale && !comparisonSource && <audio controls preload="none" src={src(ln.file)} />}
          <button className="btn sm ghost" disabled={busy || !ln.text.trim()} onClick={onRegen}
                  title="Speak just this line again (another take)">↻ this line</button>
          {onPreview && <button className="btn sm ghost" disabled={busy} onClick={onPreview}>
            Preview this line in export
          </button>}
        </div>
        {comparisonSource && <LineComparison source={comparisonSource} stale={stale}
          generated={ln.file ? src(ln.file) : null} sourceSeconds={ln.end - ln.start}
          takeSeconds={ln.seconds} {...comparisonMetadata} />}
        {children}
      </div>
    </li>
  )
}

export function NarrationDurationSelect({ value, disabled, onChange, inherit = false }) {
  return (
    <select aria-label="Narration duration" value={value} disabled={disabled}
            onChange={(e) => onChange(e.target.value)}>
      {inherit && <option value="">Use batch duration settings</option>}
      <option value="fixed">Preserve video duration</option>
      <option value="smart">Smart duration fitting</option>
      <option value="source">Match source cues</option>
      <option value="natural">Preserve voice naturalness</option>
      <option value="scene">Scene-aware pacing · align to indexed cuts</option>
    </select>
  )
}

export function SmartDurationLimit({ value, onChange, disabled }) {
  return (
    <label className="small">
      Maximum duration (seconds)
      <input key={value} type="number" min="0.01" max="86400" step="0.01"
             placeholder="Auto" defaultValue={value ?? ''} disabled={disabled}
             onBlur={(e) => {
               if (!e.target.validity.valid) {
                 e.target.value = value ?? ''
                 return
               }
               const next = e.target.value === '' ? null : e.target.valueAsNumber
               if (next !== value) onChange(next)
             }} />
    </label>
  )
}

function estimateDuration(data, clip, pacing, limit) {
  if (!data?.script.voice) return null
  const span = clip.end - clip.start
  const stale = new Set(data.stale)
  const lines = data.script.lines.filter((line) => line.start >= clip.start && line.start < clip.end && line.text.trim())
  if (!lines.length) return null
  const length = (text) => Array.from(text.replace(/\s/g, '')).length
  let calibrated = false
  const estimates = lines.map((line) => {
    if (line.seconds && !stale.has(line.i)) return line.seconds
    const units = length(data.spoken_texts?.[line.i] ?? line.text)
    const pace = data.line_paces ? data.line_paces[line.i] : data.voice_pace
    if (pace) {
      calibrated = true
      return Math.max(0.25, units / pace.units_per_second)
    }
    return Math.max(0.25, (line.end - line.start) * units / Math.max(1, length(line.original)))
  })
  const speech = estimates.reduce((sum, seconds) => sum + seconds, 0)
  const longest = lines[estimates.indexOf(Math.max(...estimates))].i
  if (pacing === 'fixed' || pacing === 'smart') {
    const gaps = 0.12 * (lines.length - 1)
    const budget = pacing === 'fixed' ? span : Math.min(span + 4, limit ?? Infinity)
    const rate = Math.min(pacing === 'fixed' ? 2 : 1.15, Math.max(1, speech / Math.max(span - gaps, 0.01)))
    const duration = Math.max(span, speech / rate + gaps)
    const over = Math.max(0, duration - budget)
    const requiredRate = Math.max(1, speech / Math.max(budget - gaps, 0.01))
    return { speech, rate, requiredRate, duration, budget, over, longest, calibrated, sourceTooLong: span > budget + 0.000001 }
  }
  if (pacing === 'source') return { speech, duration: span, longest, calibrated }
  let end = 0
  lines.forEach((line, index) => {
    const previous = lines[index - 1]
    const gap = previous ? Math.max(0.12, line.start - previous.end) : 0
    end = Math.max(line.start - clip.start, end + gap) + estimates[index]
  })
  return { speech, rate: 1, duration: Math.max(span, end + Math.max(0, clip.end - lines.at(-1).end)),
    longest, calibrated }
}

function DurationEstimate({ fit }) {
  if (!fit) return null
  return <p className="muted small">
    Estimated speech {fit.speech.toFixed(1)} s{fit.calibrated && ' · uses saved voice pace'}
    {fit.rate && ` · tempo ${fit.rate.toFixed(2)}×`}
    {fit.over > 0.000001
      ? <span className="warn"> · Estimated {fit.over.toFixed(2)} s over the {fit.budget.toFixed(2)} s limit</span>
      : <> · Estimated video before end card {fit.duration.toFixed(1)} s</>}
    {fit.rate === undefined && ' · tempo varies by line'}
  </p>
}

function SavedVoiceSelect({ label, voices, value, disabled, onChange, fallback = "Use default voice" }) {
  return <select aria-label={label} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
    <option value="">{fallback}</option>
    {voices.map((voice) => <option key={voice.id} value={voice.id} disabled={!voice.builds?.some((b) => b.prompt)}>
      {voice.name}
    </option>)}
    {value && !voices.some((voice) => voice.id === value) && <option value={value}>{value} (unavailable)</option>}
  </select>
}

// 🎙 Replace voice: the clip's sentences re-spoken in a Voice Library voice, laid out where the originals were.
export default function DubEditor({ job, clip, busy, act, setOpt, fixedVoice = false, previewPacing, previewSmartLimit, onIssues, onFit, batchVoices, reviewId }) {
  const [data, setData] = useState(null)
  const [libraryVoices, setVoices] = useState([])
  const voices = batchVoices ?? libraryVoices
  const [working, setWorking] = useState('')
  const [speakerSuggestions, setSpeakerSuggestions] = useState(null)
  const [excludedSpeakers, setExcludedSpeakers] = useState([])
  const [open, setOpen] = useState(fixedVoice || !!clip.options?.dub)
  const [preview, setPreview] = useState('')
  const [previewLine, setPreviewLine] = useState(null)
  const audio = useRef(null)
  const [ambienceAvailable, setAmbienceAvailable] = useState(false)
  const pacing = previewPacing ?? clip.options?.dub_pacing ?? 'source'
  const smartLimit = pacing === 'smart'
    ? (fixedVoice ? previewSmartLimit : clip.options?.dub_smart_max_seconds) ?? null
    : null
  const narrationStatus = job.narration?.items[clip.index]?.status
  const load = () => api.dub(job.id, clip.index).then(setData)
  useEffect(() => {
    if (!open) return
    load().catch(() => setData(null))
    if (!fixedVoice) {
      api.voices().then((r) => setVoices(r.voices)).catch(() => setVoices([]))
      api.narrationOptions().then((r) => setAmbienceAvailable(r.ambience_available)).catch(() => {})
    }
  }, [open, job.id, clip.index, clip.start, clip.end, clip.version, fixedVoice, narrationStatus])
  useEffect(() => setPreview(''), [pacing, smartLimit, clip.start, clip.end])
  useEffect(() => setSpeakerSuggestions(null), [job.id, clip.index, clip.start, clip.end])
  useEffect(() => {
    if (!onIssues) return
    if (!data) {
      onIssues(clip.index, null)
      return
    }
    const lines = (data?.script.lines ?? []).filter((line) =>
      line.start >= clip.start && line.start < clip.end && line.text.trim())
    const named = new Set(lines.map((line) => line.speaker).filter(Boolean))
    const assigned = new Set(lines.map((line) =>
      line.voice_override || data.script.speaker_voices?.[line.speaker] || data.script.voice))
    const staleIds = new Set(data?.stale)
    const summarize = (line) => ({ i: line.i, text: line.text, start: line.start })
    onIssues(clip.index, {
      flagged: lines.filter((line) => data.warnings?.[line.i]?.length).map(summarize),
      stale: lines.filter((line) => line.file && staleIds.has(line.i)).map(summarize),
      unlabeled: named.size > 1 || assigned.size > 1 ? lines.filter((line) => !line.speaker).map(summarize) : [],
    })
  }, [clip.index, clip.start, clip.end, data, onIssues])

  const run = async (fn, label) => {
    setPreview('')
    setWorking(label)
    try {
      const result = await act(fn)
      if (result?.script) setData(result)
      else await load()
      return result
    } catch { /* shown by act() */ } finally { setWorking('') }
  }
  const fit = estimateDuration(data, clip, pacing, smartLimit)
  useEffect(() => {
    onFit?.(clip.index, estimateDuration(data, clip, pacing, smartLimit))
  }, [data, clip, pacing, smartLimit, onFit])
  const s = data?.script
  const speakers = [...new Set((s?.lines ?? []).map((line) => line.speaker).filter(Boolean))]
  const speakersList = `dub-speakers-${clip.index}-${fixedVoice ? 'batch' : 'short'}`
  const stale = new Set(data?.stale || [])
  const voice = voices.find((v) => v.id === s?.voice)
  const on = !!clip.options?.dub
  const faceFraming = ['face', 'speaker', 'split'].includes(clip.reframe)
  const tooLong = s ? s.lines.filter((l) => l.fit === 'too_long').length : 0
  const timingChanged = s?.track && (s.pacing !== pacing || (s.smart_max_seconds ?? null) !== smartLimit || !s.span
    || Math.abs(s.span[0] - clip.start) > 0.001 || Math.abs(s.span[1] - clip.end) > 0.001)
  const describeVoice = (id, version) => {
    if (!id) return ''
    const name = voices.find((item) => item.id === id)?.name ?? id
    return version ? `${name} · v${version}` : name
  }
  const comparisonMetadata = (line) => {
    const override = line.voice_override || s.speaker_voices[line.speaker]
    const [takeId, takeVersion] = (line.voice ?? '').split('@v')
    return {
      assignedVoice: describeVoice(override || s.voice, override ? null : s.version),
      takeVoice: describeVoice(takeId.split('@')[0], takeVersion),
      rate: !timingChanged && !stale.has(line.i) ? line.rate : null,
    }
  }
  const makePreview = async () => {
    setWorking('Making video preview…')
    setPreview('')
    setPreviewLine(null)
    try {
      const { file } = await act(() => api.previewDub(job.id, clip.index, pacing, smartLimit))
      setPreview(api.fileUrl(job.id, file, Date.now()))
    } catch { /* shown by act() */ } finally { setWorking('') }
  }
  const previewExportLine = async (line) => {
    setWorking('Rendering line preview…')
    setPreview('')
    setPreviewLine(line.i)
    const start = Math.max(0, line.at - 0.5)
    const length = Math.min(10, Math.max(2, (line.seconds || 0) / (line.rate || 1) + 1))
    try {
      const { file } = await act(() => api.exportPreview(job.id, clip.index, { start, length }))
      setPreview(api.fileUrl(job.id, file, Date.now()))
    } catch { /* shown by act() */ } finally { setWorking('') }
  }
  const player = preview && <video key={preview}
    aria-label={previewLine == null ? 'First narration line over source video' : `Export preview of line ${previewLine + 1}`}
    controls playsInline preload="metadata" style={{ width: '100%', maxWidth: 480 }} src={preview} />

  if (!open) {
    return (
      <div className="opt-col">
        <button className="btn sm ghost" disabled={busy} onClick={() => setOpen(true)}>🎙 Replace voice…</button>
      </div>
    )
  }
  return (
    <div className="opt-col dub">
      <span className="muted">🎙 Replace voice <span className="small">· speech from a saved Voice Library voice.</span></span>
      {faceFraming && <span className="muted small warn">The speaker is on screen: lips won't match the new words.</span>}
      {!fixedVoice && !voices.length && <span className="muted small">No voices yet — make one in 🗣 Voice Library first.</span>}
      {s && (
        <>
          <div className="row-btns">
            {!fixedVoice && <select value={s.voice || ''} disabled={busy || !!working}
                    onChange={(e) => run(() => api.putDub(job.id, clip.index, { voice: e.target.value }), 'Saving…')}>
              <option value="">Choose a voice…</option>
              {voices.map((v) => <option key={v.id} value={v.id} disabled={!v.builds?.length}>
                {v.name}{v.builds?.length ? '' : ' (not built)'}</option>)}
            </select>}
            {!fixedVoice && voice && (
              <select value={s.version ?? ''} disabled={busy || !!working}
                      onChange={(e) => run(() => api.putDub(job.id, clip.index,
                        { voice: s.voice, version: e.target.value === '' ? null : Number(e.target.value) }), 'Saving…')}>
                <option value="">newest version</option>
                {[...voice.builds].reverse().filter((b) => b.prompt).map((b) => <option key={b.version} value={b.version}>v{b.version}</option>)}
              </select>
            )}
            {!fixedVoice && <>
              <NarrationDurationSelect value={pacing} disabled={busy || !!working}
                onChange={(value) => setOpt('dub_pacing', value)} />
              {pacing === 'smart' && <SmartDurationLimit value={smartLimit} disabled={busy || !!working}
                onChange={(value) => setOpt('dub_smart_max_seconds', value)} />}
            </>}
            {!fixedVoice && <button className="btn sm" disabled={busy || !!working || !s.voice || !stale.size}
                    onClick={() => run(() => api.generateDub(job.id, clip.index), `Speaking ${stale.size} line(s)…`)}>
              ▶ Generate {stale.size || ''} line{stale.size === 1 ? '' : 's'}
            </button>}
            <button className="btn sm" disabled={busy || !!working || !s.voice} onClick={makePreview}>
              Preview first line over video
            </button>
            {working && <span className="muted small">{working} (~2 s per second of speech + ~20 s to load)</span>}
          </div>
          <DurationEstimate fit={fit} />
          {s.voice && <div>
            <button className="btn sm ghost" disabled={busy || !!working || !s.lines.some((line) =>
              !line.speaker && line.text.trim() && line.start >= clip.start && line.start < clip.end)}
              onClick={async () => {
                setSpeakerSuggestions(null)
                setExcludedSpeakers([])
                const result = await run(() => api.suggestDubSpeakers(job.id, clip.index), 'Checking visible speakers…')
                if (result) setSpeakerSuggestions(result)
              }}>Suggest speaker labels</button>
            {speakerSuggestions && <>
              <p className="muted small">{Object.keys(speakerSuggestions.speakers).length
                ? 'Review labels based on visible mouth movement.' : 'No clear left/right speaker turns were found.'}</p>
              {Object.entries(speakerSuggestions.speakers).map(([id, label]) => (
                <label key={id} className="row-btns small">
                  <input type="checkbox" checked={!excludedSpeakers.includes(id)} disabled={busy || !!working}
                    onChange={(e) => {
                      const checked = e.target.checked
                      setExcludedSpeakers((previous) => checked ? previous.filter((item) => item !== id) : [...previous, id])
                    }} />
                  {label} · {s.lines.find((line) => line.i === Number(id))?.text}
                </label>
              ))}
              {Object.keys(speakerSuggestions.speakers).some((id) => !excludedSpeakers.includes(id)) && <button className="btn sm" disabled={busy || !!working}
                onClick={async () => {
                  const speakers = Object.fromEntries(Object.entries(speakerSuggestions.speakers).filter(([id]) => !excludedSpeakers.includes(id)))
                  const result = await run(() => api.putDub(job.id, clip.index, {
                    speakers,
                    expected_speakers: Object.fromEntries(Object.keys(speakers).map((id) => [id, ''])),
                    expected_texts: Object.fromEntries(Object.keys(speakers)
                      .map((id) => [id, speakerSuggestions.expected_texts[id]])),
                  }), 'Saving speaker labels…')
                  if (result) setSpeakerSuggestions(null)
                }}>Apply selected labels</button>}
              <button className="btn sm ghost" onClick={() => setSpeakerSuggestions(null)}>Dismiss</button>
            </>}
          </div>}
          <datalist id={speakersList}>{speakers.map((speaker) => <option key={speaker} value={speaker} />)}</datalist>
          {s.voice && speakers.map((speaker) => (
            <label className="row-btns small" key={speaker}>
              Voice for {speaker}
              <SavedVoiceSelect label={`Voice for ${speaker}`} voices={voices}
                value={s.speaker_voices[speaker] || ''} disabled={busy || !!working}
                onChange={(id) => run(() => api.putDub(job.id, clip.index, { speaker_voices: { [speaker]: id } }), 'Saving…')} />
            </label>
          ))}
          <ul className="dub-lines">
            {s.lines.filter((l) => l.start >= clip.start && l.start < clip.end).map((ln) => (
              <Line key={ln.i} rowId={reviewId ? `${reviewId}-line-${ln.i}` : undefined} ln={ln} stale={stale.has(ln.i)} busy={busy || !!working} maxRate={data.max_rate}
                    warnings={data.warnings?.[ln.i]}
                    comparisonSource={(data.warnings?.[ln.i]?.length || ln.fit === 'too_long' || ln.file)
                      ? api.dubSourceAudio(job.id, clip.index, ln.i) : null}
                    comparisonMetadata={comparisonMetadata(ln)}
                    src={(f) => api.fileUrl(job.id, f, s.track_version)}
                    onPreview={!fixedVoice && on && !timingChanged && !stale.has(ln.i) && ln.at != null
                      ? () => previewExportLine(ln) : undefined}
                    onSuggest={() => act(() => api.suggestDubLine(job.id, clip.index, ln.i))}
                    onText={(t, expected) => run(() => api.putDub(job.id, clip.index, {
                      texts: { [ln.i]: t }, expected_texts: expected === undefined ? {} : { [ln.i]: expected },
                    }), 'Saving…')}
                    onRegen={() => run(() => api.generateDub(job.id, clip.index, [ln.i]), 'Speaking 1 line…')}>
                {data.spoken_texts?.[ln.i] && data.spoken_texts[ln.i] !== ln.text &&
                  <p className="muted small">Spoken as “{data.spoken_texts[ln.i]}”</p>}
                {s.voice && <div className="row-btns">
                  <input aria-label="Speaker name" placeholder="Speaker name" list={speakersList} maxLength={40} key={ln.speaker}
                    defaultValue={ln.speaker} disabled={busy || !!working}
                    onBlur={(e) => {
                      const speaker = e.target.value.trim()
                      if (speaker !== ln.speaker) {
                        run(() => api.putDub(job.id, clip.index, { speakers: { [ln.i]: speaker } }), 'Saving…')
                      }
                    }} />
                  <SavedVoiceSelect label="Voice override for this line" voices={voices}
                    fallback={s.speaker_voices[ln.speaker] ? 'Use speaker voice' : 'Use default voice'}
                    value={ln.voice_override || ''} disabled={busy || !!working}
                    onChange={(id) => run(() => api.putDub(job.id, clip.index, { line_voices: { [ln.i]: id } }), 'Saving…')} />
                </div>}
                {previewLine === ln.i && player}
              </Line>
            ))}
          </ul>
          {previewLine == null && player}
          {tooLong > 0 && <p className="err small">{tooLong} line(s) don't fit their time and get cut — shorten them.</p>}
          {s.track && (
            <div className="dub-track">
              <span className="muted small">🤖 AI voice track</span>
              <audio ref={audio} controls preload="none" src={`${api.fileUrl(job.id, s.track)}?v=${s.track_version}`} />
            </div>
          )}
          {!fixedVoice && <div className="row-btns">
            <label className="small"><input type="checkbox" checked={!!clip.options?.dub_ambience}
                                            disabled={busy || !!working || (!ambienceAvailable && !clip.options?.dub_ambience)}
                                            onChange={(e) => setOpt('dub_ambience', e.target.checked)} />{' '}
              Keep source ambience {!ambienceAvailable && '(Demucs required)'}</label>
            {clip.options?.dub_ambience && <label className="small">
              Ambience level
              <input key={clip.options.dub_ambience_level} type="number" min="0" max="50" step="1"
                     defaultValue={Math.round((clip.options.dub_ambience_level ?? 0.18) * 100)}
                     disabled={busy || !!working}
                     onBlur={(e) => {
                       const level = e.target.valueAsNumber
                       const previous = Math.round((clip.options.dub_ambience_level ?? 0.18) * 100)
                       if (level >= 0 && level <= 50 && level !== previous) setOpt('dub_ambience_level', level / 100)
                       else e.target.value = previous
                     }} />%
            </label>}
            <label className="small"><input type="checkbox" checked={on} disabled={busy || !s.track}
                                            onChange={(e) => setOpt('dub', e.target.checked || null)} />{' '}
              Use in this short {on && '(re-render to hear it)'}</label>
            <button className="btn sm ghost" disabled={busy || !!working}
                    onClick={() => window.confirm('Start again from what is said in the clip now? Your edited lines are reset.')
                      && run(() => api.putDub(job.id, clip.index,
                        { reset: true, voice: s.voice || null, version: s.version }), 'Resetting…')}>
              Reset lines
            </button>
            {data.saved && <button className="btn sm ghost" disabled={busy || !!working}
                                   onClick={() => window.confirm('Remove the replaced voice and go back to the original?')
                                     && act(() => api.deleteDub(job.id, clip.index)).then(() => { setData(null); setOpen(false) }).catch(() => {})}>
              Remove</button>}
          </div>}
          {!fixedVoice && pacing === 'scene' && (
            s.track && !timingChanged && !stale.size
              ? <SceneAnchors job={job} clip={clip} script={s} busy={busy || !!working} audio={audio} act={act} />
              : <button className="btn sm" disabled={busy || !!working || !s.voice}
                        onClick={() => run(() => api.generateDub(job.id, clip.index), 'Updating narration timing…')}>
                  Prepare voice track for scene anchors
                </button>
          )}
        </>
      )}
    </div>
  )
}
