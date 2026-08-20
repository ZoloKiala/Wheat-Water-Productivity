"""The 2026 campaign shapefiles, offered when they are present on the machine.

``schemes.sample_file`` generates a small point file so the per-plot workflow can
be tried by anyone. This module offers the real thing instead when it is
available: ``Irrigated_Wheat_2026.shp`` (six irrigation-scheme boundaries) and
``Irrigated_Wheat_2026_PNT.shp`` (57 sample points), the two files the reference
notebook reads in its cell 4.

Why the files are read rather than shipped
------------------------------------------
They are IWMI field data. Keeping them out of the repository was a deliberate
decision, and embedding their coordinates in code would undo it, so nothing here
holds any geometry: the directory is located at run time and the shapefiles are
read from disk with ``aoi.read_shapefile_on_disk``. Where they are absent — any
public deployment — ``available()`` returns nothing and the interface offers only
the generated sample. Point ``WWP_CAMPAIGN_DATA`` at the notebook's ``Data``
directory to use a copy held elsewhere.

What the service does with them
-------------------------------
Exactly what it does with an upload. The dataset is handed to the client as
GeoJSON and posted back through ``POST /api/upload``, so loading the campaign
files exercises the same parsing, the same validation and the same estimation as
a user's own file. Nothing about these six schemes is special-cased.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import aoi as aoi_mod

# The repository root, where the notebook's own ``Data`` directory sits.
REPO_ROOT = Path(__file__).resolve().parents[2]

DATASETS: dict[str, dict] = {
    "campaign-boundaries": {
        "base": "Irrigated_Wheat_2026",
        "label": "2026 campaign plot boundaries",
        "note": "The six irrigation schemes of the reference notebook, as surveyed.",
    },
    "campaign-points": {
        "base": "Irrigated_Wheat_2026_PNT",
        "label": "2026 campaign sample points",
        "note": "The 57 field samples, grouped into plots by Location.",
    },
}

SOURCE = "IWMI 2026 irrigated-wheat campaign (read from this machine, not shipped)"

# Parsed features, keyed by path and modification time so an edited file is
# re-read rather than served from a stale copy.
_CACHE: dict[tuple, list] = {}


def data_dir() -> Path:
    """Directory holding the campaign shapefiles."""
    return Path(os.environ.get("WWP_CAMPAIGN_DATA") or REPO_ROOT / "Data")


def _features(name: str) -> list:
    spec = DATASETS[name]
    base = data_dir() / spec["base"]
    shp = base.with_suffix(".shp")
    key = (str(shp), shp.stat().st_mtime_ns)
    if key not in _CACHE:
        feats, _crs = aoi_mod.read_shapefile_on_disk(base)
        for stale in [k for k in _CACHE if k[0] == key[0]]:
            _CACHE.pop(stale)
        _CACHE[key] = feats
    return _CACHE[key]


def available() -> list[dict]:
    """The campaign datasets found on this machine, in loading order.

    Reads each file to report how many features it holds, so a truncated or
    unreadable copy is reported as absent here rather than failing later, when
    the user has already clicked.
    """
    found = []
    for name, spec in DATASETS.items():
        if not (data_dir() / spec["base"]).with_suffix(".shp").exists():
            continue
        try:
            feats = _features(name)
        except (aoi_mod.AOIError, OSError):
            continue
        if not feats:
            continue
        found.append({
            "name": name,
            "kind": "campaign",
            "label": spec["label"],
            "note": spec["note"],
            "filename": f"{spec['base']}.geojson",
            "geometry_type": "point" if feats[0].get("point") else "polygon",
            "n_features": len(feats),
            "source": SOURCE,
        })
    return found


def load(name: str) -> dict:
    """One campaign dataset as GeoJSON, ready to be posted back to /upload."""
    spec = DATASETS.get(name)
    if not spec:
        raise aoi_mod.AOIError(f"Unknown dataset '{name}'.")
    if not (data_dir() / spec["base"]).with_suffix(".shp").exists():
        raise aoi_mod.AOIError(
            f"{spec['label']} is not available on this machine. The campaign "
            "shapefiles are IWMI field data and are not shipped with the "
            "service; set WWP_CAMPAIGN_DATA to the directory holding them."
        )
    feats = _features(name)
    return {
        "filename": f"{spec['base']}.geojson",
        "label": spec["label"],
        "source": SOURCE,
        "geojson": aoi_mod.features_geojson(feats),
    }
