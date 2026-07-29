"""End-to-end check against the running uvicorn server."""
import io
import json
import time
import urllib.error
import urllib.request
import zipfile

import os

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


# ── wait for boot (first run trains the model) ────────────────────────────
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
check("health 200", s == 200 and h["status"] == "ok", f"model {h.get('model_version')}")
s, u = req("/api/admin-units")
check("admin-units tree", s == 200 and "Oromia" in u["tree"] and u["tree"]["Oromia"]["Arsi"])
check("seasons per system",
      u["seasons"]["rainfed"] == ["Meher", "Belg"] and len(u["seasons"]["irrigated"]) == 1)

print("\n2. Model info")
s, mi = req("/api/model/info")
check("model info 200", s == 200 and mi["metrics"]["r2"] > 0.8, f"R2={mi['metrics']['r2']}")
check("10 features listed", len(mi["features"]) == 10)
check("importance graded", mi["importance"][0]["importance"] == 100.0
      and mi["importance"][-1]["importance"] < 100.0,
      f"top={mi['importance'][0]['label']}")

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
check("importance present", len(r["feature_importance"]) == 10)
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
check("explain 200", s == 200 and len(e["contributions"]) == 7)
tot = e["base"] + sum(c["contribution"] for c in e["contributions"])
check("contributions reconstruct prediction (top-7 partial)",
      abs(tot - e["prediction"]) < 0.35, f"base+top7={tot:.3f} vs pred={e['prediction']}")
check("explain matches predict", abs(e["prediction"] - p["wwp"]) < 1e-6)
check("contributions sorted by magnitude",
      all(abs(e["contributions"][i]["contribution"]) >= abs(e["contributions"][i+1]["contribution"])
          for i in range(len(e["contributions"]) - 1)))
check("features carry value+unit",
      all(("value" in c and "unit" in c) for c in e["contributions"]))

print("\n6. CSV export")
s, csv = req(f"/api/export/csv?run_id={run_id}", raw=True)
lines = csv.decode().strip().splitlines()
check("csv 200 with rows", s == 200 and len(lines) > 100, f"{len(lines)-1} data rows")
check("csv header", lines[0] == "lat,lon,wwp_kg_m3,pred_yield_t_ha,npp_kgc_ha,aet_mm")
check("csv row parses", len(lines[1].split(",")) == 6, lines[1])
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
s, m = multipart("/api/upload", "huge.geojson", json.dumps({"type": "Polygon",
        "coordinates": [[[35.0, 4.0], [45.0, 4.0], [45.0, 14.0], [35.0, 14.0], [35.0, 4.0]]]}).encode())
check("extent too large 422", s == 422, str(m.get("detail"))[:60])
s, m = req("/api/predict?lat=88&lon=39.2")
check("out-of-range lat 422", s == 422)

print("\n11. Model management is protected")
TINY = (b"npp,rainfall,aet,soc,elevation,fertilizer,planting_dekad,improved_seed,"
        b"extension_visits,market_dist,wwp\n800,700,400,1.3,2300,90,4,1,2,15,1.0\n")
ADMIN = os.environ.get("WWP_ADMIN_TOKEN", "")

s, m = multipart("/api/model/retrain", "tiny.csv", TINY)
if ADMIN:
    check("retrain without a token is rejected", s == 401, str(m.get("detail"))[:60])
    s, m = multipart("/api/model/retrain", "tiny.csv", TINY, headers={"X-Admin-Token": "wrong"})
    check("retrain with a wrong token is rejected", s == 401, str(m.get("detail"))[:60])
    # Authorized requests reach validation.
    hdr = {"X-Admin-Token": ADMIN}
    s, m = multipart("/api/model/retrain", "tiny.csv", TINY, headers=hdr)
    check("authorized retrain too few rows 422", s == 422, str(m.get("detail"))[:60])
    s, m = multipart("/api/model/retrain", "wrong.csv", b"a,b,c\n1,2,3\n", headers=hdr)
    check("authorized retrain missing columns 422", s == 422, str(m.get("detail"))[:70])
else:
    # Fails closed: a deployment that forgets the token cannot replace the model.
    check("retrain disabled when no token is configured", s == 503,
          str(m.get("detail"))[:70])
    print("     (set WWP_ADMIN_TOKEN on the server and in this env to test the "
          "authorized paths)")

print("\n12. SPA is served")
s, html = req("/", raw=True)
check("index.html served", s == 200 and b'<div id="root">' in html)

print(f"\n{'='*58}\n  {ok} passed, {fail} failed\n{'='*58}")
raise SystemExit(1 if fail else 0)
