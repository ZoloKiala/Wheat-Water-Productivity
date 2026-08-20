"""Ingest parity against the real 2026 campaign shapefiles.

``test_notebook_parity.py`` checks the arithmetic: given the notebook's own NPP
and AETI, this service reproduces its yield and water productivity. This file
checks the other half, the part that arithmetic cannot reach: given the
notebook's own *input files*, does the service read them the way ``etwapor``
reads them?

What is verified is everything that does not depend on WaPOR retrieval, so the
checks hold under the synthetic provider:

* both files parse, with their attributes and CRS
* every feature passes validation, as ``etwapor.util.validate_input`` reports
* LGP per feature matches the notebook's printed LGP column
* the 57 sample points collapse to exactly the six plots of notebook cell 26,
  with the same Location, Scheme_ID and LGP per plot
* the in-app loader (``app.campaign``), which reads the same files from disk and
  offers them through the interface, produces feature-for-feature what uploading
  the zipped shapefile produces

The shapefiles are IWMI field data and are not committed here. Point
``WWP_CAMPAIGN_DATA`` at the notebook's ``Data`` directory to run these checks;
without it the file skips rather than fails, so the suite stays green for anyone
without the campaign data.
"""
import io
import json
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import aoi, campaign, schemes  # noqa: E402

DATA = Path(os.environ.get("WWP_CAMPAIGN_DATA", "Data"))
# The in-app loader resolves the same directory from the same variable.
os.environ["WWP_CAMPAIGN_DATA"] = str(DATA)
BOUNDARIES = "Irrigated_Wheat_2026"
POINTS = "Irrigated_Wheat_2026_PNT"

# Notebook cell 17: the LGP column of the boundary results, by feature ID.
NOTEBOOK_LGP = {1: 90, 2: 116, 3: 116, 4: 116, 5: 121, 6: 96}

# Notebook cell 27: df_agg, one row per plot, ordered as the notebook prints it.
NOTEBOOK_PLOTS = [
    ("Amibara", 1, "AF001", 90),
    ("Dodota", 4, "OR001", 111),
    ("Dodota", 6, "OR001", 102),
    ("Dubti", 2, "AF002", 116),
    ("Dubti", 3, "AF002", 116),
    ("Godino", 5, "OR002", 96),
]

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


