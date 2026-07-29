"""Administrative units used for AOI selection.

Region -> Zone -> Woreda hierarchy. Each woreda carries a centroid and a
half-extent (degrees) that defines its analysis rectangle. In production this
module is replaced by the CSA administrative boundary layer served from the
spatial database (PostGIS); the structure of the API response stays the same.
"""

ADMIN = {
    "Oromia": {
        "Arsi": {
            "Hetosa":       {"c": [8.13, 39.24], "d": 0.14},
            "Tiyo":         {"c": [7.96, 39.10], "d": 0.13},
            "Digelu Tijo":  {"c": [7.84, 39.20], "d": 0.14},
            "Lemu Bilbilo": {"c": [7.62, 39.22], "d": 0.16},
            "Munesa":       {"c": [7.55, 38.95], "d": 0.16},
        },
        "West Arsi": {
            "Kofele": {"c": [7.07, 38.78], "d": 0.14},
            "Dodola": {"c": [6.98, 39.18], "d": 0.16},
            "Adaba":  {"c": [7.01, 39.40], "d": 0.17},
        },
        "Bale": {
            "Sinana": {"c": [7.10, 40.22], "d": 0.15},
            "Agarfa": {"c": [7.28, 39.82], "d": 0.15},
            "Goba":   {"c": [7.01, 39.98], "d": 0.14},
        },
        "East Shewa": {
            "Ada'a": {"c": [8.72, 38.98], "d": 0.14},
            "Lume":  {"c": [8.60, 39.20], "d": 0.13},
        },
    },
    "Amhara": {
        "North Shewa": {
            "Basona Werana": {"c": [9.65, 39.45], "d": 0.15},
            "Angolela Tera": {"c": [9.45, 39.42], "d": 0.14},
        },
        "South Wollo": {
            "Dessie Zuria": {"c": [11.05, 39.60], "d": 0.14},
            "Kutaber":      {"c": [11.22, 39.53], "d": 0.13},
        },
    },
    "Afar": {
        "Gabi Rasu (Zone 3)": {
            "Amibara": {"c": [9.33, 40.17], "d": 0.15},
            "Gewane":  {"c": [10.17, 40.64], "d": 0.16},
        },
        "Awsi Rasu (Zone 1)": {
            "Dubti": {"c": [11.73, 41.08], "d": 0.15},
        },
    },
}


def get_woreda(region: str, zone: str, woreda: str):
    try:
        return ADMIN[region][zone][woreda]
    except KeyError:
        return None


def as_tree():
    """Nested {region: {zone: [woreda, ...]}} for the frontend selectors."""
    return {
        region: {zone: sorted(woredas.keys()) for zone, woredas in zones.items()}
        for region, zones in ADMIN.items()
    }
