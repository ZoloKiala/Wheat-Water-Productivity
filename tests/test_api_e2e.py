"""End-to-end check against the running uvicorn server."""
import io
import json
import os
import time
import urllib.error
import urllib.request
import zipfile

BASE = os.environ.get("WWP_BASE", "http://127.0.0.1:8000")
ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


def req(path, data=None, headers=None, method=None, raw=False):
    url = BASE + path
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except json.JSONDecodeError:
            return e.code, payload


def multipart(path, filename, content, field="file", headers=None):
    boundary = "----wwpBoundary1234"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
        f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    r = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 **(headers or {})},
    )
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ── wait for boot ───────────────────────────────────────────────
print("Waiting for server…")
for _ in range(120):
    try:
        s, _ = req("/api/health")
        if s == 200:
            break
    except Exception:
        pass
    time.sleep(1)

print("\n1. Health & reference data")
s, h = req("/api/health")
check("health 200", s == 200 and h["status"] == "ok", f"provider {h.get('provider')}")
s, u = req("/api/admin-units")
check("admin-units tree", s == 200 and "Oromia" in u["tree"] and u["tree"]["Oromia"]["Arsi"])
check("seasons per system",
      u["seasons"]["rainfed"] == ["Meher", "Belg"] and len(u["seasons"]["irrigated"]) == 1)

print("\n2. Method description")
s, mi = req("/api/method")
check("method 200", s == 200 and "WaPOR" in mi["method"], mi["method"])
check("equations published", len(mi["equations"]) == 3, mi["equations"][0])
cp = mi["crop_parameters"]
check("crop parameters complete",
      all(k in cp for k in ("aot", "fc", "mc", "hi", "constant")), str(cp))
# The only combination the outputs depend on. The reference notebook's published
# results imply 0.4322; drifting off it means the tool no longer agrees with it.
check("crop constant matches the notebook", abs(cp["constant"] - 0.4322) < 0.002,
      f"constant={cp['constant']}")
check("data source declared", "synthetic" in mi and bool(mi["provider"]),
      f"{mi['provider']} (synthetic={mi['synthetic']})")

print("\n3. Admin-unit analysis")
body = {"aoi_type": "admin", "region": "Oromia", "zone": "Arsi", "woreda": "Hetosa",
        "system": "rainfed", "year": "2024/25", "season": "Meher"}
t0 = time.time()
s, r = req("/api/analysis", body, {"idempotency-key": "e2e-key-1"})
dt = time.time() - t0
check("analysis 200", s == 200, f"{dt:.1f}s")
check("mean in range", 0.2 < r["stats"]["mean"] < 2.2, f"mean={r['stats']['mean']}")
check("percentiles ordered", r["stats"]["p10"] <= r["stats"]["mean"] <= r["stats"]["p90"])
check("raster png data-uri", r["raster_png"].startswith("data:image/png;base64,iVBOR"),
      f"{len(r['raster_png'])} chars")
check("histogram sums ~100", abs(sum(h["pct"] for h in r["histogram"]) - 100) < 1.5,
      f"{sum(h['pct'] for h in r['histogram']):.1f}%")
check("trend has 5 seasons", len(r["trend"]) == 5)
check("estimation chain present", len(r["chain"]) == 5,
      " -> ".join(c["step"] for c in r["chain"]))
check("chain ends on water productivity",
      r["chain"][-1]["role"] == "result" and r["chain"][-1]["unit"] == "kg/m³")
check("season window resolved",
      r["season_window"]["sos"] < r["season_window"]["eos"]
      and r["season_window"]["lgp_days"] > 0,
      f"{r['season_window']['sos']} to {r['season_window']['eos']} "
      f"({r['season_window']['lgp_days']} d)")
check("data source flagged on the result", "synthetic" in r, str(r.get("provider")))
check("area > 0", r["area_ha"] > 0, f"{r['area_ha']:,} ha")
run_id = r["run_id"]

print("\n4. Caching & idempotency")
t0 = time.time()
s, r2 = req("/api/analysis", body, {"idempotency-key": "e2e-key-1"})
check("idempotent replay same run_id", r2["run_id"] == run_id, f"{time.time()-t0:.2f}s")
t0 = time.time()
s, r3 = req("/api/analysis", body, {"idempotency-key": "e2e-key-DIFFERENT"})
check("cache hit flagged", r3.get("cached") is True, f"{time.time()-t0:.2f}s")
check("cache returns same values", r3["stats"]["mean"] == r["stats"]["mean"])

