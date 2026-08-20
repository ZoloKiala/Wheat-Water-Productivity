"""FAO WaPOR v3 data access: seasonal NPP and AETI for a set of coordinates.

The WWPT method needs exactly two rasters per analysis — seasonal net primary
production and seasonal actual evapotranspiration and interception, both summed
over the [SOS, EOS] growing-season window. This module retrieves them from FAO
WaPOR v3, following ``etwapor.download.WaPORDownload``, the retrieval the
reference notebook uses.

Where the data comes from
-------------------------
Dekadal rasters live at deterministic paths on FAO's Google Cloud Storage
bucket::

    https://storage.googleapis.com/fao-gismgr-wapor-3-data/DATA/WAPOR-3/
        MAPSET/{mapset}/WAPOR-3.{mapset}.{YYYY}-{MM}-D{n}.tif

The path is derived from the date, so no catalogue lookup is needed. That
matters beyond saving a request. The GISManager catalogue's *mapset listing*
returns 22 entries, all L1, which an earlier version of this module took as
proof that L1 was the only level published — so it served 300 m data while the
dashboard claimed 100 m. The listing is simply incomplete: fetching
``/mapsets/L2-NPP-D`` by code returns it in full ("Net Primary Production
(National - Dekadal - 100m)", scale 0.001), and the rasters are on the bucket.
The reference implementation defaults to ``L2-NPP-D`` and ``L2-AETI-D``, and so
does this one. ``check_access()`` checks both the raster and the catalogue
entry, so neither claim rests on the listing.

    L1   300 m, global
    L2   100 m, national (the default, as in the reference)
    L3    20 m, scheme mosaics (needs a scheme code, see below)

Set ``WWP_WAPOR_LEVEL`` to change level. L3 rasters live under ``MOSAICSET``
and are addressed by scheme code (``KOG`` for Koga, ``AWH`` for Awash); set
``WWP_WAPOR_SCHEME`` to use them.

How a season is summed
----------------------
Dekadal products hold a **mean daily rate** (NPP gC/m²/day, AETI mm/day), so a
dekad contributes ``rate × scale × days``, where ``days`` counts only the part
of the dekad inside the season. The first and last dekads of a season are
therefore partial, exactly as ``etwapor.download._compute_days`` computes them.

The scale factor is read from the GeoTIFF band, which is where the reference
reads it (``da.attrs['scale_factor']``), falling back to the catalogue for L1
mapsets that publish one. Negative values are nodata and become NaN, again as
in the reference.

A season is required to cover at least ``MIN_DEKADS`` dekads. The reference
sums with ``min_count=6`` and yields NaN below that, which would surface here as
an unexplained blank; refusing with a message naming the cause is more useful
than propagating a NaN.

One divergence from the reference, and it is deliberate
------------------------------------------------------
``etwapor.download._get_storage_url`` builds every dekad's filename as
``f'...{date_str[0:8]}D1{ext}'``. That slice keeps ``YYYY-MM-`` and the suffix is
a literal ``D1``, so all three dekads of a month resolve to the *same* raster —
the month's first dekad — while ``_compute_days`` still weights them 10, 10 and
8-11 days. The reference therefore reads dekad 1 three times per month and
attributes the whole month's days to its rate. The three files are distinct and
all published (checked against the storage bucket: ``2025-11-D1``, ``-D2`` and
``-D3`` all return 200 with different lengths), so this is a defect rather than a
naming convention.

This module builds ``D1``, ``D2`` and ``D3`` from the dekad, which is what the
season sum requires. The consequence has to be stated plainly: **running this
provider will not reproduce the NPP and AETI values the notebook printed**, and
the difference is the reference's, not this service's. Everything downstream of
those two numbers does agree exactly — that is what
``tests/test_notebook_parity.py`` replays, feeding the notebook's own NPP and
AETI through this service's arithmetic. Report the URL builder to IWMI before
the campaign figures are published; if the printed figures must be reproduced
as-is in the meantime, that is a change to ``raster_url`` below and should be
made explicitly, not by accident.

Verification status
-------------------
The path convention, the L2 rasters, the band scale and the 404 on unpublished
dekads were checked against the live service. The pixel read itself needs
``rasterio`` (GDAL), which is not in ``requirements.txt`` because it is a heavy
binary dependency and the dashboard runs without it on the synthetic provider::

    pip install rasterio
    WWP_PROVIDER=wapor uvicorn app.main:app

``check_access()`` re-runs these checks and reports whether rasterio is present;
it is exposed at ``GET /api/wapor/check``. Run it before trusting any number
this provider produces.
"""

