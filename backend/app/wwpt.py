"""Wheat biomass, yield and water-productivity estimation — the WWPT method.

Ported from the IWMI notebook ``ETH_WWP_WaPORv3_Irrigaed_Wheat_2026_Final.ipynb``
(``etwapor.productivity.estimate_wheat_wp``), which is the reference
implementation this service must agree with.

Method
------
::

    TB  = AOT * fc * NPP * 22.222 / (1 - mc)      kg dry matter / ha
    Y   = TB * hi                                  kg grain / ha
    CWP = Y / SWC                                  kg grain / m3

    NPP   seasonal net primary production, gC/m2/season (WaPOR v3)
    AOT   above-ground over total biomass ratio
    fc    light-use-efficiency correction factor
    mc    moisture content of the fresh biomass
    hi    harvest index
    SWC   seasonal water consumption, m3/ha = AETI[mm] * 10

The 22.222 factor converts gC/m2 to kg dry matter/ha: 1 gC/m2 is 10 kgC/ha, and
dry matter is about 45% carbon, so 10 / 0.45 = 22.222 kg DM/ha per gC/m2.

Crop parameters
---------------
Taken from ``etwapor.data.wheat``, the reference implementation's own values,
after IWMI supplied the package. Their product,
``AOT * fc * hi / (1 - mc) = 0.4320``, is what every result depends on, and it
reproduces each of the 16 records the notebook publishes at its printed
precision (``tests/test_notebook_parity.py`` asserts this).

The notebook recommends replacing these general values with Ethiopia-specific
parameters derived with EIAR. To do that without touching code, point
``WWP_CROP_PARAMS`` at a JSON file or edit ``backend/crop_params.json``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import numpy as np

# gC/m2 -> kg dry matter/ha (10 kgC/ha per gC/m2, dry matter is ~45% carbon).
NPP_TO_KG_DM_HA = 22.222

# 1 mm of water over 1 ha is 10 m3.
MM_TO_M3_PER_HA = 10.0


@dataclass(frozen=True)
class CropParams:
    """Wheat crop parameters for the biomass-to-yield conversion."""

    aot: float = 0.85     # above-ground over total wheat biomass (FAO, 2020b)
    fc: float = 0.90      # light-use-efficiency correction factor for wheat
    mc: float = 0.15      # moisture content in the wheat grain (FAO, 2020b)
    hi: float = 0.48      # wheat harvest index (FAO, 2020b)
    source: str = "etwapor.data.wheat (FAO, 2020b reference values for wheat)"

    @property
    def constant(self) -> float:
        """AOT * fc * hi / (1 - mc) — the only combination the outputs depend on.

        Reported so a parity check against the reference stays a one-line
        comparison: ``etwapor.data.wheat`` gives 0.4320.
        """
        return self.aot * self.fc * self.hi / (1.0 - self.mc)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["constant"] = round(self.constant, 5)
        return d


def _load_params() -> CropParams:
    """Crop parameters from JSON, falling back to the documented defaults.

    Looked up in order: ``$WWP_CROP_PARAMS``, then ``backend/crop_params.json``.
    Unknown keys are rejected rather than silently ignored, so a typo in a
    parameter name cannot quietly leave a default in place while appearing to
    have been overridden.
    """
    candidates = []
    env = os.environ.get("WWP_CROP_PARAMS")
    if env:
        candidates.append(Path(env))
    candidates.append(Path(__file__).resolve().parent.parent / "crop_params.json")
    for path in candidates:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        allowed = set(CropParams.__dataclass_fields__)
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(
                f"{path}: unknown crop parameter(s) {sorted(unknown)}; "
                f"expected any of {sorted(allowed)}"
            )
        return CropParams(**data)
    return CropParams()


PARAMS = _load_params()


# -- the estimation chain -------------------------------------------------
def total_biomass(npp, params: CropParams = PARAMS):
    """Seasonal NPP (gC/m2) -> total biomass (kg dry matter/ha)."""
    return (
        params.aot * params.fc * np.asarray(npp, dtype=np.float64)
        * NPP_TO_KG_DM_HA / (1.0 - params.mc)
    )


def grain_yield(npp, params: CropParams = PARAMS):
    """Seasonal NPP (gC/m2) -> harvestable grain yield (kg/ha)."""
    return total_biomass(npp, params) * params.hi


def seasonal_water(aeti_mm):
    """Seasonal AETI (mm) -> seasonal water consumption (m3/ha)."""
    return np.asarray(aeti_mm, dtype=np.float64) * MM_TO_M3_PER_HA


def water_productivity(npp, aeti_mm, params: CropParams = PARAMS):
    """Crop water productivity (kg grain per m3 of water consumed)."""
    return grain_yield(npp, params) / seasonal_water(aeti_mm)


def estimate(npp, aeti_mm, params: CropParams = PARAMS) -> dict:
    """Run the whole chain, returning every intermediate.

    Keeping the intermediates is what lets the dashboard show *how* a value was
    arrived at rather than only the final number: with a deterministic method
    the full derivation is the explanation.
    """
    tb = total_biomass(npp, params)
    y = tb * params.hi
    swc = seasonal_water(aeti_mm)
    return {
        "npp": np.asarray(npp, dtype=np.float64),
        "aeti_mm": np.asarray(aeti_mm, dtype=np.float64),
        "biomass_kg_ha": tb,
        "yield_kg_ha": y,
        "yield_t_ha": y / 1000.0,
        "swc_m3_ha": swc,
        "wwp_kg_m3": y / swc,
    }


# -- season windows -------------------------------------------------------
# The notebook is driven by a per-feature SOS/EOS pair. This service analyses a
# whole area of interest rather than individual plots, so the growing season
# comes from the selected system and season instead — but it resolves to the
# same thing the method needs: a concrete [SOS, EOS] window over which WaPOR
# dekads are summed. The window and its length are reported with every result,
# so the assumption is visible rather than buried.
SEASON_WINDOWS = {
    ("irrigated", "Dry season (Nov–Mar)"): ((11, 1), (3, 31)),
    ("rainfed", "Meher"): ((6, 1), (11, 30)),
    ("rainfed", "Belg"): ((2, 1), (6, 30)),
}


def season_window(system: str, year: str, season: str) -> tuple[date, date]:
    """Resolve (system, '2024/25', season) to concrete SOS and EOS dates.

    ``year`` is an Ethiopian cropping-year label spanning two calendar years.
    Seasons starting in the second half of the calendar year (Meher, the
    irrigated dry season) begin in its first year; Belg falls in the second.
    """
    try:
        (sm, sd), (em, ed) = SEASON_WINDOWS[(system, season)]
    except KeyError:
        raise ValueError(f"No season window is defined for {system} / {season}.")
    first = int(year.split("/")[0])
    start_year = first if sm >= 6 else first + 1
    end_year = start_year + (1 if (em, ed) < (sm, sd) else 0)
    return date(start_year, sm, sd), date(end_year, em, ed)


def lgp_days(sos: date, eos: date) -> int:
    """Length of growing period, matching the notebook's LGP column."""
    return (eos - sos).days


