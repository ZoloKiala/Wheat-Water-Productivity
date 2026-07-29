"""Area-of-interest handling: admin units, drawn polygons and boundary uploads.

Uploads accept a zipped shapefile or a GeoJSON file (EPSG:4326). Geometry is
validated server-side and returned to the client as GeoJSON so the actual
boundary is displayed on the map. Uploaded AOIs are kept in an in-memory
registry keyed by upload id (production: spatial database table).
"""

from __future__ import annotations

import io
import json
import math
import uuid
import zipfile

import numpy as np
import shapefile

from .admin_units import get_woreda

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB, as stated in the UI

_UPLOADS: dict[str, dict] = {}


class AOIError(ValueError):
    pass


# ── geometry helpers ─────────────────────────────────────────────────────
def points_in_polygon(lats: np.ndarray, lons: np.ndarray, poly: list) -> np.ndarray:
    """Vectorized even-odd point-in-polygon. ``poly`` is [[lat, lon], ...]."""
    inside = np.zeros(lats.shape, dtype=bool)
    n = len(poly)
    j = n - 1
    for i in range(n):
        yi, xi = poly[i]
        yj, xj = poly[j]
        dx = xj - xi
        if abs(dx) < 1e-12:
            dx = 1e-12
        crosses = ((xi > lons) != (xj > lons)) & (lats < (yj - yi) * (lons - xi) / dx + yi)
        inside ^= crosses
        j = i
    return inside


