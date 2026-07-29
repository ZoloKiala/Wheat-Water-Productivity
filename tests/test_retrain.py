"""Exercise the retrain-and-promote happy path against the running server."""
import json
import os
import urllib.error
import urllib.request

import numpy as np

BASE = os.environ.get("WWP_BASE", "http://127.0.0.1:8000")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.geodata import FEATURE_NAMES, PROVIDER, feature_matrix


ADMIN = os.environ.get("WWP_ADMIN_TOKEN")
if not ADMIN:
    raise SystemExit(
        "WWP_ADMIN_TOKEN must be set both on the server and in this environment "
        "to exercise the retrain workflow (model management fails closed without it)."
    )


def multipart(path, filename, content):
    boundary = "----wwpB"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{filename}\"\r\nContent-Type: text/csv\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    r = urllib.request.Request(BASE + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "X-Admin-Token": ADMIN})
    try:
        with urllib.request.urlopen(r, timeout=600) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read())


before = get("/api/model/info")
print(f"Active before: {before['version']}  rmse={before['metrics']['rmse']}")

# Simulate a new field campaign: 600 observations around Sinana (Bale).
rng = np.random.default_rng(2026)
lat = 7.10 + rng.uniform(-0.15, 0.15, 600)
lon = 40.22 + rng.uniform(-0.15, 0.15, 600)
feats = PROVIDER.assemble(lat, lon, "rainfed", "2025/26", "Meher")
y = PROVIDER.true_wwp(feats, rng=rng)
X = feature_matrix(feats)

lines = [",".join(FEATURE_NAMES + ["wwp"])]
for i in range(len(y)):
    lines.append(",".join(f"{v:.4f}" for v in X[i]) + f",{y[i]:.4f}")
csv = ("\n".join(lines) + "\n").encode()

status, res = multipart("/api/model/retrain", "campaign_2026.csv", csv)
print(f"\nretrain -> HTTP {status}")
print(json.dumps(res, indent=2))

after = get("/api/model/info")
print(f"\nActive after: {after['version']}  rmse={after['metrics']['rmse']}")

assert status == 200, "retrain failed"
assert res["n_new_observations"] == 600
if res["promoted"]:
    assert after["version"] != before["version"], "promoted but version did not change"
    print("PASS: candidate promoted and became active")
else:
    assert after["version"] == before["version"], "not promoted but version changed"
    print("PASS: candidate rejected, active model retained (guard works)")

# Inference must still work on the (possibly new) active model.
p = get("/api/predict?lat=7.10&lon=40.22&system=rainfed&year=2025/26&season=Meher")
assert 0.2 < p["wwp"] < 2.2, p
print(f"PASS: inference still serving — wwp={p['wwp']}")