def zip_of(base: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for ext in ("shp", "shx", "dbf", "prj"):
            zf.write(DATA / f"{base}.{ext}", f"{base}.{ext}")
    return buf.getvalue()


if not (DATA / f"{BOUNDARIES}.shp").exists():
    print(f"\nSKIP — campaign shapefiles not found in '{DATA}'.")
    print("      Set WWP_CAMPAIGN_DATA to the notebook's Data directory to run these checks.")
    raise SystemExit(0)

print("\n1. Boundary file (6 irrigation schemes)")
rec = aoi.register_upload(f"{BOUNDARIES}.zip", zip_of(BOUNDARIES))
check("parsed as polygons", rec["geometry_type"] == "polygon")
check("all six features read", rec["n_features"] == 6, f"{rec['n_features']} features")
check("CRS recognised as EPSG:4326", "4326" in rec["crs"], rec["crs"])
check("attributes carried through",
      set(["Crop", "SOS", "EOS", "Scheme_ID", "Area_ha", "Name", "ID"])
      .issubset(rec["features"][0]["attrs"]),
      str(list(rec["features"][0]["attrs"]))[:70])

res = schemes.analyse(rec)
check("validation passes, as etwapor reports",
      res["validation"]["message"] == "Data validation is successful!")
lgp = {int(f["ID"]): f["LGP"] for f in res["features"]}
check("LGP matches the notebook for every feature", lgp == NOTEBOOK_LGP, str(lgp))
check("the file spans four distinct seasons", len(res["season_windows"]) == 4,
      f"{len(res['season_windows'])} windows")
check("result columns are the notebook's",
      res["columns"][-5:] == ["NPP", "EYield_tpha", "AETI_mm", "WP_kgpm3", "LGP"])
check("no aggregate table for boundaries", res["aggregate"] is None)

print("\n2. Point file (57 samples)")
rec_p = aoi.register_upload(f"{POINTS}.zip", zip_of(POINTS))
check("parsed as points", rec_p["geometry_type"] == "point")
check("all 57 samples read", rec_p["n_features"] == 57, f"{rec_p['n_features']} features")

res_p = schemes.analyse(rec_p)
check("validation passes", res_p["validation"]["ok"] is True)
check("one row per sample point", len(res_p["features"]) == 57)

agg = res_p["aggregate"]
check("aggregate produced", agg is not None)
check("grouped as notebook cell 26 groups",
      agg and agg["group_cols"] == ["Name", "Location", "SOS", "EOS", "LGP", "Scheme_ID"])
plots = [(r["Name"], r["Location"], r["Scheme_ID"], r["LGP"]) for r in (agg["rows"] if agg else [])]
check("the same six plots as the notebook, in the same order",
      plots == NOTEBOOK_PLOTS, str(plots))
check("every sample is assigned to a plot",
      sum(r["n_samples"] for r in agg["rows"]) == 57 if agg else False)

print("\n3. Exports")
csv = schemes.export_csv(res["run_id"], "features")
head = csv.splitlines()[0]
check("per-feature CSV header ends with the notebook's columns",
      head.endswith("NPP,EYield_tpha,AETI_mm,WP_kgpm3,LGP"), head)
check("per-feature CSV has one line per scheme", len(csv.strip().splitlines()) == 7)
csv_p = schemes.export_csv(res_p["run_id"], "schemes")
check("per-plot CSV header matches df_agg",
      csv_p.splitlines()[0]
      == "Name,Location,SOS,EOS,LGP,Scheme_ID,NPP,AETI_mm,EYield_tpha,WP_kgpm3",
      csv_p.splitlines()[0])
check("per-plot CSV has one line per plot", len(csv_p.strip().splitlines()) == 7)

print("")
print("4. In-app loader (app.campaign): the same files, read from disk")
# The interface offers these files by reading them from this machine and handing
# them to the client as GeoJSON, which is then posted back through /upload. The
# whole point of that route is that it is the ordinary one, so what it produces
# has to match what the zipped shapefile above produced, feature for feature.
offered = {d["name"]: d for d in campaign.available()}
check("both campaign datasets are offered", set(offered) ==
      {"campaign-boundaries", "campaign-points"}, str(sorted(offered)))
check("boundaries offered as 6 polygons",
      offered.get("campaign-boundaries", {}).get("n_features") == 6
      and offered["campaign-boundaries"]["geometry_type"] == "polygon")
check("points offered as 57 samples",
      offered.get("campaign-points", {}).get("n_features") == 57
      and offered["campaign-points"]["geometry_type"] == "point")

loaded = campaign.load("campaign-boundaries")
rec_l = aoi.register_upload(loaded["filename"], json.dumps(loaded["geojson"]).encode())
check("loaded boundaries carry the same features", rec_l["n_features"] == 6)
check("loaded boundaries cover the same extent", rec_l["bounds"] == rec["bounds"],
      str(rec_l["bounds"]))
res_l = schemes.analyse(rec_l)
check("loaded boundaries give the notebook LGP",
      {int(f["ID"]): f["LGP"] for f in res_l["features"]} == NOTEBOOK_LGP)
check("loaded boundaries keep every attribute the shapefile carried",
      set(res_l["columns"]) == set(res["columns"]))

loaded_p = campaign.load("campaign-points")
rec_lp = aoi.register_upload(loaded_p["filename"], json.dumps(loaded_p["geojson"]).encode())
res_lp = schemes.analyse(rec_lp)
check("loaded points give the same 57 samples", len(res_lp["features"]) == 57)
check("loaded points collapse to the notebook plots",
      [(r["Name"], r["Location"], r["Scheme_ID"], r["LGP"])
       for r in res_lp["aggregate"]["rows"]] == NOTEBOOK_PLOTS)

print(f"\n{'=' * 58}\n  {ok} passed, {fail} failed\n{'=' * 58}")
if not res["synthetic"]:
    print("  (run against live WaPOR — values, not only structure, are real)")
raise SystemExit(1 if fail else 0)
