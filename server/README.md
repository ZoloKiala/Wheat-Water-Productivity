# WWP dashboard server

Serves `wheat_dashboard.html` and gives its **Run analysis** button something real
to call. Without it the dashboard still works — it estimates in the page and says
so in the footer.

## Run it

```bash
pip install -r server/requirements.txt
python server/run.py
```

Then open the URL it prints — <http://localhost:8000/> or <http://127.0.0.1:8000/>,
both work. The footer shows which provider answered.

`python server/run.py --check` diagnoses without starting anything: dependencies,
what `localhost` resolves to, whether the port is free.

Options: `--port 8080`, `--host 0.0.0.0` (bind one host), `--reload` (restart on
edit; IPv4 only, so use `127.0.0.1` with it).

### "Connection refused"

Use `python server/run.py` rather than uvicorn directly. Plain
`uvicorn server.app:app --port 8000` binds IPv4 `127.0.0.1` only, and on Windows
`localhost` resolves to IPv6 `::1` **first** — so `http://localhost:8000` is
refused while the server is running perfectly well on `127.0.0.1`. The launcher
binds a dual-stack socket so both names work.

Other causes, in the order worth checking:

- **Wrong directory.** Run from the project root (the folder holding
  `wheat_dashboard.html`), not from inside `server/`.
- **Port already taken.** The launcher moves to the next free port and says so —
  read the printed URL rather than assuming 8000.
- **Server never started.** A missing dependency exits with the pip command to
  run; `--check` lists them.
- **Page served elsewhere.** Opening the file from `file://` or Live Server means
  its API calls go nowhere. Either open the page from this server, or pass
  `?api=http://localhost:8000`.

Already serving the file another way (Live Server on 5500, say)? Point the page at
the API instead of moving it:

```
http://127.0.0.1:5500/wheat_dashboard.html?api=http://127.0.0.1:8000
```

Port 5500 is in the default CORS allowlist; add others with
`WWP_CORS_ORIGINS=http://host:port,...`.

## Provider

FAO WaPOR v3 is the only source of numbers — there is no synthetic fallback. If the
raster stack is missing the service starts, says so at boot, reports
`raster_stack: false` from `/api/health`, and `POST /api/estimate` returns **503**
rather than substituting values that would look like measurements.

```bash
pip install -r server/requirements-wapor.txt
python server/run.py
```

**Expect it to be slow.** Each feature reads 16 decadal COGs (8 decades x NPP and
AETI) by HTTP range request out of ~6 GB rasters — about **2 to 3 minutes per
feature**. Six features is therefore a quarter of an hour in one request, long
enough for a browser or proxy to give up. Estimate a few at a time, and use
`/api/wapor/check` first to confirm the season has data before committing to the
wait.

Verified end to end against the notebook's own Amibara figures:

| | NPP | Yield t/ha | AETI mm | WP kg/m3 |
|---|---|---|---|---|
| Notebook (published) | 184.95 | 1.78 | 297.32 | 0.60 |
| This server, `WWP_MIRROR_ETWAPOR=1` | 186.94 | 1.79 | 297.22 | 0.60 |
| This server, correct D1/D2/D3 | 189.44 | 1.82 | 322.76 | 0.56 |

Mirror mode reproduces the published numbers, which confirms the chain — URL
construction, `scale_factor`, day-weighting, clip, sum-then-mean — and shows the
notebook's figures carry the decade defect described below.

## Area limits

Enforced in the page and again in the API, so a feature the data cannot support is
refused rather than quietly averaged:

| rule | why |
|---|---|
| reject under **1 ha** | WaPOR v3 L2 is 100 m, so one pixel is one hectare; below that an average is a single pixel |
| warn under **4 ha** | only a few pixels, and the average is dominated by field edges |
| reject over **3 deg x 3 deg** | the documented limit for one analysis extent |
| reject zero area | degenerate ring |

Rejections come back as `422` naming the feature; warnings ride along on the
estimate as a `warning` field and surface in the panel.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | The dashboard |
| `GET` | `/api/health` | `provider`, `synthetic`, and any `wapor_error` |
| `GET` | `/api/method` | Crop parameters, equations, WaPOR mapsets |
| `GET` | `/api/survey` | The 2026 fields and 57 monitoring points with the notebook's published results |
| `POST` | `/api/estimate` | Estimate each area of interest over its own SOS→EOS window |

