"""Per-scheme analysis: the reference notebook's own input and output shape.

The area-of-interest journeys in ``engine.py`` answer "how productive is this
extent?" over a continuous grid. The reference notebook
(``ETH_WWP_WaPORv3_Irrigaed_Wheat_2026_Final.ipynb``) answers a different
question, and it is the question irrigation schemes actually ask: given a
shapefile of plots or sample points, each carrying its own growing season, what
is the yield and water productivity of *each* one?

Input and output therefore follow the notebook rather than this service's
earlier conventions:

* input   a boundary or point shapefile with ID, SOS, EOS and, for points,
  Location, exactly what ``etwapor.util.validate_input`` requires
* per-feature output   NPP, EYield_tpha, AETI_mm, WP_kgpm3, LGP appended to the
  attributes the file arrived with
* aggregate output   point samples collapsed to one row per plot by median,
  grouped the way notebook cell 26 groups them

Each feature is estimated over its own [SOS, EOS] window, so a file mixing
seasons (the 2026 Afar and Oromia schemes do) is handled the way the notebook
handles it, one feature at a time.
"""

from __future__ import annotations

import uuid
from statistics import median

import numpy as np

from . import aoi as aoi_mod
from . import wwpt
from .geodata import PROVIDER

# Result columns, in the notebook's own order.
RESULT_COLS = ["NPP", "EYield_tpha", "AETI_mm", "WP_kgpm3", "LGP"]

# Notebook cell 26: group_cols for the point-sample aggregation.
GROUP_COLS = ["Name", "Location", "SOS", "EOS", "LGP", "Scheme_ID"]
AGG_COLS = ["NPP", "AETI_mm", "EYield_tpha", "WP_kgpm3"]

POLY_GRID = 24          # sampling grid per axis inside a plot boundary
MAX_FEATURES = 500      # well beyond anything one season's campaign produces

RUNS: dict[str, dict] = {}


def _sample_points(feat: dict) -> tuple:
    """Coordinates at which to evaluate one feature.

    A point sample is evaluated where it sits. A boundary is evaluated on a
    grid clipped to the ring, which is the zonal mean the notebook computes
    over the WaPOR cells falling inside the plot. Boundaries smaller than the
    grid step fall back to their centroid rather than returning nothing.
    """
    if feat.get("point"):
        lat, lon = feat["point"]
        return np.array([lat]), np.array([lon])
    ring = feat["poly"]
    lats = [p[0] for p in ring]
    lons = [p[1] for p in ring]
    la = np.linspace(min(lats), max(lats), POLY_GRID)
    lo = np.linspace(min(lons), max(lons), POLY_GRID)
    lat2d, lon2d = np.meshgrid(la, lo, indexing="ij")
    inside = aoi_mod.points_in_polygon(lat2d, lon2d, ring)
    if not inside.any():
        return (np.array([sum(lats) / len(lats)]), np.array([sum(lons) / len(lons)]))
    return lat2d[inside], lon2d[inside]


def _estimate_feature(feat: dict) -> dict:
    """One feature's attributes plus the five result columns."""
    attrs = dict(feat["attrs"])
    sos = wwpt._as_date(attrs.get("SOS"))
    eos = wwpt._as_date(attrs.get("EOS"))
    lats, lons = _sample_points(feat)
    data = PROVIDER.assemble_window(lats, lons, sos, eos)
    est = wwpt.estimate(data["npp"], data["aeti"])
    npp = float(np.mean(data["npp"]))
    aeti = float(np.mean(data["aeti"]))
    yield_t_ha = float(np.mean(est["yield_t_ha"]))
    attrs.update({
        "NPP": round(npp, 2),
        "EYield_tpha": round(yield_t_ha, 2),
        "AETI_mm": round(aeti, 2),
        # Ratio of the plot means, matching etwapor.productivity: it computes
        # WP as mean_yield * 100 / mean_AETI. A ratio does not commute with
        # averaging, so this differs slightly from the mean of the per-cell
        # ratios; the reference implementation is what a plot figure has to
        # agree with, so the reference convention wins here. (The area journey
        # in engine.py averages per-cell ratios, which is the right choice for
        # a distribution over an extent, and says so.)
        "WP_kgpm3": round(yield_t_ha * 100.0 / aeti, 2) if aeti else None,
        "LGP": wwpt.lgp_days(sos, eos),
    })
    attrs["n_samples"] = int(len(lats))
    return attrs


def _median_rows(rows: list[dict]) -> list[dict] | None:
    """Notebook cell 26: median of the result columns per plot.

    Grouped by whichever of the notebook's grouping columns the file actually
    carries. With none of them present there is nothing to group by, and
    collapsing every sample into a single row would silently merge unrelated
    plots, so no aggregate is produced at all.
    """
    keys = [c for c in GROUP_COLS if any(c in r for r in rows)]
    if not keys:
        return None
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(r.get(k) for k in keys), []).append(r)
    out = []
    for key, members in groups.items():
        row = dict(zip(keys, key))
        for col in AGG_COLS:
            vals = [m[col] for m in members if isinstance(m.get(col), (int, float))]
            row[col] = round(median(vals), 2) if vals else None
        row["n_samples"] = len(members)
        out.append(row)
    out.sort(key=lambda r: (str(r.get("Name") or ""), str(r.get("Location") or "")))
    return out


def _chart_rows(rows: list[dict]) -> list[dict]:
    """Bars for the notebook's two figures: yield and WP per scheme."""
    return [
        {
            "label": str(r.get("Name") or r.get("Scheme_ID") or f"Plot {i + 1}"),
            "scheme": str(r.get("Scheme_ID") or ""),
            "yield_t_ha": r.get("EYield_tpha"),
            "wwp": r.get("WP_kgpm3"),
        }
        for i, r in enumerate(rows)
    ]


