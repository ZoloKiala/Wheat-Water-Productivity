"""Caching and idempotency for the estimate endpoint (ToR WP3).

A WaPOR retrieval reads 16 decadal COGs and takes minutes, so the same request
must not pay that twice. Two separate mechanisms, deliberately:

**Result cache** — keyed by what determines the answer: geometry, season, and
the decade mode. Ask for the same field over the same season and the stored
result comes back immediately. Rounding the geometry to 6 decimal places (about
0.1 m) keeps a redrawn-but-identical polygon on the same key.

**Idempotency keys** — keyed by the caller's own `Idempotency-Key` header. A
retried POST returns the first response rather than starting a second
retrieval. This is about the *request*, not the answer: two different callers
asking the same question share the result cache, but never each other's
idempotency entry.

Both persist as JSON under ``server/.cache`` so a restart does not throw the
work away, and both are safe to delete at any time.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

CACHE_DIR = Path(os.environ.get("WWP_CACHE_DIR", Path(__file__).resolve().parent / ".cache"))
RESULTS = CACHE_DIR / "results"
IDEMPOTENCY = CACHE_DIR / "idempotency"
TTL_SECONDS = int(os.environ.get("WWP_CACHE_TTL", 60 * 60 * 24 * 30))  # 30 days

_lock = threading.Lock()


def _ensure() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    IDEMPOTENCY.mkdir(parents=True, exist_ok=True)


def result_key(
    kind: str,
    geometry: Any,
    sos: str,
    eos: str,
    scheme_code: Optional[str],
    mirror: bool,
    mapsets: tuple[str, ...] = ("L2-NPP-D", "L2-AETI-D"),
) -> str:
    """A stable key over everything that changes the answer, and nothing else.

    Feature ID and name are excluded on purpose: renaming a plot must not
    invalidate a retrieval that would return the same numbers.
    """
    def rounded(g: Any) -> Any:
        if isinstance(g, (list, tuple)):
            return [rounded(x) for x in g]
        if isinstance(g, float):
            return round(g, 6)
        return g

    payload = {
        "kind": kind,
        "geometry": rounded(geometry),
        "sos": sos,
        "eos": eos,
        "scheme_code": scheme_code,
        "mirror": bool(mirror),
        "mapsets": list(mapsets),
        "v": 1,                      # bump when the chain itself changes
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _read(path: Path) -> Optional[dict]:
    try:
        with path.open(encoding="utf-8") as fh:
            entry = json.load(fh)
    except (OSError, ValueError):
        return None
    if TTL_SECONDS and time.time() - entry.get("stored_at", 0) > TTL_SECONDS:
        return None
    return entry


def _write(path: Path, value: dict) -> None:
    _ensure()
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump({"stored_at": time.time(), "value": value}, fh)
    tmp.replace(path)              # atomic, so a crash cannot leave a half file


def get_result(key: str) -> Optional[dict]:
    with _lock:
        entry = _read(RESULTS / f"{key}.json")
    return entry["value"] if entry else None


def put_result(key: str, value: dict) -> None:
    with _lock:
        _write(RESULTS / f"{key}.json", value)


def get_idempotent(key: str) -> Optional[dict]:
    safe = hashlib.sha256(key.encode()).hexdigest()
    with _lock:
        entry = _read(IDEMPOTENCY / f"{safe}.json")
    return entry["value"] if entry else None


def put_idempotent(key: str, value: dict) -> None:
    safe = hashlib.sha256(key.encode()).hexdigest()
    with _lock:
        _write(IDEMPOTENCY / f"{safe}.json", value)


def stats() -> dict[str, Any]:
    _ensure()
    return {
        "dir": str(CACHE_DIR),
        "results": len(list(RESULTS.glob("*.json"))),
        "idempotency_keys": len(list(IDEMPOTENCY.glob("*.json"))),
        "ttl_seconds": TTL_SECONDS,
    }


def clear() -> int:
    _ensure()
    n = 0
    with _lock:
        for d in (RESULTS, IDEMPOTENCY):
            for f in d.glob("*.json"):
                f.unlink()
                n += 1
    return n
