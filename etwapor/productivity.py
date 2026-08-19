"""

Created on Wed August 29 2026

Author: Mulugeta Tadesse
        IWMI East Africa
        mulugeta.tadesse@cgiar.org

"""
# Load necessary packages
import pandas as pd
import geopandas as gpd
import numpy as np
import xarray as xr
from rasterio.enums import Resampling
from . import metadata, data, download
from tqdm.auto import tqdm

# Get wheat data parameters
wheat = data.wheat


def compute_seasonal_biomass(da: xr.DataArray) -> xr.DataArray:
    """
       Calculate seasonal biomass from decadal net primary 
       production (NPP).

        Parameters:
        -----------
        da: xr.DataArray
            WaPOR NPP data in DataArray format.

        Returns
        -------
        da: xarray.DataArray
            Returns seasonal NPP in xarray DataArray format.
    """
    # Compute seasonal biomass
    npp_seasonal = da.sum(dim='time',
                          keep_attrs=True,
                          min_count=6,
                          skipna=True)
    # Define spatial reference system
    npp_seasonal.rio.write_crs('epsg:4326', inplace=True)
    return npp_seasonal


def compute_yield(da: xr.DataArray, cp: dict = wheat) -> xr.DataArray:
    """
       Calculate yield from seasonal NPP in kg/ha.

        Parameters:
        -----------
        da: xr.DataArray
            WaPOR seasonal NPP data in DataArray format.
        cp: dict
            dictionary of crop parameters

        Returns
        -------
        da: xarray.DataArray
            Returns productivity in xarray DataArray format.
    """
    # Compute total boimass in kg/ha.
    tbiomass = (cp['AOT'] * cp['fc'] * da * 22.222)/(1 - cp['mc'])
    # Compute yield
    wheat_yield = tbiomass * cp['hi']
    # Add metadata attributes to yield
    wheat_yield.attrs = metadata.variable_attrs['yield']
    return wheat_yield


def resample_2D_array(da: xr.DataArray, upscale_factor: int = 5) -> xr.DataArray:
    """
    Resample input DataArray based on provided upscaling factor.

        Parameters:
        -----------
        da: xr.DataArray
            WaPOR seasonal NPP data in DataArray format.
        upscale_factor: int
            Scaling factor to be used

        Returns
        -------
        da: xarray.DataArray
            Returns resampled xarray DataArray.

        E.g. The choise of the default scaling factor value is arbitrary.
             With this factor, the 2D array will have nearly 20 meters 
             resolution for L2 WaPOR data.
    """

    # Use scaling factor to change the width and height of the
    # new Dataset
    new_width = da.rio.width * upscale_factor
    new_height = da.rio.height * upscale_factor
    # Check if the input DataArray misses CRS
    if da.rio.crs == None:
        da.rio.write_crs('epsg:4326', inplace=True)

    # Use reproject to change the grid resolution of the array
    da = da.rio.reproject(
        da.rio.crs,
        shape=(new_height, new_width),
        resampling=Resampling.nearest,
        keep_attrs=True
    )
    return da

