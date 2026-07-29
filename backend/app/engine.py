"""WWPT analytical engine.

Orchestrates one analysis run: AOI grid construction, feature assembly from
the data-access layer, LightGBM inference, raster rendering, zonal statistics,
class distribution and the multi-season trend. Mirrors the original
Python WWPT pipeline (WaPOR NPP -> features -> productivity) as a service.
"""

from __future__ import annotations

import base64
import math
import uuid

import numpy as np

from . import aoi as aoi_mod
from .geodata import PROVIDER, YEARS, feature_matrix, wheat_mask
from .model_service import MODEL
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


def run_analysis(aoi: dict, system: str, year: str, season: str) -> dict:
    lat2d, lon2d = _grid(aoi["bounds"], GRID_N)
    inside = aoi_mod.mask_for(aoi, lat2d, lon2d)
    wheat = wheat_mask(lat2d, lon2d)
    mask = inside & wheat
    if not mask.any():
        raise aoi_mod.AOIError("No wheat area found inside the selected extent.")

    feats = PROVIDER.assemble(lat2d[mask], lon2d[mask], system, year, season)
    wwp = MODEL.predict(feature_matrix(feats))

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

    # 5-season trend on a coarse grid (same extent, same season & system).
    tlat, tlon = _grid(aoi["bounds"], TREND_N)
    tmask = aoi_mod.mask_for(aoi, tlat, tlon) & wheat_mask(tlat, tlon)
    trend = []
    for yr in sorted(YEARS):
        tf = PROVIDER.assemble(tlat[tmask], tlon[tmask], system, yr, season)
        trend.append({
            "year": yr,
            "mean": round(float(MODEL.predict(feature_matrix(tf)).mean()), 3),
        })

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
        "yield_t_ha": round(mean * 3.1, 2),
        "et_mm": int(round(float(feats["aet"].mean()))),
        "npp_mean": int(round(float(feats["npp"].mean()))),
        "area_ha": int(round(_area_ha(aoi["bounds"], n, GRID_N))),
        "gap_pct": max(0, round((ATTAINABLE_WWP - mean) / ATTAINABLE_WWP * 100.0)),
        "histogram": hist,
        "trend": trend,
        "feature_importance": MODEL.importance(),
        "model_version": MODEL.meta.get("version"),
    }
    RUNS[run_id] = {"aoi": aoi, "system": system, "year": year, "season": season}
    if len(RUNS) > 100:
        RUNS.pop(next(iter(RUNS)))
    return result


def point_features(lat: float, lon: float, system: str, year: str, season: str):
    feats = PROVIDER.assemble(np.array([lat]), np.array([lon]), system, year, season)
    return feats, feature_matrix(feats)


def predict_point(lat, lon, system, year, season) -> dict:
    feats, X = point_features(lat, lon, system, year, season)
    wwp = float(MODEL.predict(X)[0])
    return {
        "wwp": round(wwp, 3),
        "yield_t_ha": round(wwp * 3.1, 2),
        "npp": int(round(float(feats["npp"][0]))),
        "aet_mm": int(round(float(feats["aet"][0]))),
        "lat": lat,
        "lon": lon,
    }


def explain_point(lat, lon, system, year, season) -> dict:
    from .geodata import FEATURE_LABELS, FEATURE_NAMES, FEATURE_UNITS

    feats, X = point_features(lat, lon, system, year, season)
    contrib, base = MODEL.explain(X)
    wwp = float(MODEL.predict(X)[0])
    rows = []
    for i, name in enumerate(FEATURE_NAMES):
        rows.append({
            "feature": name,
            "label": FEATURE_LABELS[name],
            "value": round(float(feats[name][0]), 2),
            "unit": FEATURE_UNITS[name],
            "contribution": round(float(contrib[0, i]), 4),
        })
    rows.sort(key=lambda r: -abs(r["contribution"]))
    return {
        "lat": lat, "lon": lon,
        "base": round(float(base[0]), 3),
        "prediction": round(wwp, 3),
        "contributions": rows[:7],
        "model_version": MODEL.meta.get("version"),
    }


def export_csv(run_id: str) -> str:
    run = RUNS.get(run_id)
    if not run:
        raise aoi_mod.AOIError("Run not found — please run the analysis again.")
    aoi, system, year, season = run["aoi"], run["system"], run["year"], run["season"]
    lat2d, lon2d = _grid(aoi["bounds"], CSV_N)
    mask = aoi_mod.mask_for(aoi, lat2d, lon2d) & wheat_mask(lat2d, lon2d)
    feats = PROVIDER.assemble(lat2d[mask], lon2d[mask], system, year, season)
    wwp = MODEL.predict(feature_matrix(feats))
    lines = ["lat,lon,wwp_kg_m3,pred_yield_t_ha,npp_kgc_ha,aet_mm"]
    la, lo = lat2d[mask], lon2d[mask]
    for i in range(len(wwp)):
        lines.append(
            f"{la[i]:.5f},{lo[i]:.5f},{wwp[i]:.3f},{wwp[i]*3.1:.2f},"
            f"{feats['npp'][i]:.0f},{feats['aet'][i]:.0f}"
        )
    return "\n".join(lines) + "\n"
