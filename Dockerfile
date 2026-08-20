# ── Stage 1: build the dashboard bundle ───────────────────────────────────
FROM node:20-slim AS frontend

WORKDIR /build
# Copy manifests first so `npm ci` is cached until dependencies actually change.
COPY frontend/package.json frontend/package-lock.json ./
# esbuild needs its postinstall to fetch the platform binary Vite invokes.
RUN npm ci --no-audit --no-fund --foreground-scripts

COPY frontend/ ./
RUN npm run build


# ── Stage 2: Python runtime ───────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# curl serves the platform health check. The estimation itself is pure numpy,
# so no native maths runtime is needed. Enabling the WaPOR provider additionally
# requires rasterio/GDAL — see backend/requirements.txt.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY --from=frontend /build/dist ./frontend/dist

WORKDIR /app/backend

# Fail the build rather than ship an image whose estimates disagree with the
# reference notebook. Costs a second and catches a bad crop_params.json or an
# accidental change to the estimation chain before it can reach a deployment.
COPY tests/test_notebook_parity.py ./parity_check.py
RUN python parity_check.py && rm parity_check.py

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 wwp && chown -R wwp:wwp /app
USER wwp

EXPOSE 8000
# Railway (and most PaaS) inject $PORT; shell form so it expands, with a local
# default for `docker run` without one.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