print("\n5. Point prediction & explanation")
s, p = req("/api/predict?lat=8.13&lon=39.24&system=rainfed&year=2024/25&season=Meher")
check("predict 200", s == 200 and 0.2 < p["wwp"] < 2.2, f"wwp={p['wwp']}")
s, e = req("/api/explain?lat=8.13&lon=39.24&system=rainfed&year=2024/25&season=Meher")
check("explain 200", s == 200 and len(e["chain"]) == 5)
check("explain matches predict", abs(e["wwp"] - p["wwp"]) < 1e-6)
check("chain steps carry value+unit",
      all(("value" in c and "unit" in c and "detail" in c) for c in e["chain"]))
# The point of a deterministic method: the chain shown to the user must actually
# reproduce the answer, by hand, from the numbers on screen.
step = {c["step"]: c["value"] for c in e["chain"]}
check("chain arithmetic reconstructs the value",
      abs(step["Grain yield"] / step["Water consumed"] - e["wwp"]) < 0.01,
      f"{step['Grain yield']}/{step['Water consumed']} vs {e['wwp']}")
check("biomass to yield uses the harvest index",
      abs(step["Total biomass"] * e["method"]["crop_parameters"]["hi"]
          - step["Grain yield"]) < 1.0,
      f"TB={step['Total biomass']} -> Y={step['Grain yield']}")

print("\n6. CSV export")
s, csv = req(f"/api/export/csv?run_id={run_id}", raw=True)
lines = csv.decode().strip().splitlines()
check("csv 200 with rows", s == 200 and len(lines) > 100, f"{len(lines)-1} data rows")
# Column names match the reference notebook so exports are directly comparable.
check("csv header",
      lines[0] == "lat,lon,SOS,EOS,LGP,NPP,AETI_mm,EYield_tpha,WP_kgpm3", lines[0])
check("csv row parses", len(lines[1].split(",")) == 9, lines[1])
s, bad = req("/api/export/csv?run_id=doesnotexist")
check("csv unknown run 404", s == 404)

print("\n7. GeoJSON upload → analysis")
gj = json.dumps({"type": "Feature", "properties": {}, "geometry": {"type": "Polygon",
      "coordinates": [[[39.20, 8.08], [39.32, 8.08], [39.32, 8.20], [39.20, 8.20], [39.20, 8.08]]]}})
s, up = multipart("/api/upload", "scheme.geojson", gj.encode())
check("geojson upload 200", s == 200 and up["n_polygons"] == 1, f"{up.get('area_ha'):,} ha")
check("upload returns geojson for map", up["geojson"]["type"] == "MultiPolygon")
s, ru = req("/api/analysis", {"aoi_type": "upload", "upload_id": up["upload_id"],
                              "system": "irrigated", "year": "2023/24",
                              "season": "Dry season (Nov–Mar)"})
check("upload analysis 200", s == 200 and ru["stats"]["n_cells"] > 0,
      f"mean={ru['stats']['mean']}, {ru['stats']['n_cells']} cells")

print("\n8. Zipped shapefile upload")
shp_buf, shx_buf, dbf_buf = io.BytesIO(), io.BytesIO(), io.BytesIO()
import shapefile as pyshp
w = pyshp.Writer(shp=shp_buf, shx=shx_buf, dbf=dbf_buf)
w.field("name", "C")
w.poly([[[39.85, 7.05], [40.00, 7.05], [40.00, 7.18], [39.85, 7.18], [39.85, 7.05]]])
w.record("Sinana field")
w.close()
zbuf = io.BytesIO()
with zipfile.ZipFile(zbuf, "w") as z:
    z.writestr("field.shp", shp_buf.getvalue())
    z.writestr("field.shx", shx_buf.getvalue())
    z.writestr("field.dbf", dbf_buf.getvalue())
    z.writestr("field.prj", 'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984"]]')
s, zu = multipart("/api/upload", "field.zip", zbuf.getvalue())
check("shapefile upload 200", s == 200 and zu["n_polygons"] >= 1,
      f"crs={zu.get('crs')}, {zu.get('area_ha'):,} ha")
s, rz = req("/api/analysis", {"aoi_type": "upload", "upload_id": zu["upload_id"],
                              "system": "rainfed", "year": "2024/25", "season": "Meher"})
check("shapefile analysis 200", s == 200 and rz["stats"]["n_cells"] > 0,
      f"mean={rz['stats']['mean']}")

print("\n9. Drawn polygon analysis")
s, rp = req("/api/analysis", {"aoi_type": "polygon",
            "polygon": [[8.10, 39.20], [8.20, 39.22], [8.18, 39.34], [8.06, 39.30]],
            "system": "rainfed", "year": "2022/23", "season": "Belg"})
check("polygon analysis 200", s == 200 and rp["stats"]["n_cells"] > 0,
      f"mean={rp['stats']['mean']}, {rp['stats']['n_cells']} cells")
check("polygon masks cells out", rp["stats"]["n_cells"] < 170 * 170 * 0.82,
      f"{rp['stats']['n_cells']} < {int(170*170*0.82)}")

