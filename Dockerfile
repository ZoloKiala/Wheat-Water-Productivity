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

# libgomp1 is LightGBM's OpenMP runtime; the slim image omits it and inference
# fails at import time without it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY --from=frontend /build/dist ./frontend/dist

# Train the model during the build so the image ships ready to serve. Training
# at startup instead would make the first request wait ~40s and risk tripping
# the platform health check before the service ever reports healthy.
WORKDIR /app/backend
RUN python train_model.py

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 wwp && chown -R wwp:wwp /app
USER wwp

EXPOSE 8000
# Railway (and most PaaS) inject $PORT; shell form so it expands, with a local
# default for `docker run` without one.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
