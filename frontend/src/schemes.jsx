/* Per-scheme results: the reference notebook's output, on the dashboard.
 *
 * Layout follows ETH_WWP_WaPORv3_Irrigaed_Wheat_2026_Final.ipynb so a user who
 * knows the notebook recognises the screen: validation message, the per-feature
 * result table, the per-plot median table for point samples, then the two bar
 * charts (estimated yield and water productivity by scheme). CSV downloads
 * carry the same columns as the notebook's saved files.
 */

import { SchemeBars } from './charts'
import * as api from './api'

const NUM_COLS = new Set(['NPP', 'EYield_tpha', 'AETI_mm', 'WP_kgpm3', 'LGP', 'Area_ha',
  'ID', 'Location', 'Shape_Leng', 'Shape_Area', 'n_samples'])

/* Column headers keep the notebook's field names — they are what the user sees
   in QGIS and in the CSV — with the unit spelled out underneath. */
const UNITS = {
  NPP: 'gC/m²', EYield_tpha: 't/ha', AETI_mm: 'mm', WP_kgpm3: 'kg/m³', LGP: 'days',
  Area_ha: 'ha',
}

function cell(value) {
  if (value === null || value === undefined || value === '') return '—'
  return typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value
}

function ResultTable({ columns, rows, highlight = [], caption }) {
  return (
    <div className="tablewrap">
      <table className="dtable wide">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} scope="col" className={highlight.includes(c) ? 'res' : undefined}>
                {c}{UNITS[c] && <span className="cu"> ({UNITS[c]})</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className={NUM_COLS.has(c) ? 'num' : undefined}>{cell(r[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function SchemeResults({ data, onClose }) {
  const isPoint = data.geometry_type === 'point'
  const agg = data.aggregate
  const chartSource = agg ? 'per-plot medians' : 'each boundary'

  return (
    <aside className="results" aria-label="Scheme analysis results">
      <div className="rhead">
        <div>
          <h2>Scheme results</h2>
          <div className="sub">
            {data.filename} · {data.n_features} {isPoint ? 'sample points' : 'plot boundaries'}
            {agg ? ` · ${agg.rows.length} plots` : ''}
          </div>
          <div className="sub">
            {data.season_windows.length === 1
              ? `Season ${data.season_windows[0].sos} to ${data.season_windows[0].eos}`
              : `${data.season_windows.length} distinct growing seasons in this file`}
          </div>
        </div>
        <button className="close" onClick={onClose} title="Close results"
          aria-label="Close results">✕</button>
      </div>

      <p className="banner ok" role="status">
        <b>{data.validation.message}</b> Every feature carries the fields the method
        requires, and each one is estimated over its own SOS to EOS window.
      </p>

      {data.synthetic && (
        <p className="banner" role="note">
          <b>Demonstration data.</b> Values come from the built-in synthetic provider
          ({data.provider}), not FAO WaPOR v3. The method is the real one; the inputs
          are not. Set <code>WWP_PROVIDER=wapor</code> on the service for actual retrieval.
        </p>
      )}

      <div className="chartsec">
        <h3>Estimated yield by scheme</h3>
        <div className="note">Grain yield per plot, from {chartSource}</div>
        <SchemeBars data={data.charts} valueKey="yield_t_ha" unit="t/ha"
          label="Estimated yield" />
      </div>

      <div className="chartsec">
        <h3>Water productivity by scheme</h3>
        <div className="note">Grain produced per cubic metre of water consumed</div>
        <SchemeBars data={data.charts} valueKey="wwp" unit="kg/m³"
          label="Water productivity" />
      </div>

      {!agg && data.grouping_note && (
        <p className="banner" role="note">{data.grouping_note}</p>
      )}

      {agg && (
        <div className="chartsec">
          <h3>Per-plot medians</h3>
          <div className="note">
            Sample points collapsed to one row per plot, grouped by{' '}
            {agg.group_cols.join(', ')} — the median is used, so a single bad sample
            cannot move the plot value.
          </div>
          <ResultTable columns={[...agg.group_cols, ...agg.value_cols, 'n_samples']}
            rows={agg.rows} highlight={agg.value_cols}
            caption="Median result per plot" />
          <a className="tablebtn" href={api.schemeCsvUrl(data.run_id, 'schemes')} download>
            Download per-plot CSV
          </a>
        </div>
      )}

      <div className="chartsec">
        <h3>{isPoint ? 'Every sample point' : 'Every boundary'}</h3>
        <div className="note">
          The uploaded attributes with the five result columns appended, exactly as the
          reference notebook writes them.
        </div>
        <ResultTable columns={data.columns} rows={data.features}
          highlight={data.result_columns} caption="Result per feature" />
        <a className="tablebtn" href={api.schemeCsvUrl(data.run_id, 'features')} download>
          Download per-feature CSV
        </a>
      </div>

      <div className="chartsec">
        <h3>Method</h3>
        <div className="note">{data.method.method}</div>
        <ul className="eqlist">
          {data.method.equations.map((e) => <li key={e}><code>{e}</code></li>)}
        </ul>
        <p className="note">
          Crop parameters: AOT {data.method.crop_parameters.aot}, fc{' '}
          {data.method.crop_parameters.fc}, mc {data.method.crop_parameters.mc}, HI{' '}
          {data.method.crop_parameters.hi}. Reference: {data.method.reference}.
        </p>
      </div>
    </aside>
  )
}
