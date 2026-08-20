"""WWPT analytical engine.

Orchestrates one analysis run: AOI grid construction, seasonal WaPOR retrieval,
the WWPT biomass-to-yield estimation, raster rendering, zonal statistics, class
distribution and the multi-season trend.

The estimate itself is deterministic — ``wwpt.estimate`` applied per grid cell —
so every number on the dashboard can be traced back through the chain that
produced it. That is what ``estimation_chain`` below builds, and it is what the
interface shows in place of a model explanation.
"""

from __future__ import annotations

import base64
import math
import uuid

import numpy as np

from . import aoi as aoi_mod
from . import wwpt
from .geodata import PROVIDER, YEARS, wheat_mask
from .pnglib import encode_png

GRID_N = 170          # analysis raster resolution (cells per axis)
TREND_N = 42          # coarse grid used for the 5-season trend
CSV_N = 60            # export grid resolution
ATTAINABLE_WWP = 1.62  # P95 of the basin distribution (kg/m3)

# Sequential ramp for WWP magnitude (kg/m3 -> RGB): one hue, light -> dark.
# Monotone in lightness so the order survives colour-vision deficiency and
# greyscale print; the light end clears 2:1 contrast on the card surface.
# Kept identical to RAMP_HEX in the frontend so the map, the legend and the
# distribution chart all speak the same scale.
RAMP = [
    (0.4, (147, 189, 130)),
    (0.7, (107, 167, 99)),
    (1.0, (69, 139, 75)),
    (1.3, (41, 112, 56)),
    (1.6, (15, 77, 38)),
]

HIST_EDGES = [0.0, 0.6, 0.9, 1.2, 1.5, math.inf]
HIST_LABELS = ["<0.6", "0.6–0.9", "0.9–1.2", "1.2–1.5", ">1.5"]

RUNS: dict[str, dict] = {}


def _grid(bounds, n):
    [[s, w], [nn, e]] = bounds
    lats = nn - (np.arange(n) + 0.5) / n * (nn - s)
    lons = w + (np.arange(n) + 0.5) / n * (e - w)
    return np.meshgrid(lats, lons, indexing="ij")  # (lat2d, lon2d)


def _colorize(values: np.ndarray) -> np.ndarray:
    """Map WWP values to RGB using piecewise-linear interpolation on RAMP."""
    stops = np.array([s for s, _ in RAMP])
    cols = np.array([c for _, c in RAMP], dtype=np.float64)
    v = np.clip(values, stops[0], stops[-1])
    idx = np.clip(np.searchsorted(stops, v) - 1, 0, len(stops) - 2)
    t = (v - stops[idx]) / (stops[idx + 1] - stops[idx])
    rgb = cols[idx] + (cols[idx + 1] - cols[idx]) * t[..., None]
    return np.round(rgb).astype(np.uint8)


def _area_ha(bounds, n_cells, grid_n):
    [[s, w], [nn, e]] = bounds
    km = 110.57
    total_km2 = (nn - s) * km * (e - w) * km * math.cos(math.radians((s + nn) / 2))
    return total_km2 * 100.0 * n_cells / (grid_n * grid_n)


