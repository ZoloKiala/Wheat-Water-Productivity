import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as api from './api'
import MapView from './MapView'
import { PAGES } from './content'
import { ChainChart, HistChart, TrendChart } from './charts'
import SchemeResults from './schemes'

const STEPS = [
  ['Retrieving WaPOR NPP and AETI…', 'Dekadal rasters, summed over the season'],
  ['Converting NPP to biomass…', 'AOT · fc · 22.222 ÷ (1 − mc)'],
  ['Computing yield and water productivity…', 'Grain yield ÷ water consumed'],
]

function polygonAreaHa(poly) {
  let a = 0
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    a += (poly[j][1] + poly[i][1]) * (poly[j][0] - poly[i][0])
  }
  const kmPerDeg = 110.57
  return Math.abs(a / 2) * kmPerDeg * kmPerDeg * Math.cos((poly[0][0] * Math.PI) / 180) * 100
}

export default function App() {
  /* ── reference data ─────────────────────────────────────────────────── */
  const [units, setUnits] = useState(null)
  const [method, setMethod] = useState(null)
  const [bootError, setBootError] = useState(null)

  /* ── AOI ────────────────────────────────────────────────────────────── */
  const [aoiTab, setAoiTab] = useState('admin')
  const [region, setRegion] = useState('')
  const [zone, setZone] = useState('')
  const [woreda, setWoreda] = useState('')
  const [upload, setUpload] = useState(null)
  const [datasets, setDatasets] = useState(null)
  const [schemeResult, setSchemeResult] = useState(null)
  const [schemeBusy, setSchemeBusy] = useState(false)
  const [uploadBusy, setUploadBusy] = useState(false)
  const [uploadError, setUploadError] = useState(null)
  const [dropHot, setDropHot] = useState(false)
  const [drawing, setDrawing] = useState(false)
  const [drawPoints, setDrawPoints] = useState([])
  const [polygon, setPolygon] = useState(null)

  /* ── analysis parameters ────────────────────────────────────────────── */
  const [system, setSystem] = useState('rainfed')
  const [year, setYear] = useState('2024/25')
  const [season, setSeason] = useState('Meher')

  /* ── run state ──────────────────────────────────────────────────────── */
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState(0)
  const [result, setResult] = useState(null)
  const [resultsOpen, setResultsOpen] = useState(false)
  const [runError, setRunError] = useState(null)
  const [explain, setExplain] = useState(null)
  const [marker, setMarker] = useState(null)
  const [popup, setPopup] = useState(null)

  /* ── chrome ─────────────────────────────────────────────────────────── */
  const [page, setPage] = useState(null)
  const [toast, setToast] = useState(null)
  const [fitKey, setFitKey] = useState(0)
  const fitBounds = useRef(null)
  const explainRef = useRef(null)
  const toastTimer = useRef(null)

  const notify = useCallback((message, bad = false) => {
    setToast({ message, bad })
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 4000)
  }, [])

  const fit = useCallback((bounds) => {
    if (!bounds) return
    fitBounds.current = bounds
    setFitKey((k) => k + 1)
  }, [])

  /* ── boot ───────────────────────────────────────────────────────────── */
  useEffect(() => {
    api.getAdminUnits().then((u) => {
      setUnits(u)
      const r = Object.keys(u.tree)[0]
      const z = Object.keys(u.tree[r])[0]
      setRegion(r); setZone(z); setWoreda(u.tree[r][z][0])
      setYear(u.years.includes('2024/25') ? '2024/25' : u.years[0])
    }).catch((e) => setBootError(e.message))
    api.getMethod().then(setMethod).catch(() => { /* non-blocking */ })
    /* Which scheme files this deployment can offer. Non-blocking: without it
       the upload panel simply has nothing to load, and uploads still work. */
    api.getSchemeDatasets().then((d) => setDatasets(d.datasets || []))
      .catch(() => setDatasets([]))
  }, [])

  /* keep zone/woreda valid when the parent changes */
  useEffect(() => {
    if (!units || !region) return
    const zones = Object.keys(units.tree[region])
    if (!zones.includes(zone)) { setZone(zones[0]); return }
    const woredas = units.tree[region][zone]
    if (!woredas.includes(woreda)) setWoreda(woredas[0])
  }, [units, region, zone, woreda])

  /* season list follows the production system */
  useEffect(() => {
    if (!units) return
    const valid = units.seasons[system]
    if (!valid.includes(season)) setSeason(valid[0])
  }, [units, system, season])

  /* Esc cancels drawing */
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && drawing) cancelDraw() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [drawing])

  /* ── derived AOI ────────────────────────────────────────────────────── */
  const currentAoi = useMemo(() => {
    if (aoiTab === 'admin') {
      if (!units || !woreda) return null
      return { kind: 'admin', label: `${woreda} woreda, ${zone} (${region})` }
    }
    if (aoiTab === 'upload') return upload ? { kind: 'upload', ...upload } : null
    if (aoiTab === 'draw') {
      if (!polygon) return null
      const lats = polygon.map((p) => p[0]), lons = polygon.map((p) => p[1])
      return {
        kind: 'polygon', label: 'Drawn polygon', polys: [polygon],
        bounds: [[Math.min(...lats), Math.min(...lons)], [Math.max(...lats), Math.max(...lons)]],
      }
    }
    return null
  }, [aoiTab, units, region, zone, woreda, upload, polygon])

  /* Outline: for admin units the backend owns the extent, so the outline is
     only drawn once a result (or an upload/polygon) gives us real bounds. */
  const aoiOutline = useMemo(() => {
    if (aoiTab === 'admin') {
      return result && result.label === currentAoi?.label ? { bounds: result.bounds } : null
    }
    if (currentAoi?.geometry_type === 'point' && currentAoi.geojson?.coordinates?.length) {
      return { points: currentAoi.geojson.coordinates.map(([lon, lat]) => [lat, lon]) }
    }
    if (currentAoi?.polys) return { polys: currentAoi.polys }
    if (currentAoi?.bounds) return { bounds: currentAoi.bounds }
    return null
  }, [aoiTab, currentAoi, result])

  useEffect(() => {
    if (currentAoi?.bounds) fit(currentAoi.bounds)
  }, [currentAoi?.bounds, fit])

  const requestBody = useCallback(() => {
    const base = { system, year, season }
    if (aoiTab === 'admin') return { ...base, aoi_type: 'admin', region, zone, woreda }
    if (aoiTab === 'upload') return { ...base, aoi_type: 'upload', upload_id: upload.upload_id }
    return { ...base, aoi_type: 'polygon', polygon }
  }, [aoiTab, region, zone, woreda, upload, polygon, system, year, season])

  /* ── actions ────────────────────────────────────────────────────────── */
  async function handleUpload(file) {
    setUploadError(null); setUploadBusy(true)
    try {
      const rec = await api.uploadBoundary(file)
      setUpload(rec)
      setSchemeResult(null)
      fit(rec.bounds)
      const what = rec.geometry_type === 'point'
        ? `${rec.n_features} sample point${rec.n_features > 1 ? 's' : ''}`
        : `${rec.n_polygons} polygon${rec.n_polygons > 1 ? 's' : ''}`
      notify(`File validated: ${what}, ${rec.crs}`)
    } catch (e) {
      setUpload(null)
      setSchemeResult(null)
      setUploadError(e.message)
    } finally {
      setUploadBusy(false)
    }
  }

  function cancelDraw() {
    setDrawing(false); setDrawPoints([])
  }

  function finishDraw() {
    if (drawPoints.length < 3) {
      notify('A polygon needs at least 3 vertices.', true)
      return
    }
    setPolygon(drawPoints)
    setDrawing(false)
    setDrawPoints([])
  }

  /* Loads a ready-made scheme file through the ordinary upload path, so what
     the user tries is the real journey, validation included, not a canned
     result. The 2026 campaign shapefiles come through here too, which is why
     they need no special case anywhere downstream. */
  async function loadDataset(name) {
    setUploadError(null)
    try {
      const s = await api.getSchemeDataset(name)
      const file = new File([JSON.stringify(s.geojson)], s.filename,
        { type: 'application/geo+json' })
      await handleUpload(file)
    } catch (e) {
      setUploadError(e.message)
    }
  }

  /* The notebook's workflow: estimate every feature in the uploaded file over
     its own SOS/EOS window, rather than gridding one merged extent. */
  async function runSchemes() {
    if (!upload) return
    setSchemeBusy(true); setRunError(null)
    const key = `s-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    try {
      const res = await api.runSchemeAnalysis(upload.upload_id, key)
      setSchemeResult(res)
      setResult(null)
      setResultsOpen(true)
      if (res.bounds) fit(res.bounds)
      notify(`${res.n_features} feature${res.n_features > 1 ? 's' : ''} estimated.`)
    } catch (e) {
      setRunError(e.message)
      notify(e.message, true)
    } finally {
      setSchemeBusy(false)
    }
  }

  async function run() {
    if (!currentAoi) {
      notify(aoiTab === 'upload' ? 'Upload a boundary file first.' : 'Draw a polygon first.', true)
      return
    }
    setBusy(true); setStep(0); setRunError(null)
    setExplain(null); setMarker(null); setPopup(null); setSchemeResult(null)
    const ticker = setInterval(() => setStep((s) => Math.min(s + 1, STEPS.length - 1)), 700)
    const key = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    try {
      const res = await api.runAnalysis(requestBody(), key)
      setResult(res)
      setResultsOpen(true)
      fit(res.bounds)
      if (res.cached) notify('Loaded a cached result for this selection.')
    } catch (e) {
      setRunError(e.message)
      notify(e.message, true)
    } finally {
      clearInterval(ticker)
      setBusy(false)
    }
  }

  async function inspect(lat, lng) {
    if (!result) return
    const [[s, w], [n, e]] = result.bounds
    if (lat < s || lat > n || lng < w || lng > e) return
    try {
      const p = await api.predictPoint({ lat, lon: lng, system, year, season })
      setMarker({ lat, lng })
      setPopup({
        lat, lng,
        html: `<div class="pop"><h5>Wheat water productivity</h5>
          <span class="pv">${p.wwp.toFixed(2)}</span> <span class="pu">kg/m³</span>
          <table>
            <tr><td>Estimated yield</td><td>${p.yield_t_ha.toFixed(2)} t/ha</td></tr>
            <tr><td>Seasonal NPP</td><td>${p.npp.toLocaleString()} gC/m²</td></tr>
            <tr><td>Seasonal AETI</td><td>${p.aeti_mm} mm</td></tr>
            <tr><td>Location</td><td>${lat.toFixed(4)}, ${lng.toFixed(4)}</td></tr>
          </table>
          <button class="explink" id="explainLink" type="button">Show the derivation →</button>
          </div>`,
      })
    } catch (err) {
      notify(err.message, true)
    }
  }

  async function loadExplanation() {
    if (!marker) return
    try {
      const ex = await api.explainPoint({ lat: marker.lat, lon: marker.lng, system, year, season })
      setExplain(ex)
      setResultsOpen(true)   // the explanation renders in the results panel
      setTimeout(() => explainRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60)
    } catch (e) {
      notify(e.message, true)
    }
  }

  function exportCsv() {
    if (!result) return
    const a = document.createElement('a')
    a.href = api.csvUrl(result.run_id)
    a.download = 'wwp_results.csv'
    a.click()
    notify('Preparing CSV download…')
  }

  /* ── boot failure ───────────────────────────────────────────────────── */
  if (bootError) {
    return (
      <div style={{ padding: 40, maxWidth: 560, margin: '60px auto', textAlign: 'center' }}>
        <h1 style={{ fontSize: 18, marginBottom: 10 }}>Dashboard unavailable</h1>
        <p style={{ color: 'var(--muted)', lineHeight: 1.6, fontSize: 13 }}>
          {bootError} The analysis service may not be running. Start it with{' '}
          <code>uvicorn app.main:app</code> from the <code>backend</code> directory, then reload.
        </p>
      </div>
    )
  }

  const seasons = units?.seasons[system] ?? []
  const canRun = !busy && !!currentAoi

  return (
    <>
      <header className="hdr">
        <div className="brand">
          <div className="mark" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path d="M12 21V9" stroke="#f3e5c3" strokeWidth="1.6" strokeLinecap="round" />
              <path d="M12 9c0-3 2-5 5-5 0 3-2 5-5 5Zm0 4c0-3 2-5 5-5 0 3-2 5-5 5Zm0 4c0-3 2-5 5-5 0 3-2 5-5 5Zm0-8c0-3-2-5-5-5 0 3 2 5 5 5Zm0 4c0-3-2-5-5-5 0 3 2 5 5 5Zm0 4c0-3-2-5-5-5 0 3 2 5 5 5Z" fill="#e8c877" />
            </svg>
          </div>
          <div>
            <div className="t1">Wheat Water Productivity</div>
            <div className="t2">Ethiopian Institute of Agricultural Research</div>
          </div>
        </div>
        <nav aria-label="Dashboard sections">
          <button className="on" onClick={() => setPage(null)}>Dashboard</button>
          <button onClick={() => setPage('method')}>Methodology</button>
          <button onClick={() => setPage('data')}>Data sources</button>
          <button onClick={() => setPage('guide')}>User guide</button>
        </nav>
        <div className="right">
          <button className="lang" onClick={() => notify('Amharic interface is planned for deployment.')}>
            EN ▾
          </button>
        </div>
      </header>

      <div className="app">
        {/* ───────── Sidebar ───────── */}
        <aside className="side" aria-label="Analysis controls">
          <div className="sec">
            <h3><span className="stepnum">1</span>Area of interest</h3>
            <div className="tabs" role="tablist" aria-label="Area of interest method">
              {[['admin', 'Admin unit'], ['upload', 'Upload'], ['draw', 'Draw']].map(([k, label]) => (
                <button key={k} role="tab" aria-selected={aoiTab === k}
                  className={aoiTab === k ? 'on' : ''}
                  onClick={() => { setAoiTab(k); if (drawing) cancelDraw() }}>{label}</button>
              ))}
            </div>

            {aoiTab === 'admin' && (
              <div>
                <div className="fld">
                  <label htmlFor="selRegion">Region</label>
                  <select id="selRegion" value={region} onChange={(e) => setRegion(e.target.value)}>
                    {units && Object.keys(units.tree).map((r) => <option key={r}>{r}</option>)}
                  </select>
                </div>
                <div className="fld">
                  <label htmlFor="selZone">Zone</label>
                  <select id="selZone" value={zone} onChange={(e) => setZone(e.target.value)}>
                    {units && region && Object.keys(units.tree[region]).map((z) => <option key={z}>{z}</option>)}
                  </select>
                </div>
                <div className="fld">
                  <label htmlFor="selWoreda">Woreda</label>
                  <select id="selWoreda" value={woreda} onChange={(e) => setWoreda(e.target.value)}>
                    {units && region && units.tree[region][zone]?.map((w) => <option key={w}>{w}</option>)}
                  </select>
                </div>
                <p className="hint">Analysis runs on the selected woreda extent. Boundaries follow
                  CSA 2023 administrative units.</p>
              </div>
            )}

            {aoiTab === 'upload' && (
              <div>
                <button className={`drop${dropHot ? ' hot' : ''}`} type="button"
                  onClick={() => document.getElementById('fileInput').click()}
                  onDragOver={(e) => { e.preventDefault(); setDropHot(true) }}
                  onDragLeave={() => setDropHot(false)}
                  onDrop={(e) => {
                    e.preventDefault(); setDropHot(false)
                    if (e.dataTransfer.files[0]) handleUpload(e.dataTransfer.files[0])
                  }}>
                  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#27824d"
                    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <path d="M17 8l-5-5-5 5" /><path d="M12 3v12" />
                  </svg>
                  <div className="big">{uploadBusy ? 'Validating…' : 'Upload field or scheme boundary'}</div>
                  <div className="small">Zipped shapefile (.zip) or GeoJSON, max 20 MB, EPSG:4326</div>
                </button>
                <input type="file" id="fileInput" accept=".zip,.geojson,.json" style={{ display: 'none' }}
                  onChange={(e) => { if (e.target.files[0]) handleUpload(e.target.files[0]); e.target.value = '' }} />
                {upload && (
                  <div className="filechip">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#1a5735"
                      strokeWidth="2" aria-hidden="true"><path d="M20 6L9 17l-5-5" /></svg>
                    <span>{upload.label.replace('Uploaded boundary — ', '')} · {upload.geometry_type === 'point'
                      ? `${upload.n_features} point${upload.n_features > 1 ? 's' : ''}`
                      : `${upload.area_ha.toLocaleString()} ha`}</span>
                    <button className="x" onClick={() => { setUpload(null); setUploadError(null) }}
                      aria-label="Remove uploaded boundary">✕</button>
                  </div>
                )}
                {upload && upload.scheme_ready && (
                  /* The file carries ID/SOS/EOS per feature, so it can drive the
                     reference notebook's own workflow: every plot estimated over
                     its own season, rather than one merged extent. */
                  <div className="schemeask">
                    <div className="small">
                      {upload.n_features} feature{upload.n_features > 1 ? 's' : ''} with
                      per-feature growing seasons{upload.geometry_type === 'point'
                        ? ' (sample points)' : ''}.
                    </div>
                    <button className="runbtn alt" onClick={runSchemes} disabled={schemeBusy}>
                      {schemeBusy ? 'Estimating…' : 'Estimate each plot'}
                    </button>
                  </div>
                )}
                {upload && !upload.scheme_ready && upload.validation && (
                  <p className="hint warn">
                    Per-plot estimation needs ID, SOS and EOS on every feature
                    {upload.geometry_type === 'point' ? ', plus Location' : ''}.
                    {' '}{upload.validation.problems[0]}
                  </p>
                )}
                {uploadError && (
                  <p className="err" role="alert">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                      strokeWidth="2.2" style={{ flexShrink: 0, marginTop: 1 }} aria-hidden="true">
                      <circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" />
                    </svg>
                    {uploadError}
                  </p>
                )}
                <p className="hint">A file whose features carry <code>ID</code>, <code>SOS</code> and
                  <code> EOS</code> (plus <code>Location</code> for sample points) can be estimated
                  plot by plot, each over its own growing season, the way the WWPT notebook does it.</p>
                {datasets && datasets.length > 0 && (
                  /* Ready-made files, campaign data first where this machine has
                     it. Listed from the service rather than hard-coded: a public
                     deployment has only the generated sample, and the panel then
                     offers only that rather than a link that cannot work. */
                  <div className="datasets">
                    <div className="lbl">Or load a ready-made file</div>
                    {datasets.map((ds) => (
                      <div className="ds" key={ds.name}>
                        <button className="linkbtn" type="button" data-dataset={ds.name}
                          disabled={uploadBusy} onClick={() => loadDataset(ds.name)}>
                          {ds.label}
                        </button>
                        <span className="meta">{ds.n_features}{' '}
                          {ds.geometry_type === 'point' ? 'sample points' : 'plot boundaries'}</span>
                        <span className="note">{ds.note}</span>
                      </div>
                    ))}
                    {datasets.some((ds) => ds.kind === 'campaign') && (
                      <div className="src">Campaign files are IWMI field data, read
                        from this machine — they are not part of the service.</div>
                    )}
                  </div>
                )}
              </div>
            )}

            {aoiTab === 'draw' && (
              <div>
                <button className={`drawbtn${drawing ? ' armed' : ''}`}
                  onClick={() => (drawing ? cancelDraw() : (setDrawing(true), setPolygon(null)))}>
                  {drawing ? 'Cancel drawing' : 'Draw polygon on map'}
                </button>
                {drawing && (
                  /* Explicit finish control: double-click is unavailable to
                     keyboard and touch users, so it is never the only way. */
                  <button className="drawbtn" style={{ marginTop: 8 }}
                    onClick={finishDraw} disabled={drawPoints.length < 3}>
                    Finish polygon ({drawPoints.length} {drawPoints.length === 1 ? 'vertex' : 'vertices'})
                  </button>
                )}
                <p className="hint">Click the map to add vertices, then double-click the map or
                  choose <b>Finish polygon</b>. Press Esc to cancel.</p>
                {polygon && (
                  <div className="filechip">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#1a5735"
                      strokeWidth="2" aria-hidden="true"><path d="M20 6L9 17l-5-5" /></svg>
                    <span>Polygon · {polygon.length} vertices · ~{Math.round(polygonAreaHa(polygon)).toLocaleString()} ha</span>
                    <button className="x" onClick={() => setPolygon(null)}
                      aria-label="Remove drawn polygon">✕</button>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="sec">
            <h3><span className="stepnum">2</span>Production system</h3>
            <div className="seg">
              {[['rainfed', 'Rainfed'], ['irrigated', 'Irrigated']].map(([k, label]) => (
                <button key={k} className={system === k ? 'on' : ''} aria-pressed={system === k}
                  onClick={() => setSystem(k)}>{label}</button>
              ))}
            </div>
            <p className="hint">
              {system === 'irrigated'
                ? 'Irrigated analysis covers dry-season wheat on scheme or pump-based systems.'
                : 'Rainfed analysis uses the Meher and Belg growing seasons.'}
            </p>
          </div>

          <div className="sec">
            <h3><span className="stepnum">3</span>Season</h3>
            <div className="row2">
              <div className="fld">
                <label htmlFor="selYear">Year</label>
                <select id="selYear" value={year} onChange={(e) => setYear(e.target.value)}>
                  {units?.years.map((y) => <option key={y}>{y}</option>)}
                </select>
              </div>
              <div className="fld">
                <label htmlFor="selSeason">Season</label>
                <select id="selSeason" value={season} onChange={(e) => setSeason(e.target.value)}>
                  {seasons.map((s) => <option key={s}>{s}</option>)}
                </select>
              </div>
            </div>
            <p className="hint">NPP and AETI are retrieved from FAO WaPOR v3 for the
              selected season and summed across its dekads
              {method?.resolution_m ? ` (${method.resolution_m} m)` : ''}.</p>
          </div>

          <div className="sec">
            <button className="runbtn" onClick={run} disabled={!canRun}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M8 5v14l11-7z" />
              </svg>
              {busy ? 'Running…' : 'Run analysis'}
            </button>
            <p className="hint" style={{ textAlign: 'center' }}>
              Retrieves seasonal WaPOR NPP and AETI, then estimates wheat biomass,
              yield and water productivity.
            </p>
            {runError && <p className="err" role="alert">{runError}</p>}
          </div>

          <div className="foot">
            Wheat Water Productivity Tool (WWPT) · developed by IWMI East Africa with EIAR under
            WaPOR Phase II, supported by FAO and the Government of the Netherlands.
          </div>
        </aside>

        {/* ───────── Map ───────── */}
        <main className="mapwrap">
          <MapView
            aoiOutline={aoiOutline}
            raster={result ? { url: result.raster_png, bounds: result.bounds } : null}
            fitTo={fitBounds.current} fitKey={fitKey}
            drawing={drawing} drawPoints={drawPoints}
            onDrawPoint={(p) => setDrawPoints((pts) => [...pts, p])}
            onDrawFinish={finishDraw}
            onInspect={inspect} inspectEnabled={!!result && !drawing}
            marker={marker} popup={popup} onExplain={loadExplanation}
          />
          {drawing && (
            <div className="maptip">
              {drawPoints.length < 3
                ? `Click to add vertices — ${3 - drawPoints.length} more needed`
                : 'Double-click to finish · Esc to cancel'}
            </div>
          )}
          {(result || schemeResult) && !resultsOpen && (
            /* Closing the panel must not strand a completed run. */
            <button className="reopen" onClick={() => setResultsOpen(true)}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <path d="M3 3v18h18" /><path d="M7 15l4-5 3 3 5-7" />
              </svg>
              Show results
            </button>
          )}
          {result && (
            <div className="legend">
              <h4>Wheat water productivity</h4>
              <div className="ramp" />
              <div className="ramplbl"><span>0.4</span><span>0.8</span><span>1.2</span><span>1.6+</span></div>
              <div className="ramplbl" style={{ justifyContent: 'center', marginTop: 2 }}>
                <span>kg grain per m³ of water{result.resolution_m ? ` · ${result.resolution_m} m` : ''}</span>
              </div>
            </div>
          )}
          {busy && (
            <div className="spin" role="status" aria-live="polite">
              <div className="card">
                <div className="bars" aria-hidden="true"><i /><i /><i /></div>
                <p>{STEPS[step][0]}</p>
                <small>{STEPS[step][1]}</small>
              </div>
            </div>
          )}
        </main>

        {/* ───────── Results ─────────
             Mounted only once an analysis has produced results, so the panel
             never occupies the layout while it has nothing to show. */}
        {schemeResult && resultsOpen && (
          <SchemeResults data={schemeResult} onClose={() => setResultsOpen(false)} />
        )}

        {result && resultsOpen && (
          <aside className="results" aria-label="Analysis results">
              <div className="rhead">
                <div>
                  <h2>Analysis results</h2>
                  <div className="sub">
                    {result.label} · {result.season} {result.year} · {result.system}
                  </div>
                  <div className="sub">
                    Season {result.season_window.sos} to {result.season_window.eos} ·
                    LGP {result.season_window.lgp_days} days
                  </div>
                </div>
                <button className="close" onClick={() => setResultsOpen(false)}
                  title="Close results" aria-label="Close results">✕</button>
              </div>

              {result.synthetic && (
                /* The values below are indistinguishable from real output once
                   they leave the screen, so the data source has to be stated
                   where the numbers are, not only in the documentation. */
                <p className="banner" role="note">
                  <b>Demonstration data.</b> These values come from the built-in
                  synthetic provider ({result.provider}), not FAO WaPOR v3. The method
                  is the real one; the inputs are not. Set <code>WWP_PROVIDER=wapor</code>
                  on the service for actual retrieval.
                </p>
              )}

              <div className="kpis">
                <div className="kpi hero">
                  <div className="l">Mean wheat water productivity</div>
                  <div className="v">{result.stats.mean.toFixed(2)} <span className="u">kg/m³</span></div>
                  <div className="u">
                    P10–P90: {result.stats.p10.toFixed(2)} – {result.stats.p90.toFixed(2)} kg/m³
                  </div>
                </div>
                <div className="kpi">
                  <div className="l">Estimated yield</div>
                  <div className="v">{result.yield_t_ha.toFixed(2)}</div>
                  <div className="u">t/ha</div>
                </div>
                <div className="kpi">
                  <div className="l">Seasonal AETI</div>
                  <div className="v">{result.et_mm}</div><div className="u">mm</div>
                </div>
                <div className="kpi">
                  <div className="l">Seasonal NPP</div>
                  <div className="v">{result.npp_mean.toLocaleString()}</div>
                  <div className="u">gC/m²</div>
                </div>
                <div className="kpi">
                  <div className="l">Wheat area analysed</div>
                  <div className="v">{result.area_ha.toLocaleString()}</div><div className="u">ha</div>
                </div>
                <div className="kpi">
                  <div className="l">Productivity gap</div>
                  <div className="v">{result.gap_pct}%</div>
                  <div className="u">vs attainable (P95 basin)</div>
                </div>
              </div>

              <div className="chartsec">
                <h3>Seasonal trend</h3>
                <div className="note">Mean WWP across the last five seasons, same extent</div>
                <TrendChart data={result.trend} currentYear={result.year} />
              </div>

              <div className="chartsec">
                <h3>Distribution</h3>
                <div className="note">Share of wheat area by WWP class</div>
                <HistChart data={result.histogram} />
              </div>

              <div className="chartsec">
                <h3>How this estimate is built</h3>
                <div className="note">
                  Area mean through each step of the method. Water productivity is the mean
                  of the per-pixel ratios, which differs slightly from dividing the means.
                </div>
                <ChainChart steps={result.chain} />
              </div>

              {explain && (
                <div className="chartsec" id="derivation" ref={explainRef}>
                  <h3>Derivation <span className="badge">Selected pixel</span></h3>
                  <div className="note">
                    Pixel at {explain.lat.toFixed(4)}°N, {explain.lon.toFixed(4)}°E — every
                    input, parameter and intermediate behind its {explain.wwp.toFixed(2)} kg/m³
                  </div>
                  <ChainChart steps={explain.chain} />
                </div>
              )}

              {!explain && (
                <div className="chartsec">
                  <p className="hint" style={{ marginTop: 0 }}>
                    Click any pixel on the map, then choose <b>Show the derivation</b> to see
                    the inputs and parameters behind that value.
                  </p>
                </div>
              )}

              <div className="exportrow">
                <button onClick={exportCsv}>Export values (CSV)</button>
                <button onClick={() => setPage('cite')}>How to cite</button>
              </div>
          </aside>
        )}
      </div>

      <footer className="ftr">
        <span>© 2026 Ethiopian Institute of Agricultural Research</span><span className="dot" />
        <span>Data: FAO WaPOR v3</span><span className="dot" />
        <button onClick={() => setPage('cite')}>How to cite</button><span className="dot" />
        <button onClick={() => setPage('disclaimer')}>Disclaimer</button>
        {method && <><span className="dot" /><span>{method.synthetic ? 'Demonstration data' : method.provider}</span></>}
      </footer>

      {page && (
        <div className="modal" onClick={(e) => { if (e.target.classList.contains('modal')) setPage(null) }}
          role="dialog" aria-modal="true" aria-label={PAGES[page].title}>
          <div className="box">
            <div className="mh">
              <h2>{PAGES[page].title}</h2>
              <button onClick={() => setPage(null)} aria-label="Close">✕</button>
            </div>
            <div className="mb">{PAGES[page].body}</div>
          </div>
        </div>
      )}

      {toast && (
        <div className={`toast${toast.bad ? ' bad' : ''}`} role="status" aria-live="polite">
          {toast.message}
        </div>
      )}
    </>
  )
}
