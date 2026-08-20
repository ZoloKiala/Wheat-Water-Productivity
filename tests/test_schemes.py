"""Per-scheme workflow: the reference notebook's input and output, over HTTP.

Fixtures are built in memory to the schema the notebook's own shapefiles use
(``Irrigated_Wheat_2026.shp`` and ``Irrigated_Wheat_2026_PNT.shp``): boundaries
carrying Crop/SOS/EOS/Scheme_ID/Area_ha/Name/ID, and sample points carrying
ID/Name/Location/SOS/EOS/Crop/Scheme_ID. The schemes deliberately span Afar and
Oromia, so the fixture also covers the case that first broke this workflow: a
national file whose bounding box is wider than the raster journey's extent cap.
"""
import io
import json
import os
import urllib.error
import urllib.request
import zipfile

import shapefile

BASE = os.environ.get("WWP_BASE", "http://127.0.0.1:8000")
ok = fail = 0

# name, scheme id, SOS, EOS, lat, lon — Afar and Oromia, as in the 2026 campaign.
SCHEMES = [
    ("Amibara", "AF001", "20251122", "20260220", 9.3445, 40.1692),
    ("Dubti", "AF002", "20251120", "20260316", 11.7376, 41.1188),
    ("Dodota", "OR001", "20241202", "20250402", 8.2944, 39.3839),
    ("Godino", "OR002", "20251207", "20260313", 8.8477, 39.0223),
]


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


def req(path, data=None, headers=None, raw=False):
    body = json.dumps(data).encode() if data is not None else None
    hdrs = dict(headers or {})
    if body:
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=body, headers=hdrs)
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


def multipart(path, filename, content):
    boundary = "----wwpSchemeBoundary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    r = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _zip(fill):
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf)
    fill(w)
    w.close()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("plots.shp", shp.getvalue())
        zf.writestr("plots.shx", shx.getvalue())
        zf.writestr("plots.dbf", dbf.getvalue())
        zf.writestr("plots.prj", 'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984"]]')
    return out.getvalue()


def boundary_zip(drop_sos=False):
    def fill(w):
        w.field("Crop", "C"); w.field("SOS", "D"); w.field("EOS", "D")
        w.field("Scheme_ID", "C"); w.field("Area_ha", "N", decimal=2)
        w.field("Name", "C"); w.field("ID", "N")
        for i, (name, sid, sos, eos, lat, lon) in enumerate(SCHEMES, start=1):
            d = 0.004
            w.poly([[[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d],
                     [lon - d, lat + d], [lon - d, lat - d]]])
            w.record(Crop="Wheat", SOS=None if drop_sos else sos, EOS=eos,
                     Scheme_ID=sid, Area_ha=12.62, Name=name, ID=i)
    return _zip(fill)


def point_zip():
    def fill(w):
        w.field("ID", "N"); w.field("Name", "C"); w.field("Location", "N")
        w.field("SOS", "D"); w.field("EOS", "D"); w.field("Crop", "C")
        w.field("Scheme_ID", "C")
        pid = 0
        for loc, (name, sid, sos, eos, lat, lon) in enumerate(SCHEMES, start=1):
            for k in range(5):
                pid += 1
                w.point(lon + 0.0009 * k, lat + 0.0007 * k)
                w.record(ID=pid, Name=name, Location=loc, SOS=sos, EOS=eos,
                         Crop="Wheat", Scheme_ID=sid)
    return _zip(fill)


print("\n1. Boundary file upload")
s, up = multipart("/api/upload", "Irrigated_Wheat_2026.zip", boundary_zip())
check("upload accepted", s == 200, f"status {s}")
check("attributes read from the DBF",
      set(["Name", "SOS", "EOS", "Scheme_ID"]).issubset(set(up.get("fields", []))),
      str(up.get("fields")))
check("geometry type reported", up.get("geometry_type") == "polygon")
check("flagged ready for per-plot estimation", up.get("scheme_ready") is True)
check("validation message matches the notebook",
      up["validation"]["message"] == "Data validation is successful!")
check("national extent is not rejected", up["bounds"][1][0] - up["bounds"][0][0] > 3.0,
      "Afar to Oromia spans more than the 3-degree raster cap")

print("\n2. Per-boundary analysis")
s, res = req("/api/schemes/analysis", {"upload_id": up["upload_id"]})
check("analysis runs", s == 200, f"status {s}")
check("one row per boundary", res["n_features"] == len(SCHEMES))
check("result columns are the notebook's",
      res["result_columns"] == ["NPP", "EYield_tpha", "AETI_mm", "WP_kgpm3", "LGP"])
check("uploaded attributes are preserved",
      all(c in res["columns"] for c in ("Crop", "SOS", "EOS", "Scheme_ID", "Name", "ID")))
row = res["features"][0]
check("every result column is populated",
      all(isinstance(row.get(c), (int, float)) for c in res["result_columns"]),
      str({c: row.get(c) for c in res["result_columns"]}))
check("LGP is the SOS-to-EOS day count", row["LGP"] == 90, f"LGP={row['LGP']}")
check("yield is in a plausible wheat range",
      all(0.2 <= f["EYield_tpha"] <= 12 for f in res["features"]))