def analyse(rec: dict) -> dict:
    """Estimate every feature in an uploaded scheme file."""
    feats = rec.get("features") or []
    if not feats:
        raise aoi_mod.AOIError("No features found in the uploaded file.")
    if len(feats) > MAX_FEATURES:
        raise aoi_mod.AOIError(
            f"File holds {len(feats)} features; the analysis is limited to {MAX_FEATURES}."
        )
    geometry_type = rec.get("geometry_type", "polygon")
    problems = wwpt.validate_features([f["attrs"] for f in feats], geometry_type)
    if problems:
        detail = " ".join(problems[:6])
        if len(problems) > 6:
            detail += f" (+{len(problems) - 6} more)"
        raise aoi_mod.AOIError("Input validation failed. " + detail)

    rows = [_estimate_feature(f) for f in feats]
    attr_cols = [k for k in rows[0] if k not in RESULT_COLS and k != "n_samples"]
    aggregate = _median_rows(rows) if geometry_type == "point" else None

    run_id = uuid.uuid4().hex[:12]
    RUNS[run_id] = {
        "rows": rows,
        "aggregate": aggregate,
        "columns": attr_cols + RESULT_COLS,
        "filename": rec.get("filename", "schemes"),
    }
    if len(RUNS) > 100:
        RUNS.pop(next(iter(RUNS)))

    seasons = sorted({(r.get("SOS"), r.get("EOS")) for r in rows})
    agg_block = None
    if aggregate:
        agg_block = {
            "group_cols": [c for c in GROUP_COLS if c in aggregate[0]],
            "value_cols": AGG_COLS,
            "rows": aggregate,
        }
    return {
        "run_id": run_id,
        "label": rec.get("label", "Uploaded schemes"),
        "filename": rec.get("filename"),
        "geometry_type": geometry_type,
        "bounds": rec.get("bounds"),
        "n_features": len(rows),
        "validation": {"ok": True, "message": "Data validation is successful!"},
        "columns": attr_cols + RESULT_COLS,
        "result_columns": RESULT_COLS,
        "features": rows,
        "aggregate": agg_block,
        "grouping_note": wwpt.grouping_note([f["attrs"] for f in feats], geometry_type),
        "charts": _chart_rows(aggregate if aggregate else rows),
        "season_windows": [{"sos": s, "eos": e} for s, e in seasons],
        "method": wwpt.method_info(),
        "provider": PROVIDER.name,
        "synthetic": getattr(PROVIDER, "synthetic", False),
    }


def export_csv(run_id: str, level: str = "features") -> str:
    """Results as CSV, matching the notebook's saved files.

    ``level=features`` reproduces ``Irrigated_Wheat_WP_*_2026.csv`` (every
    feature, geometry dropped); ``level=schemes`` reproduces the aggregated
    table.
    """
    run = RUNS.get(run_id)
    if not run:
        raise aoi_mod.AOIError("Run not found — please run the analysis again.")
    if level == "schemes":
        rows = run["aggregate"]
        if not rows:
            raise aoi_mod.AOIError("This run has no aggregated table (boundary input).")
        cols = [c for c in GROUP_COLS if c in rows[0]] + AGG_COLS
    else:
        rows, cols = run["rows"], run["columns"]

    def cell(value):
        text = "" if value is None else str(value)
        if any(ch in text for ch in (",", '"', "\n")):
            return '"' + text.replace('"', '""') + '"'
        return text

    lines = [",".join(cols)]
    lines += [",".join(cell(r.get(c)) for c in cols) for r in rows]
    return "\n".join(lines) + "\n"


# -- a file to try the workflow with ---------------------------------------
# The workflow is invisible until a file with per-feature seasons is loaded, so
# the dashboard offers one. Generated rather than shipped as a data file: it
# carries no field observations, and it stays in step with the validator above.
# Where the machine holds the campaign shapefiles themselves, ``campaign.py``
# offers those as well and the interface lists them first — this is the stand-in
# for everywhere else, which includes every public deployment.
# The six plots of the 2026 campaign, with their real names, scheme codes and
# growing seasons, so the shape of the sample matches the shape of the data the
# tool is for. Coordinates are approximate scheme locations, not the surveyed
# field boundaries: those are IWMI field data and are not redistributed here.
SAMPLE_PLOTS = [
    # name, location, scheme id, SOS, EOS, lat, lon
    ("Amibara", 1, "AF001", "2025-11-22", "2026-02-20", 9.3445, 40.1692),
    ("Dubti", 2, "AF002", "2025-11-20", "2026-03-16", 11.7376, 41.1188),
    ("Dubti", 3, "AF002", "2025-11-20", "2026-03-16", 11.7340, 41.1090),
    ("Dodota", 4, "OR001", "2025-12-11", "2026-04-01", 8.2944, 39.3839),
    ("Godino", 5, "OR002", "2025-12-07", "2026-03-13", 8.8477, 39.0223),
    ("Dodota", 6, "OR001", "2024-12-21", "2025-04-02", 8.3020, 39.3910),
]


def sample_file() -> dict:
    """A small point file in the reference notebook's schema."""
    features = []
    for plot, (name, loc, sid, sos, eos, lat, lon) in enumerate(SAMPLE_PLOTS, start=1):
        for k in range(5):
            features.append({
                "type": "Feature",
                "properties": {
                    "ID": (plot - 1) * 5 + k + 1, "Name": name, "Location": loc,
                    "SOS": sos, "EOS": eos, "Crop": "Wheat", "Scheme_ID": sid,
                    "Crop_ID": "CER01",
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(lon + 0.0009 * k, 6), round(lat + 0.0007 * k, 6)],
                },
            })
    return {"type": "FeatureCollection", "features": features}
