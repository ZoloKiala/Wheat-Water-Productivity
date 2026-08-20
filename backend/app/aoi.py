"""Area-of-interest handling: admin units, drawn polygons and boundary uploads.

Uploads accept a zipped shapefile or a GeoJSON file (EPSG:4326). Geometry is
validated server-side and returned to the client as GeoJSON so the actual
boundary is displayed on the map. Uploaded AOIs are kept in an in-memory
registry keyed by upload id (production: spatial database table).
"""

from __future__ import annotations

import datetime as dt
import io
import json
import math
import uuid
import zipfile
from pathlib import Path

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
        if not rec.get("polys"):
            raise AOIError(
                "This file contains sample points, not boundaries. Run the "
                "scheme analysis, which estimates each sample point in place."
            )
        _validate_aoi_size(rec["bounds"])
        return rec
    raise AOIError("aoi_type must be one of: admin, polygon, upload.")


# ── uploads ──────────────────────────────────────────────────────────────
def register_upload(filename: str, content: bytes) -> dict:
    if len(content) > MAX_UPLOAD_BYTES:
        raise AOIError("File exceeds the 20 MB limit.")
    lower = filename.lower()
    if lower.endswith(".zip"):
        feats, crs_note = _read_zipped_shapefile(content)
    elif lower.endswith((".geojson", ".json")):
        feats, crs_note = _read_geojson(content), "EPSG:4326 (GeoJSON)"
    else:
        raise AOIError("Unsupported file type. Upload a zipped shapefile (.zip) or GeoJSON.")
    if not feats:
        raise AOIError("No polygon or point geometry found in the file.")
    polys = [f["poly"] for f in feats if f.get("poly")]
    pts = [f["point"] for f in feats if f.get("point")]
    geometry_type = "polygon" if polys else "point"
    bounds = _bounds_of(polys) if polys else _bounds_of([[p] for p in pts])
    _validate_extent(bounds)
    upload_id = uuid.uuid4().hex[:12]
    rec = {
        "upload_id": upload_id,
        "label": f"Uploaded boundary — {filename}",
        "filename": filename,
        "bounds": bounds,
        "polys": polys,
        "features": feats,
        "geometry_type": geometry_type,
        "n_polygons": len(polys),
        "n_features": len(feats),
        "crs": crs_note,
        "area_ha": round(sum(polygon_area_ha(p) for p in polys)),
    }
    _UPLOADS[upload_id] = rec
    return rec


def get_upload(upload_id: str) -> dict:
    rec = _UPLOADS.get(upload_id or "")
    if not rec:
        raise AOIError("Upload not found — please upload the file again.")
    return rec


def upload_geojson(rec: dict) -> dict:
    """GeoJSON (lon/lat order) of an upload record for map display."""
    if rec.get("polys"):
        return {
            "type": "MultiPolygon",
            "coordinates": [[[[p[1], p[0]] for p in poly]] for poly in rec["polys"]],
        }
    return {
        "type": "MultiPoint",
        "coordinates": [[f["point"][1], f["point"][0]]
                        for f in rec.get("features", []) if f.get("point")],
    }


def _validate_extent(bounds: list):
    """Geometry must sit inside Ethiopia. Size is checked per journey, below."""
    [[s, w], [n, e]] = bounds
    if not (2.0 < s < 16.0 and 2.0 < n < 16.0 and 32.0 < w < 49.0 and 32.0 < e < 49.0):
        raise AOIError(
            "Boundary falls outside Ethiopia. Check that coordinates are in "
            "EPSG:4326 (longitude/latitude in degrees)."
        )


def _validate_aoi_size(bounds: list):
    """The 3°×3° cap belongs to the raster journey only.

    Gridding an extent costs O(area), so an unbounded AOI would take the whole
    country at 100 m. The per-feature scheme analysis costs O(features) and
    samples each plot in place, so a national scheme file is cheap even though
    its bounding box is large — the 2026 campaign file spans Afar to Oromia,
    about 3.5° of latitude, and rejecting it here would reject the reference
    notebook's own input.
    """
    [[s, w], [n, e]] = bounds
    if (n - s) > 3.0 or (e - w) > 3.0:
        raise AOIError("Boundary is too large — the analysis extent is limited to ~3°×3°.")


def _clean_attrs(raw: dict) -> dict:
    """Attribute values as JSON-friendly scalars, dates as ISO strings."""
    out = {}
    for k, v in raw.items():
        if v is None or isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, (dt.date, dt.datetime)):
            out[k] = v.isoformat()[:10]
        elif isinstance(v, bytes):
            out[k] = v.decode("utf-8", errors="replace").strip()
        else:
            out[k] = str(v)
        if isinstance(out[k], str):
            out[k] = out[k].strip()
    return out


