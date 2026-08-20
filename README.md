# Wheat Water Productivity dashboard

Estimates irrigated-wheat yield and crop water productivity from FAO WaPOR v3,
following the IWMI reference notebook
`ETH_WWP_WaPORv3_Irrigaed_Wheat_2026_Final.ipynb`. A single-file dashboard in the
EIAR visual identity, served by a FastAPI backend that does the retrieval.

```bash
pip install -r server/requirements-wapor.txt
python server/run.py
```

Open the URL it prints — <http://localhost:8000/>. `python server/run.py --check`
diagnoses the environment without starting anything.

## What it does

Draw a point or polygon, or upload a scheme file, then **Run analysis**. Each
feature is estimated over its own SOS→EOS window: seasonal NPP and AETI are read
from the WaPOR decadal rasters, clipped to the feature, summed through time and
averaged, then converted to biomass, yield and water productivity.

```
TB  = AOT · fc · NPP · 22.222 / (1 − mc)    # total biomass, kg dry matter/ha
Y   = TB · hi                                # grain yield, kg/ha
CWP = Y / SWC,  SWC = AETI · 10              # water productivity, kg/m³
```

FAO (2020b) wheat parameters: AOT 0.85, fc 0.90, mc 0.15, hi 0.48.

**FAO WaPOR v3 is the only source of numbers.** There is no synthetic provider: if
the raster stack is missing the service says so at boot, reports
`raster_stack: false`, and estimation returns 503 rather than substituting values
that would look like measurements.

Retrieval is slow by nature — 16 decadal COGs per feature out of ~6 GB rasters,
about **2 to 3 minutes**. Results are cached, so a repeat is immediate.

## Layout

| path | what |
|---|---|
| `wheat_dashboard.html` | the whole dashboard: layout, charts, map, upload, validation |
| `server/app.py` | FastAPI app and the API |
| `server/wapor.py` | WaPOR retrieval and seasonal aggregation — the analysis logic |
| `server/cache.py` | result cache and idempotency keys |
| `server/geocode.py` | place search, proxied and throttled |
| `404.html`, `500.html` | branded, self-contained error pages |
| `server/run.py` | launcher: dual-stack bind, port handling, `--check` |
| `server/selftest.py` | offline checks for the analysis logic |
| `Data/` | the 2026 irrigated-wheat survey layers |
| `etwapor/` | the reference implementation this server mirrors |

See [server/README.md](server/README.md) for the API, the provider detail, the
area limits, and the one deliberate divergence from `etwapor`.

## Terms of reference

Against `ToR_Dashboard_Developer.docx`:

| ToR | state |
|---|---|
| WP2 — interactive map (Leaflet), EIAR visual identity | done; tokens taken from the approved dashboard |
| WP2 — inputs by shapefile upload **and** other sources | done: `.shp`+`.dbf`, all sidecars, zipped shapefile, GeoJSON, draw, place search |
| WP2 — client **and** server-side validation with informative errors | done: attribute rules and area limits enforced on both sides |
| WP3 — package the Python tool behind a stable API | done: `/api/estimate`, `/api/survey`, `/api/method`, `/api/wapor/check` |
| WP3 — caching for repeated queries, idempotency keys | done: `server/cache.py`, `Idempotency-Key` header — 197 s cold, 0 s warm |
| WP2 — responsive on small screens | done: map overlays reflow at 760px and 560px; verified 380–1440px |
| WP5 — branded error pages | done: `404.html`, `500.html`, HTML for browsers and JSON for API clients |
| WP3 — WaPOR data access layer, on-the-fly NPP retrieval | done: `server/wapor.py` |
| WP5 — supporting pages: purpose, methodology, data sources, guidance, citation, disclaimer | in the dashboard's Method & data and Help dialogs |
| WP2 — charting library (Plotly, ECharts or D3) | **not met**: charts are hand-authored SVG/CSS, no library |
| WP4 — LightGBM inference endpoint, model update workflow, explanations | **not started** |
| WP5 — EIAR website integration, GA4 analytics | **not started** |
| WP1/WP6 — architecture report, wireframes, training, handover docs | **not started** |

## Deploy

Railway, from `Dockerfile` + `railway.json`; health check on `/api/health`. The
image installs `requirements-wapor.txt`, so retrieval works out of the box, and
`server/selftest.py` runs at build time — a change that breaks the estimation
chain fails the build rather than shipping.

| variable | default | purpose |
|---|---|---|
| `PORT` | 8000 | injected by the platform |
| `WWP_CACHE_DIR` | `server/.cache` | point at a volume to keep the cache across deploys |
| `WWP_CACHE_TTL` | 2592000 | cache lifetime, seconds |
| `WWP_MIRROR_ETWAPOR` | unset | reproduce the notebook's decade URLs for comparison |
| `WWP_GEOCODER_COUNTRY` | `et` | place-search country filter; empty for worldwide |
| `WWP_CORS_ORIGINS` | localhost:5500 | extra origins allowed to call the API |

## Data caveats

Two things qualify every figure from the 2026 survey layers:

- **Dubti field 4** contains no monitoring points, so nothing checks its estimate.
  Its seasonal AETI of 194 mm is a third below its two neighbours on the same
  season window, consistent with part of the mapped area not having been cropped.
- **Dodota** mixes seasons: its boundary record runs Dec 2024 → Apr 2025 while one
  of its two point groups was collected a year later.

WaPOR v3 L2 is a 100 m product, so one pixel is one hectare — Godino covers about
4 pixels, Dodota 7 and Amibara 13. Field outlines are finer than the data can
resolve, and a whole-field average at that ratio is dominated by edge pixels.
