"""In-process TTL cache for repeated analysis queries plus idempotency-key
storage for retriable POSTs (ToR WP3). Swap for Redis in a multi-worker
deployment — the interface is deliberately minimal."""

from __future__ import annotations

import hashlib
import json
import threading
import time


class TTLCache:
    def __init__(self, ttl_seconds: int = 1800, max_items: int = 256):
        self.ttl = ttl_seconds
        self.max_items = max_items
        self._data: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key_for(payload: dict) -> str:
        canon = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def get(self, key: str):
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires, value = item
            if time.time() > expires:
                del self._data[key]
                return None
            return value

    def put(self, key: str, value) -> None:
        with self._lock:
            if len(self._data) >= self.max_items:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]
            self._data[key] = (time.time() + self.ttl, value)


ANALYSIS_CACHE = TTLCache(ttl_seconds=1800)
IDEMPOTENCY = TTLCache(ttl_seconds=3600)