def process_single_feature( 
        gdfs: gpd.GeoDataFrame, 
        scheme_code: str | None = None, 
        storage: str = "google", 
        npp_mapset: str = "L2-NPP-D", 
        aeti_mapset: str = "L2-AETI-D", 
    ) -> dict[str, float | int]:
    
    """
    Process a single spatial feature and estimate wheat productivity 
    using WaPOR NPP and AETI data. 
    
    The function retrieves seasonal WaPOR NPP and AETI data for the 
    specified growing period, calculates seasonal NPP and AETI, 
    estimates wheat yield from NPP-derived biomass, and calculates 
    crop water productivity.

    Parameters 
    ---------- 
    gdfs : geopandas.GeoDataFrame 
        GeoDataFrame containing the spatial feature to be processed. 
        The GeoDataFrame must contain the following columns: 
            - SOS: Start of Season (planting date). 
            - EOS: End of Season (harvesting date). 
            - geometry: Spatial geometry used to extract WaPOR data. 
        The function uses the first record in the GeoDataFrame to 
        obtain the SOS and EOS dates. 
    
    scheme_code : str or None, optional 
        WaPOR irrigation scheme code, if applicable. 
            Examples: - KOG: for Koga irrigation scheme. 
                      - "AWH": for Awash irrigation scheme. 
                      - None:  for other areas or schemes without a specific 
                               WaPOR scheme code. 
            Default is None. 
    storage : str, optional 
        Storage backend used by WaPORDownload. 
            Default is "google". 
    npp_mapset : str, optional 
        WaPOR mapset code used to retrieve Net Primary Production (NPP) data. 
        "L2-NPP-D" represents the decadal NPP product. 
            Default is "L2-NPP-D". 
    aeti_mapset : str, optional 
        WaPOR mapset code used to retrieve Actual Evapotranspiration 
        and Interception (AETI) data. "L2-AETI-D" represents the decadal 
        AETI product. 
            Default is "L2-AETI-D".
    
    Returns 
    ------- 
        dict[str, float | int] 
        Dictionary containing the estimated productivity indicators: 
            - NPP: Mean seasonal net primary production. 
            - EYield_tpha: Estimated wheat yield in tonnes per hectare. 
            - AETI_mm: Mean seasonal actual evapotranspiration and 
                       interception in millimetres. 
            - WP_kgpm3: Crop water productivity in kilograms per cubic metre. 
            - LGP: Length of the growing period in days.
    """

    # Get dates
    SOS = gdfs["SOS"].iloc[0]
    EOS = gdfs["EOS"].iloc[0]

    # Create download object using start and end season dates
    wapor_npp = download.WaPORDownload(
        sos=SOS,
        eos=EOS,
        mapset_code=npp_mapset,
        scheme_code=scheme_code,
        storage=storage
    )
    # Build URL list
    npp_urls = wapor_npp.get_url_list()
    if not npp_urls:
        raise ValueError(
            "No NPP data available."
        )
    
    # Download data
    ds_npp = wapor_npp.download_WaPOR_data(
        npp_urls,
        gdfs
    )
    if not hasattr(ds_npp, "NPP"):
        raise ValueError(
            "NPP variable not found."
        )

    # Compute seasonal NPP
    npp_seasonal = (
        compute_seasonal_biomass(
            ds_npp.NPP
        )
    )

    # Get mean NPP values for a field/plot
    npp_value = float(
        np.asarray(
            npp_seasonal.mean().values
        ).squeeze()
    )
    if np.isnan(npp_value):
        print("Warning: NPP result contains NaN!")

    # Compute wheat yield
    wheat_yield = (
        compute_yield(
            npp_seasonal
        ) / 1000
    )
    # Get mean yield value
    yield_value = float(
        np.asarray(
            wheat_yield.mean()
        ).squeeze()
    )
    if np.isnan(yield_value):
        print("Warning: Wheat yield result contains NaN!")

    # Create download object using start and end season dates
    wapor_aeti = download.WaPORDownload(
        sos=SOS,
        eos=EOS,
        mapset_code=aeti_mapset,
        scheme_code=scheme_code,
        storage=storage
    )
    # Buil URL list for AETI
    aeti_urls = wapor_aeti.get_url_list()
    if not aeti_urls:
        raise ValueError(
            "No AETI data available."
        )
    
    # Download AETI
    ds_aeti = wapor_aeti.download_WaPOR_data(
        aeti_urls,
        gdfs
    )
    if not hasattr(ds_aeti, "AETI"):
        raise ValueError(
            "AETI variable not found."
        )

    # Seasonal AETI
    aeti_seasonal = (
        compute_seasonal_biomass(
            ds_aeti.AETI
        )
    )
    # Compute mean value
    aeti_value = float(
        np.asarray(
            aeti_seasonal.mean().values
        ).squeeze()
    )
    if np.isnan(aeti_value):
        print("Warning: AETI result contains NaN!")

    # Water pproductivity
    wp_value = yield_value * 100/ aeti_value
    
    # Length of growing period
    lgp = (EOS - SOS).days

    return {
        "NPP": npp_value,
        "EYield_tpha": yield_value,
        "AETI_mm": aeti_value,
        "WP_kgpm3": wp_value,
        "LGP": lgp
    }

