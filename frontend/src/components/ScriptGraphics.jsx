const choices = {
  kind: [['arrow', 'Arrow'], ['label', 'Label'], ['highlight', 'Highlight']],
  horizontal: [['left', 'Left'], ['center', 'Center'], ['right', 'Right']],
  vertical: [['top', 'Top'], ['middle', 'Middle'], ['bottom', 'Lower']],
  direction: [['right', 'Right'], ['left', 'Left'], ['up', 'Up'], ['down', 'Down']],
}

function Choice({ title, field, item, onChange }) {
  return <label>{title} <select value={item[field]} onChange={(event) => onChange(field, event.target.value)}>
    {choices[field].map(([id, label]) => <option key={id} value={id}>{label}</option>)}
  </select></label>
}

export default function ScriptGraphics({ items, setItems, take, busy, canPreview, onPreview, onPlay }) {
  return <details className="card">
    <summary>Animated graphics{items.length > 0 && ` · ${items.length}`}</summary>
    <div className="form">
      {!take && <button type="button" className="btn ghost" disabled={!!busy || !canPreview} onClick={onPreview}>
        Prepare narration timing
      </button>}
      {items.map((item, index) => {
        const update = (field, value) => setItems((current) => current.map((entry, i) =>
          i === index ? { ...entry, [field]: value } : entry))
        const lineExists = !!take?.lines[item.line]
        return <fieldset key={index} className="form" disabled={!!busy}>
          <legend>Graphic {index + 1}</legend>
          <div className="row-btns">
            <Choice title="Type" field="kind" item={item} onChange={update} />
            <label>Start at narration line <select value={lineExists ? item.line : ''} disabled={!take}
              onChange={(event) => update('line', Number(event.target.value))}>
              {!lineExists && <option value="" disabled>Choose a narration line</option>}
              {take?.lines.map((line, i) => <option key={i} value={i}>
                {i + 1} · {line.start.toFixed(1)} s · {line.text.slice(0, 64)}
              </option>)}
            </select></label>
            <label>Duration <input type="number" min={0.5} max={8} step={0.5} value={item.duration}
              onChange={(event) => update('duration', Number(event.target.value))} /></label>
          </div>
          <div className="row-btns">
            <Choice title="Horizontal" field="horizontal" item={item} onChange={update} />
            <Choice title="Vertical" field="vertical" item={item} onChange={update} />
            {item.kind === 'arrow' && <Choice title="Arrow direction" field="direction" item={item} onChange={update} />}
            {item.kind === 'label' && <label>Label text <input value={item.text} maxLength={60}
              onChange={(event) => update('text', event.target.value)} /></label>}
          </div>
          <div className="row-btns">
            <button type="button" className="btn ghost" disabled={!lineExists}
              onClick={() => onPlay(take.lines[item.line].start)}>Listen from this line</button>
            <button type="button" className="btn ghost" onClick={() =>
              setItems((current) => current.filter((_, i) => i !== index))}>Remove graphic</button>
          </div>
        </fieldset>
      })}
      <button type="button" className="btn" disabled={!!busy || !take || items.length >= 12}
        onClick={() => setItems((current) => [...current, {
          kind: 'arrow', line: 0, duration: 2.5, text: '', horizontal: 'center', vertical: 'top', direction: 'right',
        }])}>Add graphic</button>
    </div>
  </details>
}