print("\n10. Validation & error handling")
s, _ = req("/api/analysis", {"aoi_type": "admin", "region": "Nowhere", "zone": "X",
                             "woreda": "Y", "system": "rainfed", "year": "2024/25",
                             "season": "Meher"})
check("unknown admin unit 422", s == 422)
s, m = req("/api/analysis", {"aoi_type": "admin", "region": "Oromia", "zone": "Arsi",
                             "woreda": "Hetosa", "system": "irrigated", "year": "2024/25",
                             "season": "Meher"})
check("season/system mismatch 422", s == 422, str(m.get("detail"))[:70])
s, m = req("/api/analysis", {"aoi_type": "admin", "region": "Oromia", "zone": "Arsi",
                             "woreda": "Hetosa", "system": "rainfed", "year": "1999/00",
                             "season": "Meher"})
check("unknown year 422", s == 422)
s, m = req("/api/analysis", {"aoi_type": "polygon", "polygon": [[8.1, 39.2], [8.2, 39.2]],
                             "system": "rainfed", "year": "2024/25", "season": "Meher"})
check("degenerate polygon 422", s == 422, str(m.get("detail"))[:60])
s, m = req("/api/analysis", {"aoi_type": "upload", "upload_id": "nope",
                             "system": "rainfed", "year": "2024/25", "season": "Meher"})
check("unknown upload 422", s == 422, str(m.get("detail"))[:60])
s, m = multipart("/api/upload", "notes.txt", b"hello")
check("bad file type 422", s == 422, str(m.get("detail"))[:60])
s, m = multipart("/api/upload", "broken.zip", b"not a zip at all")
check("corrupt zip 422", s == 422, str(m.get("detail"))[:60])
s, m = multipart("/api/upload", "far.geojson", json.dumps({"type": "Polygon",
        "coordinates": [[[2.0, 48.0], [2.1, 48.0], [2.1, 48.1], [2.0, 48.1], [2.0, 48.0]]]}).encode())
check("outside Ethiopia 422", s == 422, str(m.get("detail"))[:60])
# The 3-degree cap is a property of the raster journey, not of the file: a
# national scheme file uploads fine and is estimated per feature, so the cap is
# enforced when the extent is actually gridded.
s, m = multipart("/api/upload", "huge.geojson", json.dumps({"type": "Polygon",
        "coordinates": [[[35.0, 4.0], [45.0, 4.0], [45.0, 14.0], [35.0, 14.0], [35.0, 4.0]]]}).encode())
check("national extent uploads", s == 200, f"status {s}")
s, m = req("/api/analysis", {"aoi_type": "upload", "upload_id": m["upload_id"],
                             "system": "rainfed", "year": "2024/25", "season": "Meher"})
check("extent too large 422 at analysis", s == 422, str(m.get("detail"))[:60])
s, m = req("/api/predict?lat=88&lon=39.2")
check("out-of-range lat 422", s == 422)

print("\n11. WaPOR provider self-check")
s, wc = req("/api/wapor/check")
check("wapor check 200", s == 200 and "checks" in wc, f"ok={wc.get('ok')}")
named = {c["check"]: c for c in wc["checks"]}
# The live FAO catalogue contract this provider depends on. Skipped rather than
# failed when the service is unreachable, so the suite still runs offline.
if any("Cannot reach" in c["detail"] for c in wc["checks"]):
    print("     (FAO catalogue unreachable — skipping live contract checks)")
else:
    check("NPP and AETI mapsets exist",
          all(c["ok"] for k, c in named.items() if k.endswith("catalogue entry")))
    unit_checks = [c for k, c in named.items() if k.endswith("/day")]
    check("mapset units are as assumed",
          bool(unit_checks) and all(c["ok"] for c in unit_checks),
          "; ".join(f"{c['check']}={c['ok']}" for c in unit_checks))
    raster_checks = [c for k, c in named.items() if "raster exists" in k]
    check("the configured dekadal rasters are published",
          bool(raster_checks) and all(c["ok"] for c in raster_checks),
          "; ".join(f"{c['check']}={c['ok']}" for c in raster_checks))
    # The level the provider reads is what the dashboard reports as its
    # resolution, so a silent fall back to the 300 m global product would
    # overstate nothing but understate the detail actually available.
    s2, meth = req("/api/method")
    check("provider resolution is the L2 national 100 m",
          wc.get("level") == "L2" and meth.get("resolution_m") == 100,
          f"level={wc.get('level')} resolution={meth.get('resolution_m')}")

print("\n12. SPA is served")
s, html = req("/", raw=True)
check("index.html served", s == 200 and b'<div id="root">' in html)

print(f"\n{'='*58}\n  {ok} passed, {fail} failed\n{'='*58}")
raise SystemExit(1 if fail else 0)
