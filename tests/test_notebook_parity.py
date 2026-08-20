"""Prove the ported WWPT method reproduces the reference notebook.

The notebook ``ETH_WWP_WaPORv3_Irrigaed_Wheat_2026_Final.ipynb`` is the
authoritative implementation (``etwapor.productivity.estimate_wheat_wp``). It
does not ship with this repository, but every result it printed is stored in its
own output cells, and those results pin the method down completely: given
seasonal NPP and AETI, the yield and water productivity are fully determined.

This test replays each published record through ``app.wwpt`` and requires it to
land on the same numbers at the precision the notebook printed them (2 dp). Run
it after any change to the crop parameters or the estimation chain::

    python tests/test_notebook_parity.py

No server and no network are needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import wwpt  # noqa: E402

# (NPP gC/m2/season, AETI mm/season, EYield_tpha, WP_kgpm3) exactly as printed
# by the notebook: the 6 polygon features (cell 17) and the first 10 point
# samples (cell 22).
NOTEBOOK_RECORDS = [
    # cell 17 — gdf_poly_result, one row per irrigation-scheme polygon
    ("Amibara",  184.95, 297.32, 1.78, 0.60),
    ("Dubti-2",  168.70, 319.43, 1.62, 0.51),
    ("Dubti-3",  138.29, 291.45, 1.33, 0.46),
    ("Dubti-4",   65.24, 193.81, 0.63, 0.32),
    ("Dodota",   478.22, 369.28, 4.59, 1.24),
    ("Godino",   319.52, 219.53, 3.07, 1.40),
    # cell 22 — gdf_pnt_result.head(10), point samples
    ("pnt-1",    233.34, 346.70, 2.24, 0.65),
    ("pnt-2",    215.32, 325.60, 2.07, 0.63),
    ("pnt-3",    206.91, 319.00, 1.99, 0.62),
    ("pnt-4",    196.37, 311.10, 1.89, 0.61),
    ("pnt-5",    185.43, 297.60, 1.78, 0.60),
    ("pnt-6",    293.53, 455.20, 2.82, 0.62),
    ("pnt-7",    291.49, 444.50, 2.80, 0.63),
    ("pnt-8",    286.75, 435.90, 2.75, 0.63),
    ("pnt-9",    226.50, 344.10, 2.17, 0.63),
    ("pnt-10",   215.36, 329.90, 2.07, 0.63),
]

# Crop constant AOT*fc*hi/(1-mc) implied by the records above. The tolerance is
# the rounding of the notebook's own 2-dp output, not a modelling allowance.
NOTEBOOK_CONSTANT = 0.4322
CONSTANT_TOLERANCE = 0.002


def main() -> int:
    failures = []

    const = wwpt.PARAMS.constant
    if abs(const - NOTEBOOK_CONSTANT) > CONSTANT_TOLERANCE:
        failures.append(
            f"crop constant {const:.5f} is outside the notebook's implied "
            f"{NOTEBOOK_CONSTANT} +/- {CONSTANT_TOLERANCE}"
        )
    print(f"crop parameters: {wwpt.PARAMS.as_dict()}")
    print(f"constant AOT*fc*hi/(1-mc) = {const:.5f} "
          f"(notebook implies {NOTEBOOK_CONSTANT})\n")

    print(f"{'record':<10}{'NPP':>8}{'AETI':>8}{'yield':>8}{'want':>7}"
          f"{'WP':>7}{'want':>7}")
    for name, npp, aeti, want_yield, want_wp in NOTEBOOK_RECORDS:
        est = wwpt.estimate(npp, aeti)
        got_yield = round(float(est["yield_t_ha"]), 2)
        got_wp = round(float(est["wwp_kg_m3"]), 2)
        bad = got_yield != want_yield or got_wp != want_wp
        print(f"{name:<10}{npp:>8.2f}{aeti:>8.2f}{got_yield:>8.2f}"
              f"{want_yield:>7.2f}{got_wp:>7.2f}{want_wp:>7.2f}"
              f"{'   <-- MISMATCH' if bad else ''}")
        if bad:
            failures.append(
                f"{name}: yield {got_yield} vs {want_yield}, "
                f"WP {got_wp} vs {want_wp}"
            )

    # The notebook's LGP column is simply EOS - SOS in days.
    from datetime import date
    if wwpt.lgp_days(date(2025, 11, 22), date(2026, 2, 20)) != 90:
        failures.append("LGP for Amibara (2025-11-22 -> 2026-02-20) is not 90 days")
    if wwpt.lgp_days(date(2025, 11, 20), date(2026, 3, 16)) != 116:
        failures.append("LGP for Dubti (2025-11-20 -> 2026-03-16) is not 116 days")

    print()
    if failures:
        print(f"FAILED — {len(failures)} problem(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"PASS — all {len(NOTEBOOK_RECORDS)} notebook records reproduced, "
          "LGP matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
