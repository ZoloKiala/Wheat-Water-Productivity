"""
Created on Thu Aug  3, 2023
Last Modified on Tue Aug 15, 2026

Author: Mulugeta Tadesse
        IWMI East Africa
        mulugeta.tadesse@cgiar.org

"""

# Load required packages
import pandas as pd
import geopandas as gpd
import numpy as np

# ------------------------------------------------------------------------------
# ------------------------Validate input GeoDataFrame---------------------------
# ------------------------------------------------------------------------------
# This function checks the validity of input geodata

def validate_input(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Checks input irrigation scheme geodataframe read using the input Shapefiles.
    Required fields: ID, SOS, EOS, geometry

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Irrigation scheme data in geopandas format.

    Returns
    -------
    gdf : geopandas.GeoDataFrame
        This function returns validated geodataframe.
    """
    # Check if the input date is GeoDataFrame
    if not isinstance(gdf, gpd.GeoDataFrame):
        print('Input must be a GeoPandas GeoDataFrame.')
        return None

    # Required columns
    required_columns = ["ID", "SOS", "EOS", "geometry"]
    missing_columns = [
        col for col in required_columns
        if col not in gdf.columns]
    if missing_columns:
        print(f"Missing required columns: {missing_columns}")
        return None    

    # Copy input
    gdf = gdf.copy()
    try:
        gdf["ID"] = pd.to_numeric(
            gdf["ID"],
            errors="raise"
        ).astype(int)

    except Exception:
        raise ValueError(
            "ID must contain valid integer values."
        )
    # Check missing IDs
    if gdf["ID"].isna().any():
        raise ValueError(
            "ID contains missing values."
        )
    # Check duplicate IDs
    if gdf["ID"].duplicated().any():

        duplicate_ids = (
            gdf.loc[
                gdf["ID"].duplicated(keep=False),
                "ID"
            ]
            .unique()
            .tolist()
        )
        raise ValueError(
            f"ID values must be unique. "
            f"Duplicate IDs found: {duplicate_ids}"
        )
    # Convert SOS and EOS to datetime
    gdf["SOS"] = pd.to_datetime(
        gdf["SOS"],
        errors="coerce"
    )
    gdf["EOS"] = pd.to_datetime(
        gdf["EOS"],
        errors="coerce"
    )

    # Check missing dates
    if gdf["SOS"].isna().any():

        ids = gdf.loc[
            gdf["SOS"].isna(),
            "ID"
        ].tolist()

        raise ValueError(
            f"Invalid or missing SOS for ID(s): {ids}"
        )

    if gdf["EOS"].isna().any():

        ids = gdf.loc[
            gdf["EOS"].isna(),
            "ID"
        ].tolist()

        raise ValueError(
            f"Invalid or missing EOS for ID(s): {ids}"
        )

    # Check date order
    invalid_dates = gdf["EOS"] <= gdf["SOS"]
    if invalid_dates.any():

        ids = gdf.loc[
            invalid_dates,
            "ID"
        ].tolist()

        raise ValueError(
            f"EOS must be later than SOS. "
            f"Check ID(s): {ids}"
        )
    # Check geometry
    if gdf.geometry.isna().any():
        ids = gdf.loc[
            gdf.geometry.isna(),
            "ID"
        ].tolist()

        raise ValueError(
            f"Missing geometry for ID(s): {ids}"
        )

    invalid_geometry = ~gdf.geometry.is_valid

    if invalid_geometry.any():

        ids = gdf.loc[
            invalid_geometry,
            "ID"
        ].tolist()

        raise ValueError(
            f"Invalid geometry for ID(s): {ids}"
        )

    # Check CRS
    if gdf.crs is None:
        raise ValueError(
            "Input data have no coordinate reference system (CRS)."
        )

    # Warn if CRS is not WGS84
    if gdf.crs.to_epsg() != 4326:
        print(
            f"Warning: Input CRS is {gdf.crs}. "
            "The tool expects WGS 84 (EPSG:4326)."
        )
    print("-> Data validation is successful!")

    return gdf

