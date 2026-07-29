"""FastAPI application entry point.

Serves the REST API under /api and, when the frontend has been built
(frontend/dist), the dashboard SPA at the site root — one process runs the
whole application: ``uvicorn app.main:app`` from the backend directory.
"""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .model_service import MODEL

app = FastAPI(
    title="Wheat Water Productivity (WWP) API",
    description=(
        "Backend service for the EIAR Wheat Water Productivity Dashboard: "
        "WWPT analytical engine, WaPOR data access and LightGBM prediction "
        "service with per-prediction explanations. Developed by IWMI East "
        "Africa with EIAR under WaPOR Phase II."
    ),
    version="1.0.0",
)

# The dashboard is served from this same origin in a deployment, so CORS only
# matters for the Vite dev server and for embedding the API cross-origin.
# WWP_CORS_ORIGINS is a comma-separated allowlist.
_origins = os.environ.get("WWP_CORS_ORIGINS")
app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        [o.strip() for o in _origins.split(",") if o.strip()]
        if _origins
        else ["http://localhost:5173", "http://127.0.0.1:5173"]
    ),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


# Unknown API paths must answer in JSON. Without this catch-all they would
# fall through to the SPA mount below and come back as the HTML 404 page.
@app.api_route(
    "/api/{_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    include_in_schema=False,
)
async def _api_not_found(_path: str):
    raise HTTPException(status_code=404, detail="Not Found")


@app.on_event("startup")
def _load_model():
    MODEL.load_or_train()


DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if DIST.exists():
    # html=True also serves dist/404.html (from frontend/public) with a 404
    # status for paths that match no file, so browsers get a branded page.
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="dashboard")


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception):
    """Browser navigations get the branded 500 page; API calls stay JSON."""
    page = DIST / "500.html"
    if not request.url.path.startswith("/api") and page.exists():
        return FileResponse(page, status_code=500)
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
