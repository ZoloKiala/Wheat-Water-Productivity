"""REST API for the WWP dashboard."""

from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import aoi as aoi_mod
from . import engine
from .admin_units import as_tree
from .cache import ANALYSIS_CACHE, IDEMPOTENCY, TTLCache
from .geodata import SEASON_IRRIGATED, SEASONS_RAINFED, YEARS
from .model_service import MODEL

router = APIRouter()


class AnalysisRequest(BaseModel):
    aoi_type: str = Field(pattern="^(admin|polygon|upload)$")
    region: Optional[str] = None
    zone: Optional[str] = None
    woreda: Optional[str] = None
    upload_id: Optional[str] = None
    polygon: Optional[list] = None
    system: str = Field(pattern="^(rainfed|irrigated)$")
    year: str
    season: str


def _validate_season(req: AnalysisRequest):
    if req.year not in YEARS:
        raise HTTPException(422, f"Unknown year '{req.year}'.")
    valid = [SEASON_IRRIGATED] if req.system == "irrigated" else SEASONS_RAINFED
    if req.season not in valid:
        raise HTTPException(
            422, f"Season '{req.season}' is not valid for the {req.system} system."
        )


@router.get("/health")
def health():
    return {"status": "ok", "model_version": MODEL.meta.get("version")}


@router.get("/admin-units")
def admin_units():
    return {"tree": as_tree(), "years": YEARS,
            "seasons": {"rainfed": SEASONS_RAINFED, "irrigated": [SEASON_IRRIGATED]}}


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    try:
        rec = aoi_mod.register_upload(file.filename or "boundary", content)
    except aoi_mod.AOIError as e:
        raise HTTPException(422, str(e))
    return {
        "upload_id": rec["upload_id"],
        "label": rec["label"],
        "bounds": rec["bounds"],
        "n_polygons": rec["n_polygons"],
        "crs": rec["crs"],
        "area_ha": rec["area_ha"],
        "geojson": aoi_mod.upload_geojson(rec),
    }


@router.post("/analysis")
def analysis(req: AnalysisRequest, idempotency_key: Optional[str] = Header(default=None)):
    _validate_season(req)
    if idempotency_key:
        prior = IDEMPOTENCY.get(idempotency_key)
        if prior is not None:
            return prior
    payload = req.model_dump()
    cache_key = TTLCache.key_for(payload)
    cached = ANALYSIS_CACHE.get(cache_key)
    if cached is not None:
        result = {**cached, "cached": True}
    else:
        try:
            resolved = aoi_mod.resolve(payload)
            result = engine.run_analysis(resolved, req.system, req.year, req.season)
        except aoi_mod.AOIError as e:
            raise HTTPException(422, str(e))
        ANALYSIS_CACHE.put(cache_key, result)
    if idempotency_key:
        IDEMPOTENCY.put(idempotency_key, result)
    return result


@router.get("/predict")
def predict(
    lat: float = Query(ge=2, le=16), lon: float = Query(ge=32, le=49),
    system: str = "rainfed", year: str = "2024/25", season: str = "Meher",
):
    return engine.predict_point(lat, lon, system, year, season)


@router.get("/explain")
def explain(
    lat: float = Query(ge=2, le=16), lon: float = Query(ge=32, le=49),
    system: str = "rainfed", year: str = "2024/25", season: str = "Meher",
):
    return engine.explain_point(lat, lon, system, year, season)


@router.get("/export/csv")
def export_csv(run_id: str):
    try:
        csv = engine.export_csv(run_id)
    except aoi_mod.AOIError as e:
        raise HTTPException(404, str(e))
    return PlainTextResponse(
        csv, media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="wwp_results.csv"'},
    )


@router.get("/model/info")
def model_info():
    return MODEL.info()


def _require_admin(token: Optional[str]) -> None:
    """Guard the model-management routes.

    A successful retrain replaces the model the service is answering with, so
    this must never be open to the internet. The route is disabled unless
    WWP_ADMIN_TOKEN is configured, which means a deployment that forgets to set
    it fails closed rather than exposing model replacement.
    """
    expected = os.environ.get("WWP_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(
            503,
            "Model management is disabled. Set WWP_ADMIN_TOKEN on the service to "
            "enable it.",
        )
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(401, "A valid X-Admin-Token header is required.")


@router.post("/model/retrain")
async def model_retrain(
    file: UploadFile = File(...),
    force: bool = False,
    x_admin_token: Optional[str] = Header(default=None),
):
    _require_admin(x_admin_token)
    content = await file.read()
    try:
        return MODEL.retrain_from_csv(content, force=force)
    except ValueError as e:
        raise HTTPException(422, str(e))
