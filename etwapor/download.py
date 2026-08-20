"""

Created on Wed June 12 15:00 2025
Last Modified on Tue July 24 2026

Author: Mulugeta Tadesse
        IWMI East Africa
        mulugeta.tadesse@cgiar.org

"""

# Load required python libraries
import pandas as pd
from pandas.tseries.frequencies import to_offset
import geopandas as gpd
import numpy as np
from typing import List, Tuple, Union
import calendar
from . import metadata
import xarray as xr
import rioxarray as rxr
from tqdm.auto import tqdm
import requests


class WaPORDownload:
    """

    This class is used to get WaPORv3 data products from cloud storages.
    There are three options to get WaPORv3 data products:
        1. from FAO GISMGR file explorer (storage = 'gismgr'),
        2. from FAO Google Cloud Storage (storage = 'google') - default, and
        3. through FAO WaPORv3 API (storage = 'api')
    The actual data is not downloaded and stored on the local drive.
    Rather data on the cloud storage is clipped using geographic area
    of interest, converted to xarray DataArray format, and then stored in
    memory.

    """

    def __init__(self,
                 sos: str,
                 eos: str,
                 scheme_code=None,
                 mapset_code: str = None,
                 storage: str = 'google'):
        self.sos = sos
        self.eos = eos
        self.scheme_code = scheme_code
        self.mapset_code = mapset_code
        self.storage = storage
        # URL links
        self._google_url = 'https://storage.googleapis.com/fao-gismgr-wapor-3-data/DATA/WAPOR-3/'
        self._gismgr_url = 'https://gismgr.fao.org/DATA/WAPOR-3/'
        self._api_url = "https://data.apps.fao.org/gismgr/api/v2/catalog/workspaces/WAPOR-3/"

    def get_url_list(self) -> List[tuple]:
        """

        Main calling method to return a list of URLs depending on the storage
        type selected.

        Returns
        -------
        url_list: list
            Returns list of tuples of string values. The first element is the
            date string corresponding to the end date of a decade and the
            second element is the full path url of WaPOR data file.

        """
        # Based on mapset code, get data timestep (daily, decadal, monthly or annual)
        data_timestep = self.mapset_code.split('-')[2]

        if self.storage == 'google':
            # Builds list of urls from google cloud storage
            google_url_list = self._get_storage_url(
                self._google_url, data_timestep)
            return google_url_list
        elif self.storage == 'gismgr':
            # Builds list of urls from FAO GISMGR file explorer
            fao_url_list = self._get_storage_url(
                self._gismgr_url, data_timestep)
            return fao_url_list
        elif self.storage == 'api':
            # Builds list of urls through WaPORv3 API
            api_url_list = self._get_api_url()
            return api_url_list
        else:
            print('Please provide correct value: enter google, gismgr or api!')

    def _get_storage_url(self, url_main: str, timestep: str) -> List[Tuple[str, str]]:
        """

        Build url list of GeoTiff data files residing on Google storage or
        FAO GISMGR storage. The urls are locally generated depending on the
        way they are stored on the cloud storages, but the existance of a
        file has to be checked before applying any analysis methods.

        Parameters:
        -----------
        url_main: str
            Base url of the WaPORv3 mapsets

        Returns
        -------
        url_list: list
            Returns list of tuples of string values. The first element is the
            date string corresponding to the end date of a decade and the
            second element is the full path url of WaPOR data file.

        """

        # Data extension
        ext = '.tif'

        # Create list of dates in the range between SOS and EOS
        date_list = self._get_date_range()

        # Create empty set of dates
        date_set = set()

        # Get temporal resolution
        timestep = self.mapset_code.split('-')[2]

        # Iterate over the list of dates and identify urls having dates
        # between SOS and EOS
        for date in date_list:
            if timestep == 'D':
                # First decade
                if date.day < 11:
                    # Get date in string format
                    date_str = pd.Timestamp(date).strftime('%Y-%m-%d')
                    # Build full path of decadal GeoTiff data
                    if self.scheme_code is None:
                        url = f'{url_main}MAPSET/{self.mapset_code}/WAPOR-3.{self.mapset_code}.{date_str[0:8]}D1{ext}'
                    else:
                        url = f'{url_main}MOSAICSET/{self.mapset_code}/WAPOR-3.{self.mapset_code}.{self.scheme_code}.{date_str[0:8]}D1{ext}'
                    # Set decade end date
                    decade_start_date = date_str[0:8] + '01'
                    # Create tuple of decade start date and data full path
                    date_url_tuple = (decade_start_date, url)
                    # Add result to a set. Note that if duplicate
                    # tuple is added to the set, it will be discarded.
                    date_set.add(date_url_tuple)

                # Second decade
                elif date.day > 10 and date.day < 21:
                    date_str = pd.Timestamp(date).strftime('%Y-%m-%d')
                    if self.scheme_code is None:
                        url = f'{url_main}MAPSET/{self.mapset_code}/WAPOR-3.{self.mapset_code}.{date_str[0:8]}D1{ext}'
                    else:
                        url = f'{url_main}MOSAICSET/{self.mapset_code}/WAPOR-3.{self.mapset_code}.{self.scheme_code}.{date_str[0:8]}D1{ext}'
                    decade_start_date = date_str[0:8] + '11'
                    date_url_tuple = (decade_start_date, url)
                    date_set.add(date_url_tuple)

                # Third decade
                else:
                    date_str = pd.Timestamp(date).strftime('%Y-%m-%d')
                    if self.scheme_code is None:
                        url = f'{url_main}MAPSET/{self.mapset_code}/WAPOR-3.{self.mapset_code}.{date_str[0:8]}D1{ext}'
                    else:
                        url = f'{url_main}MOSAICSET/{self.mapset_code}/WAPOR-3.{self.mapset_code}.{self.scheme_code}.{date_str[0:8]}D1{ext}'
                    # Get end date of a month which is also end date of a decade
                    _, start_day = calendar.monthrange(date.year, date.month)
                    decade_start_date = pd.Timestamp(
                        date.year, date.month, 21)
                    decade_start_date = decade_start_date.strftime('%Y-%m-%d')
                    date_url_tuple = (decade_start_date, url)
                    date_set.add(date_url_tuple)
            elif timestep == 'A':
                date_str = pd.Timestamp(date).strftime('%Y-%m-%d')
                if self.scheme_code is None:
                    url = f'{url_main}MAPSET/{self.mapset_code}/WAPOR-3.{self.mapset_code}.{date_str[0:8]}D1{ext}'
                else:
                    url = f'{url_main}MOSAICSET/{self.mapset_code}/WAPOR-3.{self.mapset_code}.{self.scheme_code}.{date_str[0:8]}D1{ext}'
                year_end_date = str(date.year) + '-12-31'
                date_url_tuple = (year_end_date, url)
                date_set.add(date_url_tuple)
            elif timestep == 'E':
                date_str = pd.Timestamp(date).strftime('%Y-%m-%d')
                if self.scheme_code is None:
                    url = f'{url_main}MAPSET/{self.mapset_code}/WAPOR-3.{self.mapset_code}.{date_str[0:8]}D1{ext}'
                else:
                    url = f'{url_main}MOSAICSET/{self.mapset_code}/WAPOR-3.{self.mapset_code}.{self.scheme_code}.{date_str[0:8]}D1{ext}'
                date_url_tuple = (date_str, url)
                date_set.add(date_url_tuple)
            elif timestep == 'M':
                date_str = pd.Timestamp(date).strftime('%Y-%m-%d')
                if self.scheme_code is None:
                    url = f'{url_main}MAPSET/{self.mapset_code}/WAPOR-3.{self.mapset_code}.{date_str[0:8]}D1{ext}'
                else:
                    url = f'{url_main}MOSAICSET/{self.mapset_code}/WAPOR-3.{self.mapset_code}.{self.scheme_code}.{date_str[0:8]}D1{ext}'
                date_url_tuple = (date_str, url)
                date_set.add(date_url_tuple)
            else:
                print('Wrong mapset code entered!')
                return None

        # Convert set to list
        url_list = list(date_set)
        # Sort the list
        url_list.sort()
        return url_list

    def _build_time_df(self,
                       url_list: List[Tuple[str, str]]) -> pd.DataFrame:
        """
        Helper method to build a pandas DataFrame and computes the number
        of days in a given decade.

        Parameters:
        -----------
        url_list: List[tuple]
            List of tuples of end dacade date and url full path of data files.

        Returns
        -------
        df: pandas.DataFrame
            Returns pandas DataFrame containing two fields: time and no of days.

        """

        # Build list of decade start dates
        decade_date_list = [date[0] for date in url_list]
        # Create DataFrame of dates
        df = pd.DataFrame({'time': decade_date_list})
        # Compute number of days in each decade using start of season
        # and end of season
        df = self._compute_days(df, self.sos, self.eos)
        return df

    def _get_date_range(self) -> str:
        date_list = pd.date_range(self.sos, self.eos)
        return date_list

    def download_WaPOR_data(self,
                            url_list: List[Tuple[str, str]],
                            gdf: gpd.GeoDataFrame,
                            counter: object = None,
                            total_rec: np.int64 = None
                            ) -> xr.DataArray:
        """
        Using list of URLs, individual GeoTiff files are accessed and clipped
        to the area of interest and then converted to xarray DataArray on-the-fly.
        The DataArrays are then mereged along time dimension.

        Parameters:
        -----------
        url_list: List[Tuple[str, str]]
            List of tuples of end dacade date and url full path of data files.
        gdf: geopandas.GeoDataFrame
            GeoDataFrame containing geographic area of interest.

        Returns
        -------
        da: xarray.DataArray
            Returns downloaded WaPOR data in xarray DataArray format.
        """
        # Check geometry type

        # Empty list for holding xarray DataArray
        da_list = list()
        # Create dataframe containing number of days in a decade
        df = self._build_time_df(url_list)
        # print('Downloading and converting GeoTiff to DataArray...', end="\r")
        #if counter is not None:
        #    progress_cnt = f'Progress {str(counter)} of {total_rec}'
        #else:
        #    progress_cnt = 'Progress'
        # Iterate through the list of URLs and download data
        for _url in url_list:
            # Get decade end date
            time = _url[0]
            # GeoTiff data url
            url = _url[1]
            # Get number of days in a decade
            ndays = df.query('time==@time')['ndays'].values[0]
            # Read GeoTiff data on cloud storage as DataArray
            with rxr.open_rasterio(url) as src:
                # Clip to geographic are of interest
                if all(gdf.geometry.geom_type == "Point"):
                    point = gdf.geometry.iloc[0]
                    da = src.sel(
                        x=point.x,
                        y=point.y,
                        method='nearest'
                    ).squeeze('band', drop=True)
                else:
                    da = src.rio.clip(gdf.geometry, gdf.crs,
                                      from_disk=True).squeeze('band', drop=True)
                # Add time dimension
                da = da.expand_dims({'time': [pd.to_datetime(time)]})
                # Get scale factor for data attribute
                scale_factor = da.attrs['scale_factor']
                # Mask out nodata values
                da = xr.where(da < 0, np.nan, da)
                # Multiply scale factor and number of days to get decadal
                # values
                da = da * scale_factor * ndays
                da = da.astype(np.float32).rio.write_crs('epsg:4326')

            da_list.append(da)
        # Merge all DataArrays in the list along time dimension
        da = xr.concat(da_list, dim='time')
        # Create xarray Dataset and add documentation
        ds = self.write_metadata(da, self.mapset_code)
        # ds = ds.rio.write_crs('epsg:4326')
        return ds

    def write_metadata(self,
                       da: xr.DataArray,
                       mapset_code: str) -> xr.Dataset:
        """
        Create xarray Dataset and write Dataset level metadata. It first
        creates empty Datset and then adds data variables to it and then
        writes global and coordinate attributes.

        Parameters:
        -----------
        da: xr.DataArray
            WaPOR data in DataArray format.
        mapset_code: str
            Mapse code of WaPORv3 data.

        Returns
        -------
        ds: xarray.Dataset
            Returns xarray Dataset with added metadata.
        """

        # Create empty dataset
        ds = xr.Dataset()
        # Add metadata to the dataset
        ds.attrs = metadata.global_attrs['attrs']
        # Get varaible name
        _, var_name, _ = mapset_code.split('-')
        # Add metadata to the inpute DataArray
        da.attrs = metadata.variable_attrs[var_name.upper()]
        # Add variable to the Dataset
        ds[var_name] = da
        # Add global attributes
        ds.x.attrs = metadata.global_attrs['lon']
        ds.y.attrs = metadata.global_attrs['lat']
        ds.time.attrs = metadata.global_attrs['time']
        # Rename coordinates
        ds = ds.rename({'y': 'lat', 'x': 'lon'})
        return ds

    # ------------------------------------------------------------------------------
    # ---------------Write downloaded data to disk as NetCDF file format------------
    # ------------------------------------------------------------------------------
    # This function writes downloaded data to disk as NetCDF file format.

    def _get_api_url(self) -> list:
        """
        This function requests WaPOR v3 database server and returns list of
        GeoTiff files based on user input of base URL, product name, start date
        and end date.

        Parameters
        ----------
        url: str
            WaPOR v3 mapsets product url.
        start_date: str
            Season start date as string (e.g. '2023-02-05')
        end_date: str
            Season end date as string (e.g. '2023-06-20')

        Returns
        -------
        output: list
            The function returns tuple of date and url.
        """
        product_url = f"{self._api_url}/{self.mapset_code}/rasters"
        data = {"links": [{"rel": "next", "href": product_url}]}
        output = list()
        time = list()

        # Create list of dates between start and end dates
        # Create a pandas Timestamp object
        sdate = pd.to_datetime(self.eos)
        new_date = sdate + pd.Timedelta(days=10)
        date_range = pd.date_range(self.sos, new_date)

        while "next" in [x["rel"] for x in data["links"]]:
            url_ = [x["href"] for x in data["links"] if x["rel"] == "next"][0]
            response = requests.get(url_)
            if response.status_code != 200:
                print('Product not found!')
                continue
            response.raise_for_status()
            data = response.json()["response"]
            for item in data['items']:
                item_date = item['dimensions'][0]['member']['endDate']
                decade_date = item['dimensions'][0]['member']['startDate']
                # Check the date item date if it is in a range
                if item_date in date_range:
                    time.append(decade_date)
                    output.append((decade_date, item['downloadUrl']))
        if isinstance(output, list):
            output = sorted(output)
            # print('Data URL list creation completed!', end="\r")
        return output

    # ------------------------------------------------------------------------------
    # ------------Compute days in a decade during length of growing period----------
    # ------------------------------------------------------------------------------
    # This function computes number of days in each decade

    def _compute_days(self,
                      df: pd.DataFrame,
                      sos: str,
                      eos: str) -> pd.DataFrame:
        """
        Computes number of days in each decade from input dataframe,
        crop planting and harvesting dates.

        Parameters
        ----------
        df : pandas.DataFrame
            A DataFrame object containing decadal values. The dataframe
            should contain time field which is pandas datetime data type.
        sos : str
            String date which corresponds to crop planting date (start of a season)
        eos : str
            String date which corresponds to crop harvesting date (end of a season)

        Returns
        -------
        df : pandas.DataFrame
            Returns a DataFrame object containing number of days in each decade.
        """

        # Create empty list for holding number of days in each decade
        date_val = []
        # Iterate through each record and compute number of days

        for i in df.index.values:
            decade_start_day = int(df.time[i][-2:])
            # Check if i is the index of first record
            if i == 0:
                # Compute date difference as timedelta. The planting
                # date is always greater than or equal to decade start date.
                if decade_start_day < 21:
                    ndays = pd.to_datetime(
                        df.time[i]) + pd.Timedelta(days=10) - pd.to_datetime(sos)
                    ndays = to_offset(pd.Timedelta(ndays)).n
                else:
                    end_date = df.time[i][:8] + \
                        str(pd.Timestamp(df.time[i]).daysinmonth)

                    ndays = pd.to_datetime(end_date) - pd.to_datetime(sos)

                    ndays = to_offset(pd.Timedelta(ndays)).n + 1
            # Check if i is the index of the last record
            elif i == (len(df)-1):
                # Compute date difference as timedelta. The harvesting
                # date is always greater than or equal to decade start date.
                ndays = pd.to_datetime(eos) - pd.to_datetime(df.time[i])
                ndays = to_offset(pd.Timedelta(ndays)).n + 1
            else:
                # Compute date difference as timedelta between the current date
                # and the date in the previous record
                if decade_start_day < 21:
                    ndays = 10
                else:
                    ndays = pd.Timestamp(df.time[i]).daysinmonth - 20
            # Append the result to the list
            date_val.append(ndays)
        # Add the resulting comuptation as field to the DataFrame
        df['ndays'] = date_val
        return df