def estimation_chain(npp, aeti_mm, wwp=None) -> list[dict]:
    """The NPP -> biomass -> yield -> water-productivity derivation, step by step.

    Every step but the last is linear in NPP, so applying the chain to mean NPP
    gives exactly the mean biomass and the mean yield. Water productivity is a
    ratio and does not commute with averaging, so when an area mean is supplied
    it is used for the final step rather than the ratio of the means — the two
    differ, and the honest number is the mean of the per-cell values.
    """
    p = wwpt.PARAMS
    npp = float(np.mean(npp))
    aeti_mm = float(np.mean(aeti_mm))
    tb = float(wwpt.total_biomass(npp))
    y = tb * p.hi
    swc = float(wwpt.seasonal_water(aeti_mm))
    # 'role' tells the interface how to draw each step: a source measurement, a
    # value derived from the one above it, the divisor branch, or the result.
    return [
        {"role": "source", "step": "Seasonal NPP",
         "value": round(npp, 1), "unit": "gC/m²",
         "detail": "FAO WaPOR v3, summed over the growing season"},
        {"role": "derived", "step": "Total biomass",
         "value": round(tb), "unit": "kg DM/ha",
         "detail": f"AOT {p.aot} × fc {p.fc} × 22.222 ÷ (1 − mc {p.mc})"},
        {"role": "derived", "step": "Grain yield",
         "value": round(y), "unit": "kg/ha",
         "detail": f"harvest index {p.hi}"},
        {"role": "divisor", "step": "Water consumed",
         "value": round(swc), "unit": "m³/ha",
         "detail": f"seasonal AETI {aeti_mm:.0f} mm × 10"},
        {"role": "result", "step": "Water productivity",
         "value": round(float(np.mean(wwp)) if wwp is not None else y / swc, 3),
         "unit": "kg/m³", "detail": "grain yield ÷ water consumed"},
    ]


def _season_meta(feats: dict) -> dict:
    sos, eos = feats["sos"], feats["eos"]
    return {
        "sos": sos.isoformat(),
        "eos": eos.isoformat(),
        "lgp_days": wwpt.lgp_days(sos, eos),
        "n_dekads": feats.get("n_dekads"),
    }


def run_analysis(aoi: dict, system: str, year: str, season: str) -> dict:
    lat2d, lon2d = _grid(aoi["bounds"], GRID_N)
    inside = aoi_mod.mask_for(aoi, lat2d, lon2d)
    wheat = wheat_mask(lat2d, lon2d)
    mask = inside & wheat
    if not mask.any():
        raise aoi_mod.AOIError("No wheat area found inside the selected extent.")

    feats = PROVIDER.assemble(lat2d[mask], lon2d[mask], system, year, season)
    est = wwpt.estimate(feats["npp"], feats["aeti"])
    wwp = est["wwp_kg_m3"]

    # Raster (RGBA, transparent outside AOI / non-wheat cells).
    rgba = np.zeros((GRID_N, GRID_N, 4), dtype=np.uint8)
    rgba[mask, :3] = _colorize(wwp)
    rgba[mask, 3] = 225
    png_b64 = base64.b64encode(encode_png(rgba)).decode("ascii")

    # Zonal statistics.
    srt = np.sort(wwp)
    n = len(srt)
    stats = {
        "mean": round(float(wwp.mean()), 3),
        "p10": round(float(srt[int(n * 0.10)]), 3),
        "p90": round(float(srt[min(n - 1, int(n * 0.90))]), 3),
        "n_cells": n,
    }

    # Class distribution.
    hist = []
    for i in range(5):
        share = float(np.mean((wwp >= HIST_EDGES[i]) & (wwp < HIST_EDGES[i + 1])))
        hist.append({"label": HIST_LABELS[i], "pct": round(share * 100.0, 1)})

    mean = stats["mean"]
    run_id = uuid.uuid4().hex[:12]
    result = {
        "run_id": run_id,
        "label": aoi["label"],
        "bounds": aoi["bounds"],
        "system": system,
        "year": year,
        "season": season,
        "raster_png": "data:image/png;base64," + png_b64,
        "stats": stats,
        "yield_t_ha": round(float(est["yield_t_ha"].mean()), 2),
        "et_mm": int(round(float(feats["aeti"].mean()))),
        "npp_mean": int(round(float(feats["npp"].mean()))),
        "biomass_kg_ha": int(round(float(est["biomass_kg_ha"].mean()))),
        "area_ha": int(round(_area_ha(aoi["bounds"], n, GRID_N))),
        "gap_pct": max(0, round((ATTAINABLE_WWP - mean) / ATTAINABLE_WWP * 100.0)),
        "histogram": hist,
        "trend": _trend(aoi, system, season, wheat_mask),
        "chain": estimation_chain(feats["npp"], feats["aeti"], wwp),
        "season_window": _season_meta(feats),
        "method": wwpt.method_info(),
        "provider": PROVIDER.name,
        "synthetic": getattr(PROVIDER, "synthetic", False),
        "resolution_m": getattr(PROVIDER, "resolution_m", None),
    }
    RUNS[run_id] = {"aoi": aoi, "system": system, "year": year, "season": season}
    if len(RUNS) > 100:
        RUNS.pop(next(iter(RUNS)))
    return result


