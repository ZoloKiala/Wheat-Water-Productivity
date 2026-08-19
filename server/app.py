"""WWP dashboard server.

Serves ``wheat_dashboard.html`` and gives its **Run analysis** button something
real to call. Two providers:

``wapor``
    The genuine article — ``etwapor`` retrieves WaPOR v3 L2 NPP and AETI for each
    feature over its own SOS→EOS window and computes yield and water
    productivity. Needs the geo stack (geopandas, rioxarray, rasterio); see
    requirements-wapor.txt.

``synthetic``
    A seeded generator standing in for retrieval, so the dashboard is fully
    usable without the geo stack or network access. It runs the *real* equations
    on *invented* NPP and AETI. Every response carries ``synthetic: true`` and
    the service says so at boot, because these values are indistinguishable from
    real output once they leave the process.

Run:
    pip install -r server/requirements.txt
    python -m uvicorn server.app:app --reload --port 8000
Then open http://127.0.0.1:8000/
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "wheat_dashboard.html"
DATA = ROOT / "Data"

# FAO (2020b) reference values for wheat, as used by the reference notebook.
CROP = {"AOT": 0.85, "fc": 0.90, "mc": 0.15, "hi": 0.48}

# Place search defaults to Ethiopia; pass country= to widen it.
DEFAULT_GEOCODER_COUNTRY = os.environ.get("WWP_GEOCODER_COUNTRY", "et")

# Area limits, enforced here as well as in the page so the API cannot be talked
# into a feature the data cannot support. WaPOR v3 L2 is a 100 m product: one
# pixel is one hectare, so below a hectare an "average" is a single pixel. The
# upper bound is the documented single-extent limit of about 3 degrees square.
MIN_AREA_HA = 1.0
WARN_AREA_HA = 4.0
MAX_SPAN_DEG = 3.0
MAX_AREA_HA = 1_000_000.0

# ── retrieval ─────────────────────────────────────────────────────────────────
# FAO WaPOR v3 is the only source of numbers. There is no synthetic fallback: if
# the raster stack is missing the service says so and estimation returns 503,
# rather than substituting values that look like measurements.
_WAPOR_ERR: Optional[str] = None

from . import cache, geocode
from . import wapor as wapor_mod

# Reproduce the reference notebook's decade URLs (D1 for every decade) instead of
# the correct D1/D2/D3. Off by default: it is a bug, kept available so a run can
# be compared against published notebook figures.
MIRROR_ETWAPOR = os.environ.get("WWP_MIRROR_ETWAPOR", "").lower() in {"1", "true", "yes"}

RASTER_OK, _why = wapor_mod.available()
if not RASTER_OK:
    _WAPOR_ERR = f"raster stack unavailable - {_why}"
PROVIDER = "wapor"


# ── request/response models ───────────────────────────────────────────────────
class Feature(BaseModel):
    """One area of interest to estimate, with its own growing season."""

    id: int = Field(ge=1, description="Unique positive integer identifying the feature")
    name: str = Field(min_length=1)
    kind: Literal["point", "polygon"]
    sos: str
    eos: str
    location: Optional[int] = Field(default=None, ge=1)
    # point: [lon, lat]; polygon: [[lon, lat], ...] in EPSG:4326
    lon: Optional[float] = None
    lat: Optional[float] = None
    ring: Optional[list[list[float]]] = None

    @field_validator("sos", "eos")
    @classmethod
    def _date(cls, v: str) -> str:
        try:
            dt.date.fromisoformat(v)
        except ValueError as exc:
            raise ValueError(f"not an ISO date: {v!r}") from exc
        return v


class EstimateRequest(BaseModel):
    features: list[Feature] = Field(min_length=1, max_length=200)
    scheme_code: Optional[str] = None


class Estimate(BaseModel):
    id: int
    name: str
    warning: Optional[str] = None
    provider: str = "wapor"
    cached: bool = False
    NPP: float
    EYield_tpha: float
    AETI_mm: float
    WP_kgpm3: float
    LGP: int
    area_ha: Optional[float] = None


# ── shared geometry helpers ───────────────────────────────────────────────────
def lgp_of(sos: str, eos: str) -> int:
    return (dt.date.fromisoformat(eos) - dt.date.fromisoformat(sos)).days


def area_ha(ring: list[list[float]]) -> float:
    """Planar shoelace on lon/lat with a cos(lat) correction, in hectares.

    Matches the client's own areaHa() so a feature reports one area whichever
    side computed it. Good to a fraction of a percent at field scale; not a
    geodesic area.
    """
    a = 0.0
    n = len(ring)
    for i in range(n):
        j = (i - 1) % n
        a += (ring[j][0] + ring[i][0]) * (ring[j][1] - ring[i][1])
    k = 110.57  # km per degree of latitude
    # Mean latitude, not the first vertex's: taking vertex 0 makes the answer
    # depend on where the ring happens to start, so reversing it changes the area.
    lat = sum(p[1] for p in ring) / n
    return abs(a / 2) * k * k * math.cos(math.radians(lat)) * 100


def centroid(f: Feature) -> tuple[float, float]:
    if f.kind == "point":
        return float(f.lon), float(f.lat)
    ring = f.ring or []
    n = len(ring) or 1
    return sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n


def validate_feature(f: Feature) -> None:
    """The notebook's own input rules (etwapor.util.validate_input)."""
    if lgp_of(f.sos, f.eos) <= 0:
        raise HTTPException(422, f"feature {f.id}: EOS must occur after SOS")
    if f.kind == "point":
        if f.lon is None or f.lat is None:
            raise HTTPException(422, f"feature {f.id}: a point needs lon and lat")
        if f.location is None:
            raise HTTPException(422, f"feature {f.id}: points require Location (plot number)")
    else:
        if not f.ring or len(f.ring) < 3:
            raise HTTPException(422, f"feature {f.id}: a polygon needs at least 3 vertices")
        area = area_ha(f.ring)
        if area <= 0:
            raise HTTPException(422, f"feature {f.id}: polygon has zero area")
        lons = [p[0] for p in f.ring]
        lats = [p[1] for p in f.ring]
        d_lon, d_lat = max(lons) - min(lons), max(lats) - min(lats)
        if d_lon > MAX_SPAN_DEG or d_lat > MAX_SPAN_DEG:
            raise HTTPException(
                422,
                f"feature {f.id}: extent spans {d_lon:.1f} x {d_lat:.1f} degrees, over the "
                f"{MAX_SPAN_DEG:g} x {MAX_SPAN_DEG:g} limit for one analysis",
            )
        if area > MAX_AREA_HA:
            raise HTTPException(
                422, f"feature {f.id}: {area:,.0f} ha exceeds the {MAX_AREA_HA:,.0f} ha limit"
            )
        if area < MIN_AREA_HA:
            raise HTTPException(
                422,
                f"feature {f.id}: {area:.2f} ha is smaller than one WaPOR pixel "
                f"(1 ha at 100 m) - use a point instead",
            )