def estimate_wheat_wp(
        gdf: gpd.GeoDataFrame,
        scheme_code: str | None = None,
        storage: str = "google",
        npp_mapset: str = "L2-NPP-D",
        aeti_mapset: str = "L2-AETI-D",
        show_progress: bool = True,
    ) -> gpd.GeoDataFrame:
    
    """
    Estimate seasonal wheat yield and crop water productivity
    using WaPOR NPP and AETI data.

    The function processes each spatial feature independently based
    on its unique ID. For each feature, the growing season is
    defined by the SOS (Start of Season) and EOS (End of
    Season) attributes. WaPOR NPP and AETI data are then downloaded
    for the specified growing season and used to estimate wheat
    yield and crop water productivity.

    The function supports both point and polygon geometries. For
    polygon features, WaPOR data are extracted over the polygon and
    the resulting seasonal values are summarized for the feature.
    For point features, the calculation is based on the WaPOR data
    corresponding to the point location.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Input GeoDataFrame containing the spatial features to be
        processed. The following columns are required:

        - ID: Unique integer identifier for each feature.
        - SOS: Start of Season (planting date).
        - EOS: End of Season (harvesting date).
        - geometry: Point or polygon geometry.

        The input GeoDataFrame should have a valid coordinate
        reference system, preferably WGS 84 (EPSG:4326).

    scheme_code : str or None, optional
        WaPOR irrigation scheme code, where applicable.
        Use:

        - "KOG" for Koga irrigation scheme.
        - "AWH" for Awash irrigation scheme.
        - None for areas without a specific WaPOR scheme code.

        Default is None.

    storage : str, optional
        Storage backend used by the WaPORDownload class.

        Default is "google".

    npp_mapset : str, optional
        WaPOR mapset code used for retrieving decadal Net Primary
        Production (NPP) data.

        Default is "L2-NPP-D" (decadal NPP).

    aeti_mapset : str, optional
        WaPOR mapset code used for retrieving Actual
        Evapotranspiration and Interception (AETI) data.

        Default is "L2-AETI-D" (decadal AETI).

    show_progress : bool, optional
        If True, display a progress bar showing the processing
        status for each feature. The progress bar is updated in
        place using tqdm.

        Default is True.

    Returns
    -------
    geopandas.GeoDataFrame
        A copy of the input GeoDataFrame containing the original
        attributes and geometry, together with the following
        calculated fields:

        NPP
            Mean seasonal Net Primary Production derived from WaPOR.

        EYield_tpha
            Estimated wheat yield in tonnes per hectare (t/ha).

        AETI_mm
            Mean seasonal Actual Evapotranspiration and Interception
            in millimetres (mm).

        WP_kgpm3
            Crop water productivity in kilograms per cubic metre
            (kg/m³).

        LGP
            Length of the growing period in days, calculated as the
            difference between EOS and SOS.
    """

    # Initialize output fields
    gdf["NPP"] = np.nan
    gdf["EYield_tpha"] = np.nan
    gdf["AETI_mm"] = np.nan
    gdf["WP_kgpm3"] = np.nan

    # Get unique ID
    ids = gdf["ID"].unique()
    iterator = ids
    # Process features  
    if show_progress:
        iterator = tqdm(
            ids,
            total=len(ids),
            desc="Processing features",
            unit="feature",
            dynamic_ncols=True
        )

    for feature_id in iterator:
        # Select feature
        mask = gdf["ID"] == feature_id
        gdfs = gdf.loc[mask].copy()
        # Process feature
        results = process_single_feature(
                gdfs=gdfs,
                scheme_code=scheme_code,
                storage=storage,
                npp_mapset=npp_mapset,
                aeti_mapset=aeti_mapset
            )
        # Store results
        gdf.loc[mask,"NPP"] = results["NPP"]
        gdf.loc[mask,"EYield_tpha"] = results["EYield_tpha"]
        gdf.loc[ mask,"AETI_mm"] = results["AETI_mm"]
        gdf.loc[mask,"WP_kgpm3"] = results["WP_kgpm3"]
        gdf.loc[mask,"LGP"] = results["LGP"]

        # Update progress description
        if show_progress:
            iterator.set_postfix(
                    ID=feature_id,
                    Yield=f"{results['EYield_tpha']:.2f}",
                    CWP=f"{results['WP_kgpm3']:.2f}"
                )
    return gdf