def _read_geojson(content: bytes) -> list:
    """Features as {'attrs', 'poly'|'point'} in [lat, lon] order."""
    try:
        gj = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AOIError("File is not valid GeoJSON.")
    feats: list[dict] = []

    def collect(g, props=None):
        if not isinstance(g, dict):
            return
        t = g.get("type")
        if t == "FeatureCollection":
            for f in g.get("features", []):
                collect(f)
        elif t == "Feature":
            collect(g.get("geometry"), g.get("properties") or {})
        elif t == "Polygon":
            ring = (g.get("coordinates") or [None])[0]
            if ring and len(ring) >= 3:
                feats.append({"attrs": _clean_attrs(props or {}),
                              "poly": [[float(pt[1]), float(pt[0])] for pt in ring]})
        elif t == "MultiPolygon":
            for polygon in g.get("coordinates") or []:
                ring = polygon[0] if polygon else None
                if ring and len(ring) >= 3:
                    feats.append({"attrs": _clean_attrs(props or {}),
                                  "poly": [[float(pt[1]), float(pt[0])] for pt in ring]})
        elif t == "Point":
            c = g.get("coordinates") or []
            if len(c) >= 2:
                feats.append({"attrs": _clean_attrs(props or {}),
                              "point": [float(c[1]), float(c[0])]})
        elif t == "MultiPoint":
            for c in g.get("coordinates") or []:
                if len(c) >= 2:
                    feats.append({"attrs": _clean_attrs(props or {}),
                                  "point": [float(c[1]), float(c[0])]})

    collect(gj)
    return feats


_POLYGON_TYPES = (shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM)
_POINT_TYPES = (shapefile.POINT, shapefile.POINTZ, shapefile.POINTM,
                shapefile.MULTIPOINT, shapefile.MULTIPOINTZ, shapefile.MULTIPOINTM)


def _crs_note(prj: str | None) -> str:
    """CRS note for the text of a .prj, refusing anything but WGS 84 geographic.

    ArcGIS writes the campaign shapefiles as ``GCS_WGS_1984`` rather than
    naming the EPSG code, so the name is accepted alongside the code.
    """
    if prj is None:
        return "CRS unknown (.prj missing) — EPSG:4326 assumed"
    if "4326" in prj or "WGS_1984" in prj or "WGS 84" in prj:
        return "EPSG:4326 (WGS 84)"
    raise AOIError(
        "Shapefile is not in EPSG:4326. Reproject the boundary to WGS 84 "
        "geographic coordinates and upload again."
    )


def _read_shapefile(shp, dbf, shx, unreadable: str) -> list:
    """Features (attributes included) from open .shp/.dbf/.shx handles.

    Attributes are carried through because the reference notebook drives the
    estimate from them: each feature brings its own SOS/EOS growing season, and
    Name/Location/Scheme_ID are what results are grouped by afterwards.
    """
    try:
        reader = shapefile.Reader(shp=shp, dbf=dbf, shx=shx)
        feats = []
        for sr in reader.iterShapeRecords():
            shape = sr.shape
            attrs = _clean_attrs(sr.record.as_dict() if dbf else {})
            if shape.shapeType in _POLYGON_TYPES:
                parts = list(shape.parts) + [len(shape.points)]
                for k in range(len(parts) - 1):
                    ring = shape.points[parts[k]:parts[k + 1]]
                    if len(ring) >= 3:
                        feats.append({"attrs": attrs,
                                      "poly": [[float(pt[1]), float(pt[0])] for pt in ring]})
                        break  # first ring of each shape only
            elif shape.shapeType in _POINT_TYPES:
                for pt in shape.points[:1] or []:
                    feats.append({"attrs": attrs, "point": [float(pt[1]), float(pt[0])]})
    except AOIError:
        raise
    except Exception:
        raise AOIError(unreadable)
    return feats


def _read_zipped_shapefile(content: bytes) -> tuple:
    """Features and CRS note for a zipped shapefile — the upload path."""
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
    prj = zf.read(names["prj"]).decode("utf-8", errors="ignore") if "prj" in names else None
    crs_note = _crs_note(prj)
    feats = _read_shapefile(
        shp, dbf, shx,
        "Could not read the shapefile — the archive may be corrupt.",
    )
    return feats, crs_note


def read_shapefile_on_disk(base: Path | str) -> tuple:
    """Features and CRS note for a shapefile on the service's own filesystem.

    ``base`` is the path without an extension. Used for datasets that are held
    beside the service rather than uploaded — see ``campaign.py`` — so a local
    shapefile and an uploaded one are read by exactly the same code, and any
    difference in how they parse would show up in both.
    """
    base = Path(base)
    shp = base.with_suffix(".shp")
    if not shp.exists():
        raise AOIError(f"No shapefile at {shp}.")
    prj = base.with_suffix(".prj")
    crs_note = _crs_note(prj.read_text(errors="ignore") if prj.exists() else None)

    def handle(ext):
        path = base.with_suffix(ext)
        return io.BytesIO(path.read_bytes()) if path.exists() else None

    feats = _read_shapefile(
        io.BytesIO(shp.read_bytes()), handle(".dbf"), handle(".shx"),
        f"Could not read {shp.name} — the file may be corrupt.",
    )
    return feats, crs_note


def features_geojson(feats: list) -> dict:
    """Features as a GeoJSON FeatureCollection, attributes included.

    ``upload_geojson`` above drops attributes because the map only draws
    geometry. A dataset handed to the client to be uploaded back has to keep
    them: they are the growing seasons the estimate runs on.
    """
    out = []
    for f in feats:
        if f.get("poly"):
            geom = {"type": "Polygon",
                    "coordinates": [[[p[1], p[0]] for p in f["poly"]]]}
        elif f.get("point"):
            geom = {"type": "Point", "coordinates": [f["point"][1], f["point"][0]]}
        else:
            continue
        out.append({"type": "Feature", "properties": dict(f["attrs"]), "geometry": geom})
    return {"type": "FeatureCollection", "features": out}
