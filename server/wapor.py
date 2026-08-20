"""WaPOR v3 retrieval and seasonal aggregation — the analysis logic.

Mirrors ``etwapor.download.WaPORDownload`` and
``etwapor.productivity.process_single_feature`` so this server computes what the
reference notebook computes, with two differences that are stated rather than
hidden:

1. **Decade suffix.** WaPOR publishes three decadal rasters per month, ``D1``
   (days 1–10), ``D2`` (11–20) and ``D3`` (21–end). ``etwapor`` emits ``D1`` in
   all three branches of ``_get_storage_url`` — only its date *label* changes —
   so a season sums the first decade repeatedly instead of the three distinct
   decades. All three files exist and differ (verified by content length), so
   this is a defect, not a naming quirk. ``decade_urls()`` builds the correct
   D1/D2/D3 list; ``mirror_etwapor=True`` reproduces the notebook's behaviour
   for comparison.

2. **Aggregation guard.** ``compute_seasonal_biomass`` uses ``min_count=6``, so a
   season with fewer than six valid decades yields NaN. Kept, and surfaced as an
   explicit error instead of a silent NaN.

Reading the rasters needs rioxarray/rasterio (GDAL). Without them
``available()`` is False and the caller must not claim a WaPOR result.
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Iterable, Literal, Optional

GOOGLE = "https://storage.googleapis.com/fao-gismgr-wapor-3-data/DATA/WAPOR-3/"
GISMGR = "https://gismgr.fao.org/DATA/WAPOR-3/"

Storage = Literal["google", "gismgr"]


def _base(storage: Storage) -> str:
    return GISMGR if storage == "gismgr" else GOOGLE


def decade_of(day: int) -> int:
    """1, 2 or 3 — the decade a day falls in, as WaPOR splits the month."""
    if day < 11:
        return 1
    if day < 21:
        return 2
    return 3


def decade_starts(sos: str, eos: str) -> list[dt.date]:
    """Every decade whose start falls in [sos, eos], in order.

    A decade is included when it *starts* inside the season, matching the way
    etwapor walks the daily date range and collapses it to a set of decades.
    """
    start = dt.date.fromisoformat(sos)
    end = dt.date.fromisoformat(eos)
    out: list[dt.date] = []
    year, month = start.year, start.month
    while dt.date(year, month, 1) <= end:
        for first in (1, 11, 21):
            d = dt.date(year, month, first)
            if start <= d <= end:
                out.append(d)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def decade_urls(
    sos: str,
    eos: str,
    mapset: str = "L2-NPP-D",
    scheme_code: Optional[str] = None,
    storage: Storage = "google",
    mirror_etwapor: bool = False,
) -> list[tuple[str, str]]:
    """(decade start date, raster URL) for the season, oldest first.

    With ``mirror_etwapor`` the D1 raster is used for every decade, exactly as
    the reference implementation does — the same list the notebook fetched.
    """
    base = _base(storage)
    urls: list[tuple[str, str]] = []
    for d in decade_starts(sos, eos):
        suffix = "D1" if mirror_etwapor else f"D{decade_of(d.day)}"
        stem = f"WAPOR-3.{mapset}"
        if scheme_code:
            url = f"{base}MOSAICSET/{mapset}/{stem}.{scheme_code}.{d:%Y-%m}-{suffix}.tif"
        else:
            url = f"{base}MAPSET/{mapset}/{stem}.{d:%Y-%m}-{suffix}.tif"
        urls.append((d.isoformat(), url))
    return urls


def check_urls(urls: Iterable[tuple[str, str]], timeout: float = 20.0) -> list[dict]:
    """HEAD each raster: does the season's data actually exist and differ?

    Cheap next to a download, and it catches the two failures that otherwise
    surface late — a season with no published data, and a URL pattern that
    silently resolves to the same file for every decade.
    """
    import requests

    out = []
    for date, url in urls:
        row: dict = {"date": date, "url": url}
        try:
            r = requests.head(url, timeout=timeout, allow_redirects=True)
            row["status"] = r.status_code
            row["bytes"] = int(r.headers.get("content-length") or 0) or None
        except Exception as exc:  # network is the expected failure here
            row["status"] = None
            row["error"] = f"{type(exc).__name__}: {exc}"
        out.append(row)
    return out


def available() -> tuple[bool, Optional[str]]:
    """Can this process read the rasters? (rioxarray/rasterio present)"""
    try:
        import rioxarray  # noqa: F401
        import xarray  # noqa: F401
        from rasterio.enums import Resampling  # noqa: F401

        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def decade_days(sos: str, eos: str, starts: list[dt.date], i: int) -> int:
    """Days of decade `i` that fall inside the season.

    Mirrors ``WaPORDownload._compute_days``: the first and last decades are
    clipped to SOS and EOS, the rest take the full decade (10 days, or
    days-in-month minus 20 for the third). The decadal rasters hold a *daily
    rate*, so this weight is what turns one into a decade total.
    """
    d = starts[i]
    start = dt.date.fromisoformat(sos)
    end = dt.date.fromisoformat(eos)
    if i == 0:
        if d.day < 21:
            return (d + dt.timedelta(days=10) - start).days
        last = dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
        return (last - start).days + 1
    if i == len(starts) - 1:
        return (end - d).days + 1
    if d.day < 21:
        return 10
    return calendar.monthrange(d.year, d.month)[1] - 20


def seasonal_sum(
    urls: list[tuple[str, str]],
    geometry,
    sos: str,
    eos: str,
    min_count: int = 6,
    all_touched: bool = False,
) -> float:
    """Sum the decadal rasters over the season, then average over the geometry.

    The order matters and is the notebook's: sum through time per pixel first
    (a seasonal total), then take the spatial mean of those totals. Averaging
    the other way round gives a different number whenever the clip has NaNs.

    Returns the feature mean of the seasonal total. Raises if fewer than
    ``min_count`` decades carry data, rather than returning NaN.
    """
    import numpy as np
    import rioxarray  # noqa: F401
    import xarray as xr

    starts = [dt.date.fromisoformat(d) for d, _ in urls]
    layers = []
    for i, (date, url) in enumerate(urls):
        try:
            # NOT masked=True: that masks nodata but leaves scale_factor unapplied,
            # so values come back 1000x too large. Read raw and scale explicitly,
            # exactly as etwapor does.
            da = rioxarray.open_rasterio(url, chunks=True)
        except Exception:
            continue                      # a missing decade is skipped, as upstream
        try:
            clipped = da.rio.clip([geometry], crs="EPSG:4326", all_touched=all_touched,
                                  drop=True, from_disk=True)
        except Exception:
            continue                      # geometry outside this raster
        clipped = clipped.squeeze(drop=True)
        scale = float(clipped.attrs.get("scale_factor", 1.0) or 1.0)
        ndays = decade_days(sos, eos, starts, i)
        # Nodata is negative in these products; mask before scaling.
        clipped = xr.where(clipped < 0, np.nan, clipped) * scale * ndays
        layers.append(clipped)

    if len(layers) < min_count:
        raise ValueError(
            f"only {len(layers)} of {len(urls)} decades carried data for this "
            f"geometry; the notebook requires at least {min_count}"
        )

    stack = xr.concat(layers, dim="time")
    seasonal = stack.sum(dim="time", skipna=True, min_count=min_count)
    value = float(np.asarray(seasonal.mean().values).squeeze())
    if not np.isfinite(value):
        raise ValueError("seasonal aggregate is not finite (all-NaN clip)")
    return value