def area_warning(f: Feature) -> Optional[str]:
    """Usable, but the caller should know the estimate rests on very few pixels."""
    if f.kind != "polygon" or not f.ring:
        return None
    area = area_ha(f.ring)
    if area < WARN_AREA_HA:
        px = max(1, round(area))
        return (f"{area:.2f} ha is about {px} WaPOR pixel{'' if px == 1 else 's'}; "
                "the average is dominated by field edges")
    return None


# ── the estimation chain (identical in both providers) ─────────────────────────
def yield_from_npp(npp: float) -> float:
    """Seasonal NPP (gC/m²) → grain yield (t/ha), per the reference notebook."""
    total_biomass = CROP["AOT"] * CROP["fc"] * npp * 22.222 / (1 - CROP["mc"])
    return total_biomass * CROP["hi"] / 1000.0


def water_productivity(yield_tpha: float, aeti_mm: float) -> float:
    """kg of grain per m³ consumed. SWC = AETI × 10 m³/ha."""
    return yield_tpha * 1000.0 / (aeti_mm * 10.0)


# ── wapor provider ────────────────────────────────────────────────────────────
def geojson_geometry(f: Feature) -> dict:
    """The feature as a GeoJSON geometry mapping, which rio.clip accepts."""
    if f.kind == "point":
        # A point clip would take a single pixel and often miss on the raster
        # grid, so use the pixel-sized box around it (WaPOR L2 is 100 m ~ 0.0009 deg).
        d = 0.00045
        lon, lat = float(f.lon), float(f.lat)
        return {
            "type": "Polygon",
            "coordinates": [[
                [lon - d, lat - d], [lon + d, lat - d],
                [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d],
            ]],
        }
    ring = list(f.ring or [])
    if ring and ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    return {"type": "Polygon", "coordinates": [ring]}


