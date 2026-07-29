"""WaPOR data-access layer and explanatory-feature assembly.

This module produces, for any set of coordinates, the full feature vector the
LightGBM model consumes:

    npp, rainfall, aet, soc, elevation, fertilizer, planting_dekad,
    improved_seed, extension_visits, market_dist

Provider design
---------------
`FeatureProvider` is the interface the analytical engine talks to. The default
implementation, `SyntheticProvider`, generates deterministic, spatially
coherent fields (sums of low/high-frequency harmonics) so the whole pipeline —
retrieval -> feature assembly -> LightGBM inference -> explanation — runs
end-to-end without external services. Swapping in the production WaPOR v3
retrieval (NPP/AET rasters via the FAO GISManager API) and the EthioSIS /
SRTM / survey layers only requires implementing this interface; nothing
downstream changes.
"""

from __future__ import annotations

import numpy as np

FEATURE_NAMES = [
    "npp", "rainfall", "aet", "soc", "elevation",
    "fertilizer", "planting_dekad", "improved_seed",
    "extension_visits", "market_dist",
]

FEATURE_LABELS = {
    "npp": "Seasonal NPP (WaPOR)",
    "rainfall": "Seasonal rainfall",
    "aet": "Actual evapotranspiration",
    "soc": "Soil organic carbon",
    "elevation": "Elevation",
    "fertilizer": "Fertilizer applied (NPS)",
    "planting_dekad": "Planting dekad",
    "improved_seed": "Improved seed use",
    "extension_visits": "Extension visits",
    "market_dist": "Distance to market",
}

