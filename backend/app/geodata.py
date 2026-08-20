"""WaPOR data-access layer: seasonal NPP and AETI for a set of coordinates.

The WWPT method (see ``wwpt.py``) consumes exactly two seasonal rasters, so
that — and not a wide feature vector — is what a provider has to supply:

    npp     seasonal net primary production, gC/m²/season
    aeti    seasonal actual evapotranspiration and interception, mm/season

Provider design
---------------
Two implementations satisfy the same interface:

``WaporProvider`` (``wapor.py``)
    The real thing: FAO WaPOR v3 dekadal rasters summed over the season window.
    Needs network access and ``rasterio``. Select with ``WWP_PROVIDER=wapor``.

``SyntheticProvider`` (below)
    Deterministic, spatially coherent stand-in so the dashboard runs end to end
    without external services. It is the default, and every result it produces
    is flagged ``synthetic: true`` all the way to the interface — a number the
    user cannot tell apart from real WaPOR output is worse than no number.
"""

from __future__ import annotations

import os

import numpy as np

# Inter-annual production factor (drought years score below 1).
YEAR_FACTOR = {
    "2025/26": 1.04, "2024/25": 1.00, "2023/24": 0.95,
    "2022/23": 0.90, "2021/22": 0.86,
}

SEASONS_RAINFED = ["Meher", "Belg"]
SEASON_IRRIGATED = "Dry season (Nov–Mar)"
YEARS = list(YEAR_FACTOR.keys())


def _field(lat, lon, a, b, c, d, fine=0.0):
    """Smooth pseudo-random field in roughly [-1, 1], deterministic in space."""
    v = np.sin(lat * a + lon * b) * 0.6 + np.sin(lat * c - lon * d) * 0.4
    if fine:
        v = v + np.sin(lat * 151.0 + lon * 97.0) * fine
    return v


WHEAT_MASK_THRESHOLD = -0.92


def wheat_mask(lat, lon):
    """Boolean wheat-area mask covering ~82% of cells.

    Frequencies are deliberately low enough that the patches stay coherent at
    the analysis grid: a high-frequency hash aliases against the grid and
    renders as a diagonal moiré, which reads as a display artefact rather than
    as field boundaries.
    """
    m = (
        np.sin(lat * 47.0 + lon * 39.0)
        + 0.65 * np.sin(lat * 83.0 - lon * 61.0)
        + 0.45 * np.sin(lat * 29.0 + lon * 97.0)
    )
    return m >= WHEAT_MASK_THRESHOLD


class SyntheticProvider:
    """Deterministic stand-in for WaPOR v3 seasonal NPP and AETI.

    Rather than inventing NPP and WWP independently, this generates seasonal
    water consumption (AETI) and a biomass water-use efficiency (gC/m² per mm),
    then multiplies them. NPP and AETI therefore vary together the way they do
    in the real products rather than independently, and the resulting values land
    in the range the reference notebook observed over the 2026 Ethiopian
    irrigation schemes (NPP 65–560 gC/m², AETI 190–460 mm).
    """

    name = "synthetic-v2"
    synthetic = True
    # Matches the real provider's native resolution (WaPOR v3 L2, 100 m) so the
    # demonstration mode cannot overstate the detail the tool would deliver.
    resolution_m = 100

    def assemble(self, lat, lon, system: str, year: str, season: str) -> dict:
        from .wwpt import season_window  # local import: avoids a cycle

        sos, eos = season_window(system, year, season)
        npp, aeti = self._fields(lat, lon, system, season, YEAR_FACTOR.get(year, 1.0))
        return {"npp": npp, "aeti": aeti, "sos": sos, "eos": eos, "n_dekads": None}

    def assemble_window(self, lat, lon, sos, eos) -> dict:
        """Seasonal totals for an explicit [SOS, EOS] window.

        This is the notebook's mode: every feature carries its own growing
        season, so the totals scale with the length of that window rather than
        with a season label. Mirrors ``WaporProvider.assemble_window`` so both
        providers drive the scheme analysis unchanged.
        """
        system, season = self._season_of(sos)
        label = f"{sos.year}/{str(sos.year + 1)[2:]}" if sos.month >= 6 else                 f"{sos.year - 1}/{str(sos.year)[2:]}"
        npp, aeti = self._fields(lat, lon, system, season, YEAR_FACTOR.get(label, 1.0))
        # WaPOR sums dekadal rasters, so a longer season accumulates more of
        # both quantities. Scaled against a 120-day reference season and
        # bounded so an unusual window cannot produce impossible totals.
        scale = float(np.clip((eos - sos).days / 120.0, 0.6, 1.4))
        return {"npp": np.clip(npp * scale, 45.0, 620.0),
                "aeti": np.clip(aeti * scale, 180.0, 470.0),
                "sos": sos, "eos": eos, "n_dekads": None}

    @staticmethod
    def _season_of(sos) -> tuple:
        """Production system and season implied by a start-of-season date."""
        if sos.month in (10, 11, 12, 1):
            return "irrigated", SEASON_IRRIGATED
        return ("rainfed", "Belg") if sos.month in (2, 3, 4, 5) else ("rainfed", "Meher")

    def _fields(self, lat, lon, system: str, season: str, yf: float) -> tuple:
        lat = np.asarray(lat, dtype=np.float64)
        lon = np.asarray(lon, dtype=np.float64)

        # Seasonal water consumption. Irrigated schemes apply water to demand,
        # so they consume more and vary less than rainfed fields.
        f_aeti = _field(lat, lon, 23.7, 11.3, 5.9, 8.7, fine=0.2)
        if system == "irrigated":
            aeti = 345.0 + 70.0 * f_aeti
        elif season == "Belg":
            aeti = (250.0 + 65.0 * f_aeti) * yf
        else:  # Meher
            aeti = (305.0 + 85.0 * f_aeti) * yf
        aeti = np.clip(aeti, 180.0, 470.0)

        # Biomass water-use efficiency: how much carbon each mm of consumed
        # water buys. Driven by soil fertility and crop vigour, and higher under
        # irrigation, where water can be timed to the critical growth stages.
        soil = _field(lat, lon, 31.7, 21.9, 13.3, 9.1, fine=0.3)
        vigor = _field(lat, lon, 311.0, 271.0, 151.0, 97.0)
        eff = 0.82 * yf * (1.0 + 0.30 * soil + 0.22 * vigor)
        if system == "irrigated":
            eff *= 1.15
        eff = np.clip(eff, 0.33, 1.62)

        return np.clip(aeti * eff, 45.0, 620.0), aeti


def _select_provider():
    """Provider chosen by ``WWP_PROVIDER`` (``synthetic`` by default).

    Constructing the WaPOR provider does no network I/O, so an unreachable
    catalogue surfaces as a clear error on the first analysis rather than
    preventing the service from starting.
    """
    choice = os.environ.get("WWP_PROVIDER", "synthetic").strip().lower()
    if choice in ("synthetic", ""):
        return SyntheticProvider()
    if choice == "wapor":
        from .wapor import WaporProvider

        return WaporProvider()
    raise ValueError(
        f"Unknown WWP_PROVIDER '{choice}'. Expected 'synthetic' or 'wapor'."
    )


PROVIDER = _select_provider()
