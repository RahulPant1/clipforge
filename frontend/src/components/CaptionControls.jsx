export function Seg({ label, value, options, onChange, disabled }) {
  return (
    <div className="opt">
      <span className="muted">{label}</span>
      <div className="seg sm">
        {options.map(([v, l]) => (
          <button type="button" key={String(v)} disabled={disabled} className={value === v ? 'on' : ''} onClick={() => onChange(v)}>
            {l}
          </button>
        ))}
      </div>
    </div>
  )
}

// ASS &HAABBGGRR (BGR, AA 00 = opaque) -> CSS rgba
export function assColor(c) {
  const h = (c || '&H00FFFFFF').replace('&H', '').padStart(8, '0')
  const [a, b, g, r] = [0, 2, 4, 6].map((i) => parseInt(h.slice(i, i + 2), 16))
  return `rgba(${r},${g},${b},${((255 - a) / 255).toFixed(2)})`
}

export function LookChip({ p, on, disabled, onPick }) {
  const tx = (w) => (p.uppercase ? w.toUpperCase() : w)
  const ol = assColor(p.outline_color)
  const w = Math.max(1, Math.min(2, p.outline / 4))
  const sample = {
    fontFamily: `"${p.font}", sans-serif`,
    fontWeight: p.bold ? 700 : 400,
    color: assColor(p.text),
    ...(p.box
      ? { background: ol }
      : { textShadow: `${w}px ${w}px 0 ${ol}, -${w}px ${w}px 0 ${ol}, ${w}px -${w}px 0 ${ol}, -${w}px -${w}px 0 ${ol}`
          + (p.blur ? `, 0 0 ${p.blur * 2}px ${ol}` : '') }),
  }
  return (
    <button type="button" className={`look ${on ? 'on' : ''}`} disabled={disabled} onClick={onPick} title={`${p.description} · ${p.font}`}>
      <span className="sample" style={sample}>
        {tx('Make')} <span style={{ color: assColor(p.active) }}>{tx('it')}</span>{' '}
        <span style={{ color: assColor(p.keyword) }}>{tx('pop')}</span>
      </span>
      <small>{p.label}</small>
    </button>
  )
}
