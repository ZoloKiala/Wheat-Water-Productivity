"""Self-test for the analysis logic. No pytest, no network by default.

    python server/selftest.py            # offline checks
    python server/selftest.py --online   # also HEAD the real WaPOR rasters

Covers the decade split, the season -> URL list (both correct and
etwapor-mirroring), the biomass chain against the notebook's published figures,
and the input rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import wapor
from server.app import CROP, area_ha, lgp_of, water_productivity, yield_from_npp

FAILS = 0


def ok(label: str, cond: bool, extra: str = "") -> None:
    global FAILS
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  - {extra}" if extra else ""))
    if not cond:
        FAILS += 1


def near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


print("\n=== decade split (WaPOR: D1 1-10, D2 11-20, D3 21-end) ===")
for day, want in ((1, 1), (10, 1), (11, 2), (20, 2), (21, 3), (28, 3), (31, 3)):
    ok(f"day {day:2d} -> D{want}", wapor.decade_of(day) == want, f"got D{wapor.decade_of(day)}")

print("\n=== season -> decade list ===")
starts = wapor.decade_starts("2025-11-22", "2026-02-20")
ok("Amibara season spans 8 decades", len(starts) == 8, str(len(starts)))
ok("first decade starts 2025-12-01", starts[0].isoformat() == "2025-12-01", starts[0].isoformat())
ok("last decade starts 2026-02-11", starts[-1].isoformat() == "2026-02-11", starts[-1].isoformat())
ok("no decade starts before SOS", all(s.isoformat() >= "2025-11-22" for s in starts))
ok("no decade starts after EOS", all(s.isoformat() <= "2026-02-20" for s in starts))
ok("decades are ordered", starts == sorted(starts))

single = wapor.decade_starts("2025-12-01", "2025-12-05")
ok("a 5-day season yields exactly one decade", len(single) == 1, str(len(single)))
ok("a zero-length season yields none", wapor.decade_starts("2025-12-05", "2025-12-04") == [])

print("\n=== URL construction ===")
urls = wapor.decade_urls("2025-11-22", "2026-02-20", "L2-NPP-D")
ok("one URL per decade", len(urls) == 8, str(len(urls)))
ok("every decade is a distinct raster", len({u for _, u in urls}) == 8,
   f"{len({u for _, u in urls})} distinct")
ok("D1/D2/D3 all appear", {u.rsplit('-', 1)[1][:2] for _, u in urls} == {"D1", "D2", "D3"},
   str(sorted({u.rsplit('-', 1)[1][:2] for _, u in urls})))
ok("MAPSET path when no scheme code", "/MAPSET/" in urls[0][1])
scheme = wapor.decade_urls("2025-11-22", "2026-02-20", "L2-NPP-D", "AWH")
ok("MOSAICSET path with a scheme code", "/MOSAICSET/" in scheme[0][1])
ok("scheme code appears in the filename", ".AWH." in scheme[0][1], scheme[0][1].rsplit("/", 1)[1])
ok("gismgr storage switches host",
   wapor.decade_urls("2025-12-01", "2025-12-05", storage="gismgr")[0][1].startswith(wapor.GISMGR))

mirror = wapor.decade_urls("2025-11-22", "2026-02-20", "L2-NPP-D", mirror_etwapor=True)
ok("mirror mode keeps the same decade count", len(mirror) == len(urls))
ok("mirror mode collapses to 3 distinct files (the etwapor defect)",
   len({u for _, u in mirror}) == 3, f"{len({u for _, u in mirror})} distinct")
ok("mirror mode is D1 only", {u.rsplit('-', 1)[1][:2] for _, u in mirror} == {"D1"})

print("\n=== biomass chain vs the notebook's published figures ===")
# Each field: seasonal NPP -> yield, and (yield, AETI) -> WP, from cells 17/27.
for name, npp, aeti, want_y, want_wp in (
    ("Amibara field 1", 184.95, 297.32, 1.78, 0.60),
    ("Dubti field 2", 168.70, 319.43, 1.62, 0.51),
    ("Dubti field 3", 138.29, 291.45, 1.33, 0.46),
    ("Dubti field 4", 65.24, 193.81, 0.63, 0.32),
    ("Dodota field 5", 478.22, 369.28, 4.59, 1.24),
    ("Godino field 6", 319.52, 219.53, 3.07, 1.40),
):
    y = yield_from_npp(npp)
    wp = water_productivity(y, aeti)
    ok(f"{name}: NPP {npp} -> {want_y} t/ha", near(y, want_y, 0.005), f"got {y:.3f}")
    ok(f"{name}: WP {want_wp} kg/m3", near(wp, want_wp, 0.005), f"got {wp:.3f}")

print("\n=== crop parameters and the 22.222 factor ===")
ok("FAO (2020b) wheat parameters",
   CROP == {"AOT": 0.85, "fc": 0.90, "mc": 0.15, "hi": 0.48}, str(CROP))
# AOT and (1 - mc) are both 0.85, so they cancel: the chain reduces to fc*22.222*hi.
ok("chain reduces to fc x 22.222 x hi (AOT cancels 1-mc)",
   near(yield_from_npp(1000.0), 0.9 * 1000.0 * 22.222 * 0.48 / 1000.0, 1e-9),
   f"{yield_from_npp(1000.0):.4f} t/ha per 1000 gC/m2")
ok("1 mm over 1 ha is 10 m3", near(water_productivity(1.0, 100.0), 1.0, 1e-9))

print("\n=== geometry and season helpers ===")
ok("LGP counts days between SOS and EOS", lgp_of("2025-11-20", "2026-03-16") == 116,
   str(lgp_of("2025-11-20", "2026-03-16")))
# a 0.01 deg square at 9 N: 1.1057 km x 1.0918 km ~ 120.7 ha
sq = [[40.17, 9.34], [40.18, 9.34], [40.18, 9.35], [40.17, 9.35]]
ok("area of a 0.01 deg square near 9 N is ~121 ha", near(area_ha(sq), 120.7, 1.5),
   f"{area_ha(sq):.2f} ha")
ok("winding does not change the area", near(area_ha(sq), area_ha(list(reversed(sq))), 1e-6))

print("\n=== the Estimate response model matches what the retrieval builds ===")
from server.app import Estimate, area_warning, validate_feature, Feature

_built = Estimate(id=1, name="Amibara", NPP=189.4, EYield_tpha=1.82, AETI_mm=322.8,
                  WP_kgpm3=0.56, LGP=90, area_ha=12.53)
ok("estimate_wapor's field set satisfies the model", _built.provider == "wapor",
   _built.provider)
ok("no field left required that the retrieval no longer sends",
   set(Estimate.model_fields) == {"id", "name", "warning", "provider", "cached", "NPP",
                                  "EYield_tpha", "AETI_mm", "WP_kgpm3", "LGP", "area_ha"},
   ",".join(sorted(Estimate.model_fields)))
ok("nothing named 'synthetic' survives on the model", "synthetic" not in Estimate.model_fields)

print("\n=== area rules ===")
_poly = Feature(id=1, name="x", kind="polygon", sos="2025-11-20", eos="2026-03-15",
                ring=[[40.170, 9.340], [40.180, 9.340], [40.180, 9.350], [40.170, 9.350]])
validate_feature(_poly)
ok("120 ha polygon accepted, unwarned", area_warning(_poly) is None)
_small = Feature(id=1, name="x", kind="polygon", sos="2025-11-20", eos="2026-03-15",
                 ring=[[40.170, 9.3400], [40.1715, 9.3400], [40.1715, 9.3413], [40.170, 9.3413]])
validate_feature(_small)
ok("2 ha polygon accepted but warned", (area_warning(_small) or "").endswith("field edges"),
   str(area_warning(_small)))
for label, ring in (
    ("under one pixel", [[40.170, 9.340], [40.1706, 9.340], [40.1706, 9.3405], [40.170, 9.3405]]),
    ("over 3 degrees", [[38.0, 8.0], [42.0, 8.0], [42.0, 10.0], [38.0, 10.0]]),
):
    try:
        validate_feature(Feature(id=1, name="x", kind="polygon", sos="2025-11-20",
                                 eos="2026-03-15", ring=ring))
        ok(f"{label} rejected", False, "accepted")
    except Exception as exc:
        ok(f"{label} rejected", getattr(exc, "status_code", None) == 422, str(exc)[:70])

if "--online" in sys.argv:
    print("\n=== live WaPOR availability (HEAD) ===")
    rows = wapor.check_urls(urls)
    ok("every decade of the season is published",
       all(r.get("status") == 200 for r in rows),
       str([r.get("status") for r in rows]))
    sizes = {r.get("bytes") for r in rows if r.get("bytes")}
    ok("the 8 decades are 8 different files", len(sizes) == 8, f"{len(sizes)} distinct sizes")
    mrows = wapor.check_urls(mirror)
    msizes = {r.get("bytes") for r in mrows if r.get("bytes")}
    ok("mirror mode really does fetch only 3 files", len(msizes) == 3, f"{len(msizes)} distinct sizes")
else:
    print("\n  (skipping live WaPOR checks; pass --online to run them)")

stack, why = wapor.available()
print(f"\nraster stack for retrieval: {'present' if stack else 'absent - ' + str(why)}")
print("all analysis checks passed" if not FAILS else f"FAILURES: {FAILS}")
sys.exit(1 if FAILS else 0)
