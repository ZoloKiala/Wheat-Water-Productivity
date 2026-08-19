"""Place search, so an area of interest can be found by name (ToR WP2b).

Proxied through this server rather than called from the browser for three
reasons: Nominatim's usage policy requires an identifying User-Agent, which a
browser cannot set; the policy caps requests at one per second, which is easier
to honour in one place; and results are worth caching, since the same few scheme
names get searched repeatedly.

Nothing here is required for the analysis — it only moves the map.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional

from . import cache

NOMINATIM = os.environ.get("WWP_GEOCODER", "https://nominatim.openstreetmap.org/search")
# Identify the application, as the Nominatim usage policy requires.
USER_AGENT = os.environ.get(
    "WWP_GEOCODER_UA",
    "WWP-Dashboard/1.0 (IWMI East Africa / EIAR; wheat water productivity tool)",
)
DEFAULT_COUNTRY = os.environ.get("WWP_GEOCODER_COUNTRY", "et")

_throttle_lock = threading.Lock()
_last_call = 0.0


def _throttle(min_interval: float = 1.05) -> None:
    """One request per second, as the policy requires."""
    global _last_call
    with _throttle_lock:
        wait = min_interval - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def parse_coordinates(q: str) -> Optional[dict[str, Any]]:
    """Accept a pasted coordinate pair before trying the network.

    "9.3443, 40.1712" or "40.1712 9.3443" — latitude first is the convention
    people type, so that ordering wins when both readings are plausible.
    """
    parts = [p for p in q.replace(",", " ").split() if p]
    if len(parts) != 2:
        return None
    try:
        a, b = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    for lat, lon in ((a, b), (b, a)):
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return {
                "name": f"{lat:.5f}, {lon:.5f}",
                "kind": "coordinates",
                "lat": lat,
                "lon": lon,
                "bbox": None,
            }
    return None


def search(q: str, limit: int = 6, country: Optional[str] = DEFAULT_COUNTRY) -> dict[str, Any]:
    """Look a place up. Returns {query, source, results:[...]}"""
    q = (q or "").strip()
    if not q:
        return {"query": q, "source": "none", "results": []}

    coords = parse_coordinates(q)
    if coords:
        return {"query": q, "source": "coordinates", "results": [coords]}

    key = cache.result_key("geocode", [q.lower(), limit, country or ""], "", "", None, False, ())
    hit = cache.get_result(key)
    if hit is not None:
        return dict(hit, source="cache")

    import requests

    params = {"q": q, "format": "jsonv2", "limit": max(1, min(limit, 20)), "addressdetails": 0}
    if country:
        params["countrycodes"] = country
    _throttle()
    r = requests.get(NOMINATIM, params=params, timeout=20,
                     headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    r.raise_for_status()
    out = []
    for row in r.json():
        bb = row.get("boundingbox")
        out.append({
            "name": row.get("display_name"),
            "kind": row.get("type") or row.get("category"),
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
            # Nominatim gives [south, north, west, east]; hand back the order a
            # map wants so the client does no reshuffling.
            "bbox": [float(bb[0]), float(bb[2]), float(bb[1]), float(bb[3])] if bb and len(bb) == 4 else None,
        })
    payload = {"query": q, "source": "nominatim", "results": out}
    cache.put_result(key, payload)
    return payload