def mask_for(aoi: dict, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Boolean mask of grid cells inside the AOI geometry."""
    polys = aoi.get("polys")
    if not polys:
        return np.ones(lats.shape, dtype=bool)
    inside = np.zeros(lats.shape, dtype=bool)
    for poly in polys:
        inside |= points_in_polygon(lats, lons, poly)
    return inside


def polygon_area_ha(poly: list) -> float:
    """Approximate geodesic area (ha) of a [[lat, lon], ...] ring."""
    a = 0.0
    j = len(poly) - 1
    for i in range(len(poly)):
        a += (poly[j][1] + poly[i][1]) * (poly[j][0] - poly[i][0])
        j = i
    km_per_deg = 110.57
    return abs(a / 2.0) * km_per_deg * km_per_deg * math.cos(math.radians(poly[0][0])) * 100.0


def _bounds_of(polys: list) -> list:
    lats = [p[0] for poly in polys for p in poly]
    lons = [p[1] for poly in polys for p in poly]
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


# ── AOI resolution ───────────────────────────────────────────────────────
def resolve(payload: dict) -> dict:
    """Turn an analysis-request AOI spec into {label, bounds, polys|None}."""
    kind = payload.get("aoi_type")
    if kind == "admin":
        region, zone, woreda = payload.get("region"), payload.get("zone"), payload.get("woreda")
        unit = get_woreda(region, zone, woreda)
        if not unit:
            raise AOIError("Unknown administrative unit.")
        (clat, clon), d = unit["c"], unit["d"]
        return {
            "label": f"{woreda} woreda, {zone} ({region})",
            "bounds": [[clat - d, clon - d], [clat + d, clon + d]],
            "polys": None,
        }
    if kind == "polygon":
        poly = payload.get("polygon") or []
        if len(poly) < 3:
            raise AOIError("A drawn polygon needs at least 3 vertices.")
        poly = [[float(p[0]), float(p[1])] for p in poly]
        return {"label": "Drawn polygon", "bounds": _bounds_of([poly]), "polys": [poly]}
    if kind == "upload":
        rec = _UPLOADS.get(payload.get("upload_id", ""))
        if not rec:
            raise AOIError("Upload not found — please upload the boundary again.")
        return rec
    raise AOIError("aoi_type must be one of: admin, polygon, upload.")


# ── uploads ──────────────────────────────────────────────────────────────
def register_upload(filename: str, content: bytes) -> dict:
    if len(content) > MAX_UPLOAD_BYTES:
        raise AOIError("File exceeds the 20 MB limit.")
    lower = filename.lower()
    if lower.endswith(".zip"):
        polys, crs_note = _read_zipped_shapefile(content)
    elif lower.endswith((".geojson", ".json")):
        polys, crs_note = _read_geojson(content), "EPSG:4326 (GeoJSON)"
    else:
        raise AOIError("Unsupported file type. Upload a zipped shapefile (.zip) or GeoJSON.")
    if not polys:
        raise AOIError("No polygon geometry found in the file.")
    bounds = _bounds_of(polys)
    _validate_extent(bounds)
    upload_id = uuid.uuid4().hex[:12]
    rec = {
        "upload_id": upload_id,
        "label": f"Uploaded boundary — {filename}",
        "bounds": bounds,
        "polys": polys,
        "n_polygons": len(polys),
        "crs": crs_note,
        "area_ha": round(sum(polygon_area_ha(p) for p in polys)),
    }
    _UPLOADS[upload_id] = rec
    return rec


def upload_geojson(rec: dict) -> dict:
    """GeoJSON (lon/lat order) of an upload record for map display."""
    return {
        "type": "MultiPolygon",
        "coordinates": [[[[p[1], p[0]] for p in poly]] for poly in rec["polys"]],
    }


def _validate_extent(bounds: list):
    [[s, w], [n, e]] = bounds
    if not (2.0 < s < 16.0 and 2.0 < n < 16.0 and 32.0 < w < 49.0 and 32.0 < e < 49.0):
        raise AOIError(
            "Boundary falls outside Ethiopia. Check that coordinates are in "
            "EPSG:4326 (longitude/latitude in degrees)."
        )
    if (n - s) > 3.0 or (e - w) > 3.0:
        raise AOIError("Boundary is too large — the analysis extent is limited to ~3°×3°.")


def _read_geojson(content: bytes) -> list:
    try:
        gj = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AOIError("File is not valid GeoJSON.")
    geoms = []

    def collect(g):
        if not isinstance(g, dict):
            return
        t = g.get("type")
        if t == "FeatureCollection":
            for f in g.get("features", []):
                collect(f)
        elif t == "Feature":
            collect(g.get("geometry"))
        elif t == "Polygon":
            geoms.append(g["coordinates"])
        elif t == "MultiPolygon":
            geoms.extend(g["coordinates"])

    collect(gj)
    polys = []
    for polygon in geoms:
        if polygon and polygon[0] and len(polygon[0]) >= 3:
            polys.append([[float(pt[1]), float(pt[0])] for pt in polygon[0]])  # outer ring only
    return polys


def _read_zipped_shapefile(content: bytes) -> tuple:
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise AOIError("File is not a valid ZIP archive.")
    names = {n.lower().rsplit(".", 1)[-1]: n for n in zf.namelist() if "." in n}
    if "shp" not in names:
        raise AOIError("ZIP does not contain a .shp file.")
    shp = io.BytesIO(zf.read(names["shp"]))
    dbf = io.BytesIO(zf.read(names["dbf"])) if "dbf" in names else None
    shx = io.BytesIO(zf.read(names["shx"])) if "shx" in names else None

    crs_note = "CRS unknown (.prj missing) — EPSG:4326 assumed"
    if "prj" in names:
        prj = zf.read(names["prj"]).decode("utf-8", errors="ignore")
        if "4326" in prj or "WGS_1984" in prj or "WGS 84" in prj:
            crs_note = "EPSG:4326 (WGS 84)"
        else:
            raise AOIError(
                "Shapefile is not in EPSG:4326. Reproject the boundary to WGS 84 "
                "geographic coordinates and upload again."
            )
    try:
        reader = shapefile.Reader(shp=shp, dbf=dbf, shx=shx)
        polys = []
        for shape in reader.shapes():
            if shape.shapeType not in (shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM):
                continue
            parts = list(shape.parts) + [len(shape.points)]
            # Outer ring per part (holes ignored for the analysis mask).
            for k in range(len(parts) - 1):
                ring = shape.points[parts[k]:parts[k + 1]]
                if len(ring) >= 3:
                    polys.append([[float(pt[1]), float(pt[0])] for pt in ring])
                    break  # first ring of each shape only
    except Exception:
        raise AOIError("Could not read the shapefile — the archive may be corrupt.")
    return polys, crs_note
