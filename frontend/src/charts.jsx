/* Inline-SVG charts for the results panel.
 *
 * Shared conventions (see docs/ARCHITECTURE.md § Visualization palette):
 *  - 2px lines, >=8px markers with a 2px surface ring, 4px rounded bar ends
 *    anchored square to the baseline, 2px surface gaps between adjacent bars.
 *  - Solid hairline gridlines one step off the surface; never dashed.
 *  - Labels are selective, and always in text tokens, never the mark colour.
 *  - Every chart has a hover tooltip AND a table view, so no value is reachable
 *    only by hovering.
 */

import { useState } from 'react'

export const RAMP_HEX = ['#93bd82', '#6ba763', '#458b4b', '#297038', '#0f4d26']
const SERIES = '#297038'      // single-series mark colour
const GRID = '#eceee8'
const AXIS_INK = '#8a988f'
const LABEL_INK = '#33413a'
const SURFACE = '#ffffff'

/* ── shared shell: chart + tooltip + table toggle ─────────────────────── */
function ChartShell({ children, tip, table, tableLabel = 'table' }) {
  const [showTable, setShowTable] = useState(false)
  return (
    <>
      <div className="chartbox">
        {children}
        {tip && (
          <div className="ctip" style={{ left: tip.x, top: tip.y }} role="status">
            {tip.rows.map((r, i) => (
              <div key={i}>
                {r.label && <span className="cl">{r.label} </span>}
                <span className="cv">{r.value}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <button className="tablebtn" onClick={() => setShowTable((s) => !s)}
        aria-expanded={showTable}>
        {showTable ? 'Hide' : 'Show'} {tableLabel}
      </button>
      {showTable && table}
    </>
  )
}

/* ── Seasonal trend: single series line ──────────────────────────────── */
export function TrendChart({ data, currentYear }) {
  const [tip, setTip] = useState(null)
  const W = 320, H = 128
  // x1 leaves room for the last season label (~37px wide, centre-anchored) so
  // it cannot spill past the viewBox.
  const x0 = 40, x1 = 284, yTop = 16, yBase = 96
  const vals = data.map((d) => d.mean)
  const pad = Math.max(0.08, (Math.max(...vals) - Math.min(...vals)) * 0.25)
  const vmin = Math.min(...vals) - pad, vmax = Math.max(...vals) + pad
  const X = (i) => x0 + ((x1 - x0) * i) / Math.max(1, data.length - 1)
  const Y = (v) => yBase - ((yBase - yTop) * (v - vmin)) / (vmax - vmin)
  const curIdx = data.findIndex((d) => d.year === currentYear)
  const line = data.map((d, i) => `${X(i)},${Y(d.mean)}`).join(' ')

  return (
    <ChartShell
      tip={tip}
      table={
        <table className="dtable">
          <caption className="sr-only">Mean wheat water productivity by season</caption>
          <thead><tr><th scope="col">Season</th><th scope="col">Mean WWP (kg/m³)</th></tr></thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.year}>
                <td>{d.year}{d.year === currentYear ? ' (selected)' : ''}</td>
                <td className="num">{d.mean.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`Line chart of mean wheat water productivity across ${data.length} seasons, from ${vals[0].toFixed(2)} to ${vals[vals.length - 1].toFixed(2)} kilograms per cubic metre.`}
        onMouseLeave={() => setTip(null)}>
        {[0, 1, 2].map((g) => {
          const v = vmin + ((vmax - vmin) * g) / 2
          return (
            <g key={g}>
              <line x1={x0} y1={Y(v)} x2={x1} y2={Y(v)} stroke={GRID} strokeWidth="1" />
              <text x={x0 - 7} y={Y(v) + 3} fontSize="9" fill={AXIS_INK} textAnchor="end">{v.toFixed(2)}</text>
            </g>
          )
        })}
        <polyline points={line} fill="none" stroke={SERIES} strokeWidth="2"
          strokeLinejoin="round" strokeLinecap="round" />
        {data.map((d, i) => {
          const cur = i === curIdx
          return (
            <g key={d.year}>
              <circle cx={X(i)} cy={Y(d.mean)} r={cur ? 5 : 4} fill={cur ? '#b07f28' : SERIES}
                stroke={SURFACE} strokeWidth="2" />
              <text x={X(i)} y={H - 4} fontSize="9" fill={AXIS_INK} textAnchor="middle">{d.year}</text>
              {/* Selective direct label: only the selected season. */}
              {cur && (
                <text x={X(i)} y={Y(d.mean) - 11} fontSize="10" fontWeight="700"
                  fill={LABEL_INK} textAnchor="middle">{d.mean.toFixed(2)}</text>
              )}
              {/* Generous hit target (>=24px band) for hover. */}
              <rect x={X(i) - 14} y={yTop - 6} width="28" height={yBase - yTop + 16} fill="transparent"
                onMouseEnter={() => setTip({
                  x: X(i) / W * 100 + '%', y: Y(d.mean) - 14,
                  rows: [{ label: d.year, value: `${d.mean.toFixed(2)} kg/m³` }],
                })} />
            </g>
          )
        })}
      </svg>
    </ChartShell>
  )
}

/* ── Distribution: ordered classes on the WWP ramp ───────────────────── */
export function HistChart({ data }) {
  const [tip, setTip] = useState(null)
  const W = 320, H = 118
  const yBase = 86, maxH = 64
  const slot = (W - 24) / data.length
  const bw = Math.min(24, slot - 2)   // cap bar thickness; 2px surface gap
  const peak = Math.max(...data.map((d) => d.pct), 1)

  return (
    <ChartShell
      tip={tip}
      table={
        <table className="dtable">
          <caption className="sr-only">Share of wheat area by water-productivity class</caption>
          <thead><tr><th scope="col">Class (kg/m³)</th><th scope="col">Share of area</th></tr></thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.label}><td>{d.label}</td><td className="num">{d.pct.toFixed(1)}%</td></tr>
            ))}
          </tbody>
        </table>
      }
    >
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={'Column chart of the share of wheat area in each water-productivity class: ' +
          data.map((d) => `${d.label}, ${d.pct.toFixed(0)} percent`).join('; ') + '.'}
        onMouseLeave={() => setTip(null)}>
        <line x1={12} y1={yBase} x2={W - 12} y2={yBase} stroke={GRID} strokeWidth="1" />
        {data.map((d, i) => {
          const h = d.pct === 0 ? 0 : Math.max(2, (d.pct / peak) * maxH)
          const x = 12 + i * slot + (slot - bw) / 2
          const y = yBase - h
          return (
            <g key={d.label}>
              {h > 0 && (
                /* 4px rounded cap, square at the baseline. */
                <path d={`M${x},${yBase} L${x},${y + Math.min(4, h)} Q${x},${y} ${x + Math.min(4, h)},${y}
                          L${x + bw - Math.min(4, h)},${y} Q${x + bw},${y} ${x + bw},${y + Math.min(4, h)}
                          L${x + bw},${yBase} Z`} fill={RAMP_HEX[i]} />
              )}
              {/* Label above the cap — outside the mark, never clipped. */}
              <text x={x + bw / 2} y={y - 6} fontSize="9.5" fontWeight="700" fill={LABEL_INK}
                textAnchor="middle">{d.pct >= 0.1 ? `${Math.round(d.pct)}%` : '0'}</text>
              <text x={x + bw / 2} y={yBase + 13} fontSize="8.5" fill={AXIS_INK}
                textAnchor="middle">{d.label}</text>
              <rect x={12 + i * slot} y={yBase - maxH - 12} width={slot} height={maxH + 24}
                fill="transparent"
                onMouseEnter={() => setTip({
                  x: ((12 + i * slot + slot / 2) / W) * 100 + '%', y: y - 12,
                  rows: [{ label: `${d.label} kg/m³`, value: `${d.pct.toFixed(1)}% of area` }],
                })} />
            </g>
          )
        })}
        <text x={W / 2} y={H - 2} fontSize="9" fill={AXIS_INK} textAnchor="middle">
          wheat water productivity (kg/m³)
        </text>
      </svg>
    </ChartShell>
  )
}


/* ── Per-scheme bars: the reference notebook's two figures ───────────────
 * Notebook cells 30 and 31 plot estimated yield and water productivity per
 * scheme. Same encoding here (one bar per plot, ordered as the table is), with
 * the house conventions added: a tooltip, a table view, and labels in text
 * tokens rather than in the mark colour.
 */
export function SchemeBars({ data, valueKey, unit, label, digits = 2 }) {
  const [tip, setTip] = useState(null)
  const W = 320, yBase = 96, maxH = 70
  const rows = data.filter((d) => typeof d[valueKey] === 'number')
  if (!rows.length) return <p className="note">No values to plot.</p>
  const slot = (W - 24) / rows.length
  const bw = Math.min(28, Math.max(4, slot - 2))
  const peak = Math.max(...rows.map((d) => d[valueKey]), 0.001)
  // Long names on a narrow slot are unreadable at any angle, so they are
  // clipped to what fits and the full name stays in the tooltip and table.
  const maxChars = Math.max(3, Math.floor(slot / 4.6))
  const short = (t) => (t.length > maxChars ? t.slice(0, maxChars - 1) + '…' : t)

  return (
    <ChartShell
      tip={tip}
      table={
        <table className="dtable">
          <caption className="sr-only">{label} by scheme</caption>
          <thead>
            <tr>
              <th scope="col">Scheme</th><th scope="col">Code</th>
              <th scope="col">{label} ({unit})</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((d, i) => (
              <tr key={i}>
                <td>{d.label}</td><td>{d.scheme}</td>
                <td className="num">{d[valueKey].toFixed(digits)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <svg width="100%" height={124} viewBox={`0 0 ${W} 124`} role="img"
        aria-label={`Column chart of ${label} in ${unit} by scheme: ` +
          rows.map((d) => `${d.label}, ${d[valueKey].toFixed(digits)}`).join('; ') + '.'}
        onMouseLeave={() => setTip(null)}>
        <line x1={12} y1={yBase} x2={W - 12} y2={yBase} stroke={GRID} strokeWidth="1" />
        {rows.map((d, i) => {
          const v = d[valueKey]
          const h = v <= 0 ? 0 : Math.max(2, (v / peak) * maxH)
          const x = 12 + i * slot + (slot - bw) / 2
          const y = yBase - h
          const r = Math.min(4, h)
          return (
            <g key={i}>
              {h > 0 && (
                <path d={`M${x},${yBase} L${x},${y + r} Q${x},${y} ${x + r},${y}
                          L${x + bw - r},${y} Q${x + bw},${y} ${x + bw},${y + r}
                          L${x + bw},${yBase} Z`} fill={RAMP_HEX[i % RAMP_HEX.length]} />
              )}
              {rows.length <= 10 && (
                <text x={x + bw / 2} y={y - 6} fontSize="9.5" fontWeight="700"
                  fill={LABEL_INK} textAnchor="middle">{v.toFixed(digits)}</text>
              )}
              <text x={x + bw / 2} y={yBase + 13} fontSize="8.5" fill={AXIS_INK}
                textAnchor="middle">{short(String(d.label))}</text>
              <rect x={12 + i * slot} y={yBase - maxH - 12} width={slot} height={maxH + 24}
                fill="transparent"
                onMouseEnter={() => setTip({
                  x: ((12 + i * slot + slot / 2) / W) * 100 + '%', y: Math.max(0, y - 12),
                  rows: [
                    { label: d.label, value: `${v.toFixed(digits)} ${unit}` },
                    ...(d.scheme ? [{ label: 'Scheme', value: d.scheme }] : []),
                  ],
                })} />
            </g>
          )
        })}
        <text x={W / 2} y={122} fontSize="9" fill={AXIS_INK} textAnchor="middle">
          {label} ({unit})
        </text>
      </svg>
    </ChartShell>
  )
}


/* ── Estimation chain: how one value was derived ─────────────────────────
 * The method is deterministic, so the explanation is the derivation itself:
 * each measured input, each parameter applied, each intermediate. Laid out as
 * text rather than as a chart because every element here IS a number with a
 * name — there is no magnitude comparison a mark would make clearer.
 */
const fmtValue = (v) =>
  Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : v.toFixed(v < 10 ? 3 : 1)

export function ChainChart({ steps }) {
  return (
    <ol className="chain">
      {steps.map((s) => (
        <li key={s.step} className={s.role}>
          {s.role !== 'source' && (
            <div className="op" aria-hidden="true">
              <span className="sym">{s.role === 'divisor' ? '÷' : s.role === 'result' ? '=' : '×'}</span>
              <span>{s.detail}</span>
            </div>
          )}
          <div className="node">
            <span className="nm">{s.step}</span>
            <span className="val">
              {fmtValue(s.value)}<em>{s.unit}</em>
            </span>
          </div>
          {s.role === 'source' && <div className="src">{s.detail}</div>}
        </li>
      ))}
    </ol>
  )
}