check("water productivity is in a plausible range",
      all(0.1 <= f["WP_kgpm3"] <= 4 for f in res["features"]))
check("boundaries produce no aggregate table", res["aggregate"] is None)
check("both notebook figures have bars",
      len(res["charts"]) == len(SCHEMES)
      and all(c["yield_t_ha"] and c["wwp"] for c in res["charts"]))
check("each boundary is sampled over many cells", row.get("n_samples", 0) > 20,
      f"n_samples={row.get('n_samples')}")

print("\n3. Per-feature CSV matches the notebook's saved file")
s, csv = req(f"/api/schemes/export/csv?run_id={res['run_id']}&level=features", raw=True)
head = csv.decode().splitlines()[0]
check("CSV served", s == 200)
check("header carries attributes then results",
      head.endswith("NPP,EYield_tpha,AETI_mm,WP_kgpm3,LGP"), head)
check("one line per feature", len(csv.decode().strip().splitlines()) == len(SCHEMES) + 1)

print("\n4. Point samples and the per-plot median")
s, up2 = multipart("/api/upload", "Irrigated_Wheat_2026_PNT.zip", point_zip())
check("point file accepted", s == 200, f"status {s}")
check("recognised as points", up2.get("geometry_type") == "point")
check("point file is scheme-ready", up2.get("scheme_ready") is True)
s, res2 = req("/api/schemes/analysis", {"upload_id": up2["upload_id"]})
check("analysis runs", s == 200, f"status {s}")
check("one row per sample point", res2["n_features"] == len(SCHEMES) * 5)
agg = res2["aggregate"]
check("aggregate present for points", agg is not None)
check("grouped as notebook cell 26 groups",
      agg and agg["group_cols"] == ["Name", "Location", "SOS", "EOS", "LGP", "Scheme_ID"],
      str(agg and agg["group_cols"]))
check("one aggregated row per plot", agg and len(agg["rows"]) == len(SCHEMES))
check("median columns are the notebook's",
      agg and agg["value_cols"] == ["NPP", "AETI_mm", "EYield_tpha", "WP_kgpm3"])
if agg:
    plot = agg["rows"][0]
    members = [f["WP_kgpm3"] for f in res2["features"]
               if f["Name"] == plot["Name"] and f["Location"] == plot["Location"]]
    med = sorted(members)[len(members) // 2]
    check("aggregate value is the median of its samples",
          abs(plot["WP_kgpm3"] - med) < 0.011, f"{plot['WP_kgpm3']} vs {med}")
    check("sample count reported per plot", plot["n_samples"] == 5)
check("charts are drawn from the medians", len(res2["charts"]) == len(SCHEMES))

print("\n5. Aggregated CSV")
s, csv2 = req(f"/api/schemes/export/csv?run_id={res2['run_id']}&level=schemes", raw=True)
check("CSV served", s == 200)
check("header is group columns then medians",
      csv2.decode().splitlines()[0]
      == "Name,Location,SOS,EOS,LGP,Scheme_ID,NPP,AETI_mm,EYield_tpha,WP_kgpm3",
      csv2.decode().splitlines()[0])
s, err = req(f"/api/schemes/export/csv?run_id={res['run_id']}&level=schemes")
check("aggregated CSV refused for boundary runs", s == 404, f"status {s}")

print("\n6. Validation and error paths")
s, bad = multipart("/api/upload", "no_sos.zip", boundary_zip(drop_sos=True))
check("file missing SOS still uploads", s == 200, f"status {s}")
check("but is not scheme-ready", bad.get("scheme_ready") is False)
check("and says which field is missing",
      any("SOS" in p for p in bad["validation"]["problems"]),
      str(bad["validation"]["problems"][:2]))
s, r = req("/api/schemes/analysis", {"upload_id": bad["upload_id"]})
check("analysis refuses it with 422", s == 422, f"status {s}")
check("message names the failure",
      isinstance(r.get("detail"), str) and "validation failed" in r["detail"].lower())
s, r = req("/api/schemes/analysis", {"upload_id": "does-not-exist"})
check("unknown upload id is rejected", s == 422, f"status {s}")
s, r = req(f"/api/schemes/export/csv?run_id=nope&level=features")
check("unknown run id is rejected", s == 404, f"status {s}")

print("\n7. A point file cannot drive the raster journey")
s, r = req("/api/analysis", {"aoi_type": "upload", "upload_id": up2["upload_id"],
                             "system": "irrigated", "year": "2025/26",
                             "season": "Dry season (Nov–Mar)"})
check("points rejected as an extent with a useful message",
      s == 422 and "scheme analysis" in str(r.get("detail", "")).lower(),
      str(r.get("detail"))[:80])

print("\n8. Idempotency and caching")
key = "scheme-test-key-1"
s, a = req("/api/schemes/analysis", {"upload_id": up["upload_id"]},
           headers={"Idempotency-Key": key})
s, b = req("/api/schemes/analysis", {"upload_id": up["upload_id"]},
           headers={"Idempotency-Key": key})
check("same key returns the same run", a["run_id"] == b["run_id"])

print(f"\n{'=' * 58}\n  {ok} passed, {fail} failed\n{'=' * 58}")
raise SystemExit(1 if fail else 0)