`POST /api/estimate`:

```json
{"features": [
  {"id": 1, "name": "Block 3", "kind": "polygon", "sos": "2025-11-20", "eos": "2026-03-15",
   "ring": [[40.170, 9.340], [40.180, 9.340], [40.180, 9.350]]},
  {"id": 2, "name": "Sample", "kind": "point", "sos": "2025-11-20", "eos": "2026-03-15",
   "location": 1, "lon": 39.02, "lat": 8.85}
]}
```

Rings are `[lon, lat]` in EPSG:4326. Each feature comes back with `NPP`,
`EYield_tpha`, `AETI_mm`, `WP_kgpm3`, `LGP`, `area_ha`, plus `provider` and
`synthetic` — the notebook's own column names, so a response and a notebook run
line up directly.

Validation mirrors `etwapor.util.validate_input` and rejects with `422`: unique
positive IDs, EOS after SOS, `Location` required on points, polygons need three
vertices and non-zero area.

## Analysis logic

`server/wapor.py` holds the retrieval, mirroring
`etwapor.download.WaPORDownload` and `productivity.process_single_feature`:

1. The season is split into WaPOR decades — `D1` days 1–10, `D2` 11–20, `D3`
   21–end — keeping every decade whose start falls inside SOS→EOS.
2. Each decade maps to a COG URL: `MAPSET/<mapset>/WAPOR-3.<mapset>.YYYY-MM-Dn.tif`,
   or `MOSAICSET/...<scheme>...` when a scheme code is given.
3. Each raster is clipped to the feature, summed through time per pixel, then
   averaged over the feature. That order is the notebook's and it matters: mean-then-sum
   differs from sum-then-mean whenever the clip contains NaNs.
4. `min_count=6` is kept from `compute_seasonal_biomass` — a season with fewer
   than six valid decades raises instead of quietly returning NaN.
5. NPP → biomass → yield → water productivity, using the equations below.

Points are clipped as a pixel-sized box (WaPOR L2 is 100 m ≈ 0.0009°) rather than
a bare point, which frequently misses on the raster grid.

`python server/selftest.py` checks all of this offline — the decade split, the URL
list, the chain against the notebook's six published fields, the input rules.
`--online` additionally HEADs the real rasters.

`GET /api/wapor/check?sos=…&eos=…&mapset=L2-NPP-D` reports, per decade, whether
the data is published and how many *distinct* files the season resolves to. Cheap
next to a download, and it separates "no data for this season" from "retrieval
failed".

### One deliberate divergence from etwapor

`etwapor._get_storage_url` emits `D1` in all three decade branches — only the
date *label* differs — so a season fetches the first decade of each month
repeatedly instead of the three decades. Verified against the live store: D1, D2
and D3 all exist for 2025-11 and differ in size, and the Amibara season resolves
to **8 distinct rasters correctly but only 3 under etwapor's pattern**.

This server requests D1/D2/D3. Set `WWP_MIRROR_ETWAPOR=1` to reproduce the
notebook's behaviour when you need to compare against published figures —
`/api/health` and `/api/method` both report which mode is active. Expect real
retrieval to disagree with the notebook's numbers until this is fixed upstream.

## The method

One chain, shared by both providers, so only the *inputs* differ:

```
TB  = AOT · fc · NPP · 22.222 / (1 − mc)    # total biomass, kg dry matter/ha
Y   = TB · hi                                # grain yield, kg/ha
CWP = Y / SWC,  SWC = AETI · 10              # water productivity, kg/m³
```

FAO (2020b) wheat parameters: AOT 0.85, fc 0.90, mc 0.15, hi 0.48.

`notebook_results.json` holds the published results transcribed from
`ETH_WWP_WaPORv3_Irrigaed_Wheat_2026_Final.ipynb` — boundary results from cell 17
and point-group medians from cell 27. `/api/survey` joins them to the geometry it
reads from `Data/*.shp`. Two caveats travel with that data: **Dubti field 4** has
no monitoring points inside it, and **Dodota**'s boundary record sits a season
earlier than one of its point groups.