from __future__ import annotations

import calendar
import json
import os
import threading
import urllib.error
import urllib.request
from datetime import date, timedelta

import numpy as np

WORKSPACE = os.environ.get("WWP_WAPOR_WORKSPACE", "WAPOR-3")
GISMGR = os.environ.get(
    "WWP_WAPOR_BASE", "https://data.apps.fao.org/gismgr/api/v2/catalog/workspaces"
).rstrip("/") + f"/{WORKSPACE}"

# Cloud storage root holding the dekadal GeoTIFFs, as used by etwapor.
STORAGE = os.environ.get(
    "WWP_WAPOR_STORAGE",
    "https://storage.googleapis.com/fao-gismgr-wapor-3-data/DATA",
).rstrip("/") + f"/{WORKSPACE}"

# L2 (100 m) is the reference implementation's default.
LEVEL = os.environ.get("WWP_WAPOR_LEVEL", "L2")

# Scheme code for L3 mosaics (KOG = Koga, AWH = Awash). None for MAPSET paths.
SCHEME_CODE = os.environ.get("WWP_WAPOR_SCHEME") or None

NPP_MAPSET = f"{LEVEL}-NPP-D"
AETI_MAPSET = f"{LEVEL}-AETI-D"

# Units the per-dekad accumulation assumes. Confirmed against the catalogue's
# measureUnit for L1; check_access() re-checks so a change by FAO fails loudly
# instead of silently shifting every result by a scale factor.
EXPECTED_UNITS = {f"{lvl}-NPP-D": "gC/m²/day" for lvl in ("L1", "L2", "L3")}
EXPECTED_UNITS.update({f"{lvl}-AETI-D": "mm/day" for lvl in ("L1", "L2", "L3")})

RESOLUTION_M = {"L1": 300, "L2": 100, "L3": 20}.get(LEVEL, 300)

# etwapor sums dekads with min_count=6: fewer valid dekads yields NaN.
MIN_DEKADS = 6

HTTP_TIMEOUT = 60

# WaPOR's fill value where a product has no valid retrieval. The reference
# masks every negative value, not only this one, so this module does too.
WAPOR_NODATA = -9999


class WaporError(RuntimeError):
    """Raised when WaPOR data cannot be retrieved or is not as expected."""


# -- dekad arithmetic -----------------------------------------------------
def _dekad_bounds(year: int, month: int, dekad: int) -> tuple[date, date]:
    """First and last calendar day of a WaPOR dekad (1, 2 or 3 of a month).

    Dekads 1 and 2 are fixed 10-day periods; dekad 3 runs from the 21st to the
    end of the month, so it is 8 to 11 days long. This matches the dekad
    definition the catalogue publishes with every raster.
    """
    if dekad == 1:
        return date(year, month, 1), date(year, month, 10)
    if dekad == 2:
        return date(year, month, 11), date(year, month, 20)
    return date(year, month, 21), date(year, month, calendar.monthrange(year, month)[1])


def dekads_in(sos: date, eos: date) -> list[tuple[str, int]]:
    """Dekad codes overlapping [sos, eos] with their contributing day counts.

    A dekad only partly inside the season contributes just its overlapping
    days, so the seasonal total matches the requested window exactly rather
    than the nearest whole dekads.
    """
    if eos <= sos:
        raise WaporError(f"EOS ({eos}) must fall after SOS ({sos}).")
    out: list[tuple[str, int]] = []
    cursor = date(sos.year, sos.month, 1)
    while cursor <= eos:
        for dekad in (1, 2, 3):
            start, end = _dekad_bounds(cursor.year, cursor.month, dekad)
            lo, hi = max(start, sos), min(end, eos)
            days = (hi - lo).days + 1
            if days > 0:
                out.append((f"{cursor.year}-{cursor.month:02d}-D{dekad}", days))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    if not out:
        raise WaporError(f"No WaPOR dekads fall between {sos} and {eos}.")
    return out