# -- input validation (mirrors etwapor.util.validate_input) ---------------
REQUIRED_FIELDS = ("ID", "SOS", "EOS")

# Grouping fields. Not required by the reference validator, and not required
# here either: without them the per-plot aggregation is skipped rather than the
# file being refused, because the per-feature estimate does not need them.
GROUPING_FIELDS = ("Name", "Location", "Scheme_ID")


def validate_features(records: list[dict], geometry_type: str = "polygon") -> list[str]:
    """Check uploaded features carry what the WWPT method requires.

    Returns human-readable problems; an empty list means the input is usable.
    Mirrors ``etwapor.util.validate_input``, including which fields are
    mandatory: ID, SOS, EOS and geometry, and nothing else. ID must parse as an
    integer and be unique across the file, because the reference selects each
    feature by ID; SOS and EOS must be valid dates with EOS after SOS, because
    they define the window WaPOR dekads are summed over.

    Problems are reported against the feature's ID where it has a usable one,
    as the reference does, since that is what the user sees in the attribute
    table; features whose ID is itself the problem are reported by position.
    """
    problems: list[str] = []
    missing_cols = [c for c in REQUIRED_FIELDS
                    if not any(c in rec for rec in records)]
    if missing_cols:
        return [f"Missing required column(s): {missing_cols}. "
                f"The method needs {list(REQUIRED_FIELDS)} on every feature."]

    seen: dict[int, int] = {}
    duplicates: list[int] = []
    bad_sos: list[str] = []
    bad_eos: list[str] = []
    bad_order: list[str] = []

    for i, rec in enumerate(records):
        raw_id = rec.get("ID")
        label = str(raw_id) if raw_id not in (None, "") else f"(row {i + 1})"
        if raw_id in (None, ""):
            problems.append(f"Row {i + 1}: ID is missing.")
        else:
            try:
                ident = int(float(str(raw_id).strip()))
            except (TypeError, ValueError):
                problems.append(f"Row {i + 1}: ID '{raw_id}' is not a whole number.")
            else:
                label = str(ident)
                if ident in seen:
                    duplicates.append(ident)
                seen[ident] = i

        sos_raw, eos_raw = rec.get("SOS"), rec.get("EOS")
        sos, eos = _as_date(sos_raw), _as_date(eos_raw)
        if sos is None:
            bad_sos.append(label)
        if eos is None:
            bad_eos.append(label)
        if sos and eos and eos <= sos:
            bad_order.append(label)

    if duplicates:
        problems.append("ID values must be unique. Duplicate IDs found: "
                        f"{sorted(set(duplicates))}.")
    if bad_sos:
        problems.append(f"Invalid or missing SOS for ID(s): {bad_sos[:10]}.")
    if bad_eos:
        problems.append(f"Invalid or missing EOS for ID(s): {bad_eos[:10]}.")
    if bad_order:
        problems.append(f"EOS must be later than SOS. Check ID(s): {bad_order[:10]}.")
    return problems


def grouping_note(records: list[dict], geometry_type: str) -> str | None:
    """Why a point file will not produce a per-plot table, if it will not.

    Not an error: the per-feature estimate is unaffected. Worth saying, because
    a user who expects the notebook's aggregated table and does not get one
    should learn why from the interface rather than from the absence.
    """
    if not geometry_type.lower().startswith("point"):
        return None
    if any(f in rec for rec in records for f in GROUPING_FIELDS):
        return None
    return (f"No per-plot table: sample points can only be grouped when the file "
            f"carries one of {list(GROUPING_FIELDS)}.")


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def method_info() -> dict:
    """Everything the dashboard needs to describe how a number was produced."""
    return {
        "method": "WWPT — WaPOR v3 NPP/AETI biomass-to-yield estimation",
        "reference": (
            "ETH_WWP_WaPORv3_Irrigaed_Wheat_2026_Final.ipynb "
            "(etwapor.productivity.estimate_wheat_wp)"
        ),
        "equations": [
            "TB = AOT · fc · NPP · 22.222 / (1 − mc)",
            "Y = TB · hi",
            "CWP = Y / SWC,  SWC = AETI · 10",
        ],
        "crop_parameters": PARAMS.as_dict(),
        "units": {
            "npp": "gC/m²/season", "aeti": "mm/season",
            "biomass": "kg DM/ha", "yield": "kg/ha", "wwp": "kg/m³",
        },
    }
