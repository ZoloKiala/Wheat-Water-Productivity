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
const POS = '#d97a1e'         // diverging: raises the prediction
const NEG = '#1f6f9c'         // diverging: lowers the prediction
const GRID = '#eceee8'
const AXIS_INK = '#8a988f'
const LABEL_INK = '#33413a'
const SURFACE = '#ffffff'

/* Bin index for a WWP value, matching the backend histogram edges. */
export function rampIndex(v) {
  if (v < 0.6) return 0
  if (v < 0.9) return 1
  if (v < 1.2) return 2
  if (v < 1.5) return 3
  return 4
}

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

/* ── Feature importance: one series, one colour ──────────────────────── */
export function FeatureChart({ data }) {
  const [tip, setTip] = useState(null)
  const rows = data.slice(0, 10)
  const rh = 13, gap = 4
  const W = 320, H = rows.length * (rh + gap) + 8
  const x0 = 132, xw = 158

  return (
    <ChartShell
      tip={tip}
      tableLabel="values"
      table={
        <table className="dtable">
          <caption className="sr-only">Model feature importance</caption>
          <thead><tr><th scope="col">Feature</th><th scope="col">Relative importance</th></tr></thead>
          <tbody>
            {rows.map((f) => (
              <tr key={f.feature}><td>{f.label}</td><td className="num">{f.importance.toFixed(0)}</td></tr>
            ))}
          </tbody>
        </table>
      }
    >
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={'Bar chart of relative feature importance. Most important: ' +
          rows.slice(0, 3).map((f) => f.label).join(', ') + '.'}
        onMouseLeave={() => setTip(null)}>
        {rows.map((f, i) => {
          const y = 4 + i * (rh + gap)
          const w = Math.max(1.5, (xw * f.importance) / 100)
          const r = Math.min(4, w)
          return (
            <g key={f.feature}>
              <text x={x0 - 7} y={y + rh - 3.5} fontSize="9.5" fill={LABEL_INK} textAnchor="end">{f.label}</text>
              {/* 4px rounded data-end, square at the x0 baseline. */}
              <path d={`M${x0},${y} L${x0 + w - r},${y} Q${x0 + w},${y} ${x0 + w},${y + r}
                        L${x0 + w},${y + rh - r} Q${x0 + w},${y + rh} ${x0 + w - r},${y + rh}
                        L${x0},${y + rh} Z`} fill={SERIES} />
              <text x={x0 + w + 5} y={y + rh - 3.5} fontSize="9" fill={AXIS_INK}
                fontVariant="tabular-nums">{f.importance.toFixed(0)}</text>
              <rect x={0} y={y - gap / 2} width={W} height={rh + gap} fill="transparent"
                onMouseEnter={() => setTip({
                  x: '50%', y: y - 2,
                  rows: [{ label: f.label, value: `${f.importance.toFixed(0)} / 100` }],
                })} />
            </g>
          )
        })}
      </svg>
    </ChartShell>
  )
}

/* ── Prediction explanation: diverging contributions ─────────────────── */
export function ShapChart({ data }) {
  const [tip, setTip] = useState(null)
  const rows = data.contributions
  const rh = 14, gap = 7
  const W = 320, H = rows.length * (rh + gap) + 34
  const x0 = 128, xm = 214, arm = 78
  const peak = Math.max(...rows.map((r) => Math.abs(r.contribution)), 0.01)

  return (
    <ChartShell
      tip={tip}
      tableLabel="values"
      table={
        <table className="dtable">
          <caption className="sr-only">Feature contributions to this prediction</caption>
          <thead>
            <tr><th scope="col">Feature</th><th scope="col">Value</th><th scope="col">Effect (kg/m³)</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.feature}>
                <td>{r.label}</td>
                <td className="num">{r.value.toLocaleString()} {r.unit}</td>
                <td className="num">{r.contribution >= 0 ? '+' : ''}{r.contribution.toFixed(3)}</td>
              </tr>
            ))}
            <tr>
              <td><b>Base → prediction</b></td>
              <td className="num">{data.base.toFixed(2)}</td>
              <td className="num"><b>{data.prediction.toFixed(2)}</b></td>
            </tr>
          </tbody>
        </table>
      }
    >
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`Diverging bar chart. Base value ${data.base.toFixed(2)} to prediction ${data.prediction.toFixed(2)} kilograms per cubic metre. ` +
          rows.map((r) => `${r.label} ${r.contribution >= 0 ? 'raises' : 'lowers'} it by ${Math.abs(r.contribution).toFixed(2)}`).join('; ') + '.'}
        onMouseLeave={() => setTip(null)}>
        <text x={xm} y={11} fontSize="9.5" fill={AXIS_INK} textAnchor="middle">
          base {data.base.toFixed(2)} → prediction {data.prediction.toFixed(2)} kg/m³
        </text>
        {/* Neutral zero line: the diverging midpoint reads as "no effect". */}
        <line x1={xm} y1={18} x2={xm} y2={H - 16} stroke="#c9cfc6" strokeWidth="1" />
        {rows.map((r, i) => {
          const y = 22 + i * (rh + gap)
          const w = Math.max(1.5, (Math.abs(r.contribution) / peak) * arm)
          const up = r.contribution >= 0
          const x = up ? xm : xm - w
          const rr = Math.min(4, w)
          const d = up
            ? `M${xm},${y} L${x + w - rr},${y} Q${x + w},${y} ${x + w},${y + rr}
               L${x + w},${y + rh - rr} Q${x + w},${y + rh} ${x + w - rr},${y + rh} L${xm},${y + rh} Z`
            : `M${xm},${y} L${x + rr},${y} Q${x},${y} ${x},${y + rr}
               L${x},${y + rh - rr} Q${x},${y + rh} ${x + rr},${y + rh} L${xm},${y + rh} Z`
          return (
            <g key={r.feature}>
              <text x={x0 - 7} y={y + rh - 3.5} fontSize="9.5" fill={LABEL_INK} textAnchor="end">{r.label}</text>
              <path d={d} fill={up ? POS : NEG} />
              {/* Signed value label: direction is encoded by side AND by sign,
                  so the diverging hues are never the only channel. */}
              <text x={up ? x + w + 5 : x - 5} y={y + rh - 3.5} fontSize="9" fontWeight="700"
                fill={LABEL_INK} textAnchor={up ? 'start' : 'end'}>
                {up ? '+' : '−'}{Math.abs(r.contribution).toFixed(2)}
              </text>
              <rect x={0} y={y - gap / 2} width={W} height={rh + gap} fill="transparent"
                onMouseEnter={() => setTip({
                  x: '50%', y: y - 2,
                  rows: [
                    { label: r.label, value: `${r.value.toLocaleString()} ${r.unit}` },
                    { label: up ? 'raises WWP by' : 'lowers WWP by', value: `${Math.abs(r.contribution).toFixed(3)} kg/m³` },
                  ],
                })} />
            </g>
          )
        })}
        {/* Legend: two series, so identity never rests on colour alone. */}
        <g fontSize="9" fill={AXIS_INK}>
          <rect x={x0 - 4} y={H - 12} width="8" height="8" rx="2" fill={POS} />
          <text x={x0 + 8} y={H - 5}>raises</text>
          <rect x={x0 + 44} y={H - 12} width="8" height="8" rx="2" fill={NEG} />
          <text x={x0 + 56} y={H - 5}>lowers</text>
        </g>
      </svg>
    </ChartShell>
  )
}