FEATURE_UNITS = {
    "npp": "kgC/ha", "rainfall": "mm", "aet": "mm", "soc": "%",
    "elevation": "m", "fertilizer": "kg/ha", "planting_dekad": "dekad",
    "improved_seed": "0/1", "extension_visits": "visits/season",
    "market_dist": "km",
}

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
    the 100 m analysis grid: a high-frequency hash aliases against the grid and
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
    """Deterministic stand-in for WaPOR v3 + ancillary layers."""

    name = "synthetic-v1"

    def assemble(self, lat, lon, system: str, year: str, season: str) -> dict:
        """Return a dict of feature-name -> ndarray (same shape as lat/lon)."""
        lat = np.asarray(lat, dtype=np.float64)
        lon = np.asarray(lon, dtype=np.float64)
        yf = YEAR_FACTOR.get(year, 1.0)
        irrigated = system == "irrigated"

        # Terrain: broad NE-ward descent toward the Afar lowlands + local relief.
        elevation = (
            2450.0 - 210.0 * np.clip(lon - 39.0, 0, None) - 120.0 * np.clip(lat - 9.0, 0, None)
            + 260.0 * _field(lat, lon, 5.3, 4.1, 9.7, 7.9)
        )
        elevation = np.clip(elevation, 350.0, 3200.0)

        f_rain = _field(lat, lon, 13.1, 17.9, 7.3, 5.1, fine=0.25)
        if irrigated:
            rainfall = (90.0 + 45.0 * f_rain) * yf
        elif season == "Belg":
            rainfall = (390.0 + 150.0 * f_rain) * yf
        else:  # Meher
            rainfall = (760.0 + 250.0 * f_rain) * yf
        rainfall = np.clip(rainfall, 20.0, 1400.0)

        f_aet = _field(lat, lon, 23.7, 11.3, 5.9, 8.7, fine=0.2)
        if irrigated:
            aet = 485.0 + 50.0 * f_aet
        else:
            aet = np.minimum(rainfall * 0.58, 430.0) + 45.0 * f_aet
        aet = np.clip(aet, 180.0, 640.0)

        soc = np.clip(1.35 + 0.75 * _field(lat, lon, 31.7, 21.9, 13.3, 9.1, fine=0.3), 0.3, 3.2)

        fertilizer = np.clip(85.0 + 65.0 * _field(lat, lon, 41.3, 27.7, 17.9, 13.7), 0.0, 220.0)

        planting_dekad = np.rint(4.0 + 2.6 * _field(lat, lon, 61.1, 37.3, 23.9, 19.3)).clip(1, 9)

        improved_seed = (_field(lat, lon, 53.9, 43.1, 29.3, 23.1) > 0.12).astype(np.float64)

        extension_visits = np.rint(
            2.4 + 2.0 * _field(lat, lon, 71.3, 51.7, 33.1, 27.7)
        ).clip(0, 6)

        market_dist = np.clip(16.0 + 13.0 * _field(lat, lon, 3.9, 6.7, 47.3, 31.9), 1.5, 45.0)

        # Seasonal NPP: water supply and soil fertility drive true biomass
        # accumulation; the WaPOR-observed NPP feature carries ~9% retrieval
        # noise on top (fine-scale field, uncorrelated with the other layers),
        # as satellite products do — so the model cannot lean on it alone.
        water = 0.55 * aet + 0.20 * rainfall + (185.0 if irrigated else 0.0)
        vigor = 1.0 + 0.26 * _field(lat, lon, 311.0, 271.0, 151.0, 97.0)
        npp_true = np.clip(water * (0.85 + 0.22 * (soc - 0.3) / 2.9) * 2.35 * vigor * yf, 180.0, 1900.0)
        retrieval = 1.0 + 0.09 * _field(lat, lon, 431.7, 389.3, 211.1, 173.9)
        npp = np.clip(npp_true * retrieval, 180.0, 1900.0)

        return {
            "_npp_true": npp_true,
            "npp": npp, "rainfall": rainfall, "aet": aet, "soc": soc,
            "elevation": elevation, "fertilizer": fertilizer,
            "planting_dekad": planting_dekad, "improved_seed": improved_seed,
            "extension_visits": extension_visits, "market_dist": market_dist,
        }

    def true_wwp(self, feats: dict, rng: np.random.Generator | None = None):
        """'Ground truth' process used to label synthetic training samples.

        Yield is driven by NPP with management/terrain modifiers; WWP is grain
        yield per unit of actual evapotranspiration (kg/m3).
        """
        npp = feats.get("_npp_true", feats["npp"])
        mgmt = (
            1.0
            + 0.12 * feats["improved_seed"]
            + 0.14 * np.minimum(feats["fertilizer"], 140.0) / 140.0
            + 0.035 * feats["extension_visits"]
            - 0.055 * np.abs(feats["planting_dekad"] - 4.0)
            - 0.0045 * feats["market_dist"]
        )
        # Elevation optimum around 2,300 m for highland wheat.
        elev_pen = np.clip(1.0 - ((feats["elevation"] - 2300.0) / 1700.0) ** 2, 0.45, 1.0)
        soc_lift = 1.0 + 0.16 * (feats["soc"] - 1.3)
        # Water supply acts on yield both through NPP (biomass accumulation)
        # and directly (grain filling), so rainfall/AET carry signal of their
        # own — as in the field data — rather than being fully mediated by NPP.
        water_equiv = (0.55 * feats["aet"] + 0.30 * feats["rainfall"]) * 0.0072
        # Irrigation lets growers time water to the critical growth stages, so
        # each mm of ET converts to grain more efficiently than under rainfed
        # supply — the standard justification for its higher water productivity.
        reliability = 1.0 + 0.18 * (feats["rainfall"] < 200.0)
        yield_t_ha = (
            (0.60 * npp * 0.00335 + 0.40 * water_equiv) * mgmt * elev_pen * soc_lift * reliability
        )
        wwp = yield_t_ha * 100.0 / feats["aet"] * 1.32  # kg grain per m3 of ET water
        if rng is not None:
            wwp = wwp + rng.normal(0.0, 0.045, size=np.shape(wwp))
        return np.clip(wwp, 0.15, 2.2)


PROVIDER = SyntheticProvider()


def feature_matrix(feats: dict) -> np.ndarray:
    """Stack a feature dict into the (n, 10) matrix the model expects."""
    return np.column_stack([np.ravel(feats[name]) for name in FEATURE_NAMES])