# -- catalogue access -----------------------------------------------------
_RASTER_CACHE: dict[str, str] = {}
_MAPSET_CACHE: dict[str, dict] = {}
_LOCK = threading.Lock()


def _get_json(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise WaporError(f"WaPOR catalogue returned HTTP {e.code} for {url}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise WaporError(f"Cannot reach the WaPOR catalogue at {url}: {e}") from e
    except json.JSONDecodeError as e:
        raise WaporError(f"WaPOR catalogue returned a non-JSON body for {url}") from e


def mapset_meta(mapset: str) -> dict:
    """Catalogue metadata for a mapset: caption, measureUnit, scale, offset.

    Only L1 mapsets are listed, so this returns an empty dict for L2 and L3
    rather than raising: the retrieval path does not depend on it, and
    ``check_access`` reports the gap instead of failing over it.
    """
    with _LOCK:
        if mapset in _MAPSET_CACHE:
            return _MAPSET_CACHE[mapset]
    try:
        meta = _get_json(f"{GISMGR}/mapsets/{mapset}").get("response", {}) or {}
    except WaporError:
        meta = {}
    with _LOCK:
        _MAPSET_CACHE[mapset] = meta
    return meta


def raster_url(mapset: str, dekad_code: str) -> str:
    """Storage URL of one dekadal raster, e.g. ('L2-AETI-D', '2024-01-D1').

    Built from the date rather than looked up, as ``etwapor`` builds it, except
    that the dekad number is the requested one: the reference hardcodes ``D1``
    for all three dekads of a month. See "One divergence from the reference" at
    the top of this module.

    L3 products are published per irrigation scheme under ``MOSAICSET`` and carry
    the scheme code in the filename.
    """
    if SCHEME_CODE:
        return (f"{STORAGE}/MOSAICSET/{mapset}/"
                f"{WORKSPACE}.{mapset}.{SCHEME_CODE}.{dekad_code}.tif")
    return f"{STORAGE}/MAPSET/{mapset}/{WORKSPACE}.{mapset}.{dekad_code}.tif"


def raster_exists(url: str) -> bool:
    """Whether a dekadal raster is published yet (WaPOR releases with a lag)."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


# -- raster sampling ------------------------------------------------------
def _rasterio():
    try:
        import rasterio  # noqa: PLC0415 - optional heavy dependency
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise WaporError(
            "Reading WaPOR GeoTIFFs requires rasterio, which is not installed. "
            "Run 'pip install rasterio', or set WWP_PROVIDER=synthetic to run "
            "the dashboard on demonstration data."
        ) from e
    return rasterio


def sample_raster(url: str, lats: np.ndarray, lons: np.ndarray,
                  scale: float | None = None, offset: float = 0.0) -> np.ndarray:
    """Sample one COG at the given coordinates and convert to physical units.

    Read through GDAL's ``/vsicurl/`` so only the tiles covering the requested
    points are fetched; these files run to hundreds of megabytes each.

    The scale factor comes from the GeoTIFF band, which is where the reference
    implementation reads it. A caller-supplied scale (from the catalogue, for
    an L1 mapset that publishes one) is used only when the file carries none,
    so a scale present in both places cannot be applied twice.
    """
    rasterio = _rasterio()
    with rasterio.open(f"/vsicurl/{url}") as src:
        if src.crs and src.crs.to_epsg() != 4326:
            raise WaporError(f"Expected an EPSG:4326 raster, got {src.crs} for {url}.")
        raw = np.array(
            [v[0] for v in src.sample(zip(lons.tolist(), lats.tolist()))],
            dtype=np.float64,
        )
        band_scale = (src.scales[0] if src.scales else None)
        band_offset = (src.offsets[0] if src.offsets else 0.0) or 0.0
        nodata = src.nodatavals[0]
    if band_scale not in (None, 0, 1.0):
        scale, offset = band_scale, band_offset
    if nodata is not None:
        raw[raw == nodata] = np.nan
    # Both products are non-negative, and the reference masks every negative
    # value rather than only the documented fill value.
    raw[raw < 0] = np.nan
    return raw * (scale if scale else 1.0) + offset


class WaporProvider:
    """Seasonal WaPOR v3 NPP and AETI for a set of coordinates."""

    name = f"wapor-v3-{LEVEL}" + (f"-{SCHEME_CODE}" if SCHEME_CODE else "")
    synthetic = False
    resolution_m = RESOLUTION_M

    def assemble(self, lat, lon, system: str, year: str, season: str) -> dict:
        from .wwpt import season_window  # local import: avoids a cycle

        sos, eos = season_window(system, year, season)
        return self.assemble_window(lat, lon, sos, eos)

    def assemble_window(self, lat, lon, sos: date, eos: date) -> dict:
        """Sum dekadal NPP and AETI over [sos, eos] — the notebook's window."""
        lat = np.asarray(lat, dtype=np.float64)
        lon = np.asarray(lon, dtype=np.float64)
        dekads = dekads_in(sos, eos)
        if len(dekads) < MIN_DEKADS:
            raise WaporError(
                f"The season {sos} to {eos} spans {len(dekads)} WaPOR dekads; at "
                f"least {MIN_DEKADS} are needed for a seasonal total. The "
                "reference implementation returns no data below this threshold."
            )

        totals = {}
        for key, mapset in (("npp", NPP_MAPSET), ("aeti", AETI_MAPSET)):
            meta = mapset_meta(mapset)
            scale = float(meta["scale"]) if meta.get("scale") else None
            offset = float(meta.get("offset") or 0.0)
            acc = np.zeros(lat.shape, dtype=np.float64)
            for code, days in dekads:
                url = raster_url(mapset, code)
                # Daily rate x contributing days = this dekad's seasonal share.
                acc += sample_raster(url, lat, lon, scale, offset) * days
            if np.isnan(acc).all():
                raise WaporError(
                    f"WaPOR {mapset} returned no valid data between {sos} and {eos} "
                    "for this extent."
                )
            totals[key] = np.nan_to_num(acc, nan=0.0)

        return {**totals, "sos": sos, "eos": eos, "n_dekads": len(dekads)}


def check_access() -> dict:
    """Verify the retrieval contract this provider depends on.

    Turns the assumptions documented at the top of this module into something
    observed: that the dekadal rasters for the configured level exist at the
    path built from the date, and that the units are what the per-dekad
    accumulation assumes. Run once per deployment before relying on any number
    the provider produces — reported through ``GET /api/wapor/check``.
    """
    report: dict = {
        "storage": STORAGE, "catalogue": GISMGR, "level": LEVEL,
        "scheme_code": SCHEME_CODE, "resolution_m": RESOLUTION_M,
        "ok": False, "checks": [],
    }

    def record(name, ok, detail=""):
        report["checks"].append({"check": name, "ok": bool(ok), "detail": str(detail)})
        return ok

    try:
        import rasterio  # noqa: F401, PLC0415
        record("rasterio installed", True, "pixel reads enabled")
    except ImportError:
        record("rasterio installed", False,
               "pip install rasterio — without it no pixel can be read")

    # A dekad old enough to be published everywhere WaPOR covers.
    probe = "2024-01-D1"
    for mapset in (NPP_MAPSET, AETI_MAPSET):
        url = raster_url(mapset, probe)
        record(f"{mapset}.{probe} raster exists", raster_exists(url), url)

        # L2 and L3 are served from storage but not listed by the catalogue, so
        # a missing entry is expected there and is not a failure. Where an entry
        # does exist, its unit must match what the accumulation assumes.
        meta = mapset_meta(mapset)
        if not meta:
            record(f"{mapset} catalogue entry", True,
                   "not listed (expected: the catalogue lists L1 mapsets only)")
            continue
        unit = meta.get("measureUnit") or "?"
        expected = EXPECTED_UNITS.get(mapset, "?")
        record(f"{mapset} catalogue entry", True,
               f"{meta.get('caption')} · scale {meta.get('scale')}")
        record(
            f"{mapset} unit is {expected}",
            str(unit).replace(" ", "") == expected.replace(" ", ""),
            f"catalogue says '{unit}' — if this differs, the per-dekad "
            "accumulation in assemble_window() must be corrected",
        )

    report["ok"] = all(c["ok"] for c in report["checks"])
    return report