def estimate_wapor(f: Feature, scheme_code: Optional[str]) -> Estimate:  # pragma: no cover
    """Real retrieval: seasonal NPP and AETI for one feature over its own season."""
    geom = geojson_geometry(f)
    npp_urls = wapor_mod.decade_urls(f.sos, f.eos, "L2-NPP-D", scheme_code,
                                     mirror_etwapor=MIRROR_ETWAPOR)
    aeti_urls = wapor_mod.decade_urls(f.sos, f.eos, "L2-AETI-D", scheme_code,
                                      mirror_etwapor=MIRROR_ETWAPOR)
    if not npp_urls:
        raise HTTPException(422, f"feature {f.id}: the season covers no WaPOR decade")

    npp = wapor_mod.seasonal_sum(npp_urls, geom, f.sos, f.eos)
    aeti = wapor_mod.seasonal_sum(aeti_urls, geom, f.sos, f.eos)
    y = yield_from_npp(npp)
    return Estimate(
        id=f.id,
        name=f.name,
        NPP=round(npp, 1),
        EYield_tpha=round(y, 2),
        AETI_mm=round(aeti, 1),
        WP_kgpm3=round(water_productivity(y, aeti), 2),
        LGP=lgp_of(f.sos, f.eos),
        area_ha=round(area_ha(f.ring), 2) if f.kind == "polygon" and f.ring else None,
    )


# ── survey layers, read straight from the input shapefiles ────────────────────
def _survey() -> dict[str, Any]:
    """The 2026 fields and monitoring points, with the notebook's own results."""
    import shapefile  # pyshp

    results = json.loads((Path(__file__).parent / "notebook_results.json").read_text("utf-8"))
    by_field = {int(k): v for k, v in results["fields"].items()}
    by_group = {k: v for k, v in results["point_groups"].items()}

    fields = []
    r = shapefile.Reader(str(DATA / "Irrigated_Wheat_2026.shp"))
    flds = [f[0] for f in r.fields[1:]]
    for sr in r.iterShapeRecords():
        rec = dict(zip(flds, sr.record))
        fid = int(rec["ID"])
        ring = [[round(c[0], 6), round(c[1], 6)] for c in sr.shape.points]
        fields.append(
            {
                "ID": fid,
                "Name": rec["Name"],
                "Scheme_ID": rec["Scheme_ID"],
                "Area_ha": round(float(rec["Area_ha"]), 4),
                "SOS": str(rec["SOS"]),
                "EOS": str(rec["EOS"]),
                "ring": ring,
                **by_field[fid],
            }
        )

    points = []
    rp = shapefile.Reader(str(DATA / "Irrigated_Wheat_2026_PNT.shp"))
    fp = [f[0] for f in rp.fields[1:]]
    for sr in rp.iterShapeRecords():
        rec = dict(zip(fp, sr.record))
        key = f"{rec['Name']}|{int(rec['Location'])}"
        g = by_group[key]
        points.append(
            {
                "ID": int(rec["ID"]),
                "Name": rec["Name"],
                "Location": int(rec["Location"]),
                "Scheme_ID": rec["Scheme_ID"],
                "lon": round(sr.shape.points[0][0], 6),
                "lat": round(sr.shape.points[0][1], 6),
                # per-point values are not published by the notebook; the group
                # median is what stands behind each point
                "group_median": g,
            }
        )
    return {"fields": fields, "points": points, "point_groups": by_group}


# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="WWP dashboard server",
    description=(
        "Serves the Wheat Water Productivity dashboard and estimates wheat yield "
        "and crop water productivity for areas of interest, using the reference "
        "notebook's method."
    ),
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get(
        "WWP_CORS_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500"
    ).split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _announce() -> None:
    if RASTER_OK:
        mode = " (etwapor-mirror decades)" if MIRROR_ETWAPOR else ""
        print(f"[wwp] provider: FAO WaPOR v3 - real retrieval{mode}", flush=True)
    else:
        print(
            f"[wwp] WaPOR retrieval UNAVAILABLE - {_WAPOR_ERR}. "
            "Estimation will return 503. Fix with: "
            "pip install -r server/requirements-wapor.txt",
            flush=True,
        )


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "provider": PROVIDER,
        "wapor_error": _WAPOR_ERR,
        "raster_stack": RASTER_OK,
        "dashboard": DASHBOARD.exists(),
        "mirror_etwapor": MIRROR_ETWAPOR,
    }


@app.get("/api/method")
def method() -> dict[str, Any]:
    return {
        "crop_params": CROP,
        "equations": [
            "TB = AOT * fc * NPP * 22.222 / (1 - mc)   # total biomass, kg dry matter/ha",
            "Y  = TB * hi                              # grain yield, kg/ha",
            "CWP = Y / SWC,  SWC = AETI * 10            # water productivity, kg/m3",
        ],
        "mapsets": {"npp": "L2-NPP-D", "aeti": "L2-AETI-D"},
        "reference": "ETH_WWP_WaPORv3_Irrigaed_Wheat_2026_Final",
        "provider": PROVIDER,
        "raster_stack": RASTER_OK,
        "mirror_etwapor": MIRROR_ETWAPOR,
        "decade_note": (
            "etwapor requests the D1 raster for all three decades of a month; this "
            "server requests D1/D2/D3 unless WWP_MIRROR_ETWAPOR is set."
        ),
    }