def _trend(aoi: dict, system: str, season: str, mask_fn) -> list[dict]:
    """Mean WWP across the available seasons on a coarse grid.

    A season whose WaPOR dekads are not yet published is skipped rather than
    failing the whole analysis — the current cropping year is routinely
    incomplete, and a missing point on the trend line is far better than no
    result at all.
    """
    tlat, tlon = _grid(aoi["bounds"], TREND_N)
    tmask = aoi_mod.mask_for(aoi, tlat, tlon) & mask_fn(tlat, tlon)
    if not tmask.any():
        return []
    trend = []
    for yr in sorted(YEARS):
        try:
            tf = PROVIDER.assemble(tlat[tmask], tlon[tmask], system, yr, season)
            wwp = wwpt.water_productivity(tf["npp"], tf["aeti"])
        except Exception:  # noqa: BLE001 - any retrieval failure drops the point
            continue
        trend.append({"year": yr, "mean": round(float(wwp.mean()), 3)})
    return trend


def point_features(lat: float, lon: float, system: str, year: str, season: str):
    return PROVIDER.assemble(np.array([lat]), np.array([lon]), system, year, season)


def predict_point(lat, lon, system, year, season) -> dict:
    feats = point_features(lat, lon, system, year, season)
    est = wwpt.estimate(feats["npp"], feats["aeti"])
    return {
        "wwp": round(float(est["wwp_kg_m3"][0]), 3),
        "yield_t_ha": round(float(est["yield_t_ha"][0]), 2),
        "npp": round(float(feats["npp"][0]), 1),
        "aeti_mm": int(round(float(feats["aeti"][0]))),
        "lat": lat,
        "lon": lon,
        "synthetic": getattr(PROVIDER, "synthetic", False),
    }


def explain_point(lat, lon, system, year, season) -> dict:
    """The full derivation for one cell.

    With a deterministic method there is nothing to attribute statistically:
    the explanation *is* the chain of equations and the parameters they used,
    which is both complete and checkable by hand.
    """
    feats = point_features(lat, lon, system, year, season)
    est = wwpt.estimate(feats["npp"], feats["aeti"])
    return {
        "lat": lat,
        "lon": lon,
        "wwp": round(float(est["wwp_kg_m3"][0]), 3),
        "chain": estimation_chain(feats["npp"], feats["aeti"]),
        "season_window": _season_meta(feats),
        "method": wwpt.method_info(),
    }


def export_csv(run_id: str) -> str:
    """Grid-cell results, using the reference notebook's column names."""
    run = RUNS.get(run_id)
    if not run:
        raise aoi_mod.AOIError("Run not found — please run the analysis again.")
    aoi, system, year, season = run["aoi"], run["system"], run["year"], run["season"]
    lat2d, lon2d = _grid(aoi["bounds"], CSV_N)
    mask = aoi_mod.mask_for(aoi, lat2d, lon2d) & wheat_mask(lat2d, lon2d)
    feats = PROVIDER.assemble(lat2d[mask], lon2d[mask], system, year, season)
    est = wwpt.estimate(feats["npp"], feats["aeti"])
    meta = _season_meta(feats)
    lines = ["lat,lon,SOS,EOS,LGP,NPP,AETI_mm,EYield_tpha,WP_kgpm3"]
    la, lo = lat2d[mask], lon2d[mask]
    for i in range(len(la)):
        lines.append(
            f"{la[i]:.5f},{lo[i]:.5f},{meta['sos']},{meta['eos']},{meta['lgp_days']},"
            f"{feats['npp'][i]:.2f},{feats['aeti'][i]:.2f},"
            f"{est['yield_t_ha'][i]:.2f},{est['wwp_kg_m3'][i]:.2f}"
        )
    return "\n".join(lines) + "\n"