@app.get("/api/wapor/check")
def wapor_check(
    sos: str,
    eos: str,
    mapset: str = "L2-NPP-D",
    scheme_code: Optional[str] = None,
    mirror_etwapor: bool = False,
) -> dict[str, Any]:
    """Does the season's WaPOR data exist? HEADs each decadal raster.

    Cheap compared with a download, and it distinguishes 'no data published for
    this season' from 'retrieval failed'.
    """
    try:
        urls = wapor_mod.decade_urls(sos, eos, mapset, scheme_code,
                                     mirror_etwapor=mirror_etwapor)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    rows = wapor_mod.check_urls(urls)
    distinct = {r.get("bytes") for r in rows if r.get("bytes")}
    return {
        "mapset": mapset,
        "decades": len(rows),
        "present": sum(1 for r in rows if r.get("status") == 200),
        "distinct_files": len(distinct),
        "raster_stack": wapor_mod.available()[0],
        "results": rows,
    }


@app.get("/api/geocode")
def api_geocode(
    q: str = Query(min_length=1, max_length=200, description="Place name or 'lat, lon'"),
    limit: int = Query(6, ge=1, le=20),
    country: Optional[str] = Query(DEFAULT_GEOCODER_COUNTRY,
                                   description="ISO 3166-1 alpha-2 filter; empty for worldwide"),
) -> dict[str, Any]:
    """Find a place by name so the map can be moved to it (ToR WP2b).

    A coordinate pair is resolved without leaving the process. Anything else goes
    to Nominatim, throttled and cached here.
    """
    try:
        return geocode.search(q, limit=limit, country=(country or None))
    except Exception as exc:
        raise HTTPException(502, f"place search failed: {type(exc).__name__}: {exc}") from exc


@app.get("/api/cache")
def api_cache() -> dict[str, Any]:
    """What the result and idempotency caches are holding."""
    return cache.stats()


@app.delete("/api/cache")
def api_cache_clear() -> dict[str, Any]:
    removed = cache.clear()
    return {"removed": removed, **cache.stats()}


@app.get("/api/survey")
def survey() -> dict[str, Any]:
    """The 2026 irrigated-wheat layers with the notebook's published results."""
    try:
        return _survey()
    except FileNotFoundError as exc:
        raise HTTPException(500, f"survey layer missing: {exc}") from exc


@app.post("/api/estimate", response_model=list[Estimate])
def estimate(
    req: EstimateRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> list[Estimate]:
    """Estimate each feature over its own SOS→EOS window.

    Retrieval is slow, so two things short-circuit it: an `Idempotency-Key`
    header replays the whole response for a retried request, and each feature is
    cached on its geometry and season, so the same field over the same season is
    answered from disk.
    """
    ids = [f.id for f in req.features]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "feature IDs must be unique")

    if idempotency_key:
        replay = cache.get_idempotent(idempotency_key)
        if replay is not None:
            return [Estimate(**e) for e in replay]

    out: list[Estimate] = []
    for f in req.features:
        validate_feature(f)
        ck = cache.result_key(
            f.kind, geojson_geometry(f)["coordinates"], f.sos, f.eos,
            req.scheme_code, MIRROR_ETWAPOR,
        )
        hit = cache.get_result(ck)
        if hit is not None:
            # the geometry and season decide the numbers; the label does not
            out.append(Estimate(**dict(hit, id=f.id, name=f.name, cached=True,
                                       warning=area_warning(f))))
            continue
        if not RASTER_OK:
            raise HTTPException(
                503,
                "WaPOR retrieval is unavailable: "
                f"{_WAPOR_ERR}. Install it with: "
                "pip install -r server/requirements-wapor.txt",
            )
        try:
            est = estimate_wapor(f, req.scheme_code)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, f"feature {f.id}: WaPOR retrieval failed - {exc}") from exc
        est.warning = area_warning(f)
        cache.put_result(ck, est.model_dump())
        out.append(est)

    if idempotency_key:
        cache.put_idempotent(idempotency_key, [e.model_dump() for e in out])
    return out


@app.get("/")
def dashboard() -> FileResponse:
    if not DASHBOARD.exists():
        raise HTTPException(404, "wheat_dashboard.html not found next to server/")
    return FileResponse(DASHBOARD, media_type="text/html")


# The 2026 survey layers, so the dashboard can offer them as ready-made input
# instead of asking for a file it already ships with. Read-only and scoped to
# Data/ — nothing else in the project is exposed.
if DATA.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/Data", StaticFiles(directory=str(DATA)), name="data")
