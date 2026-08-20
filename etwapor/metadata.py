"""

Created on Wed August 29 2025

Author: Mulugeta Tadesse
        IWMI East Africa
        mulugeta.tadesse@cgiar.org

"""

from datetime import datetime

date = datetime.now().strftime('%Y-%m-%d')

# Latitude attributes
lat_attrs = {
    'units': 'degrees_north',
    'long_name': 'Latitude',
    'standard_name': 'latitude',
    'axis': 'Y',
    'coordinate_defines': 'center'
}

# Longitude attributes
lon_attrs = {
    'units': 'degrees_east',
    'long_name': 'Longitude',
    'standard_name': 'longitude',
    'axis': 'X',
    'coordinate_defines': 'center'
}

# Time attributes
time_attrs = {'long_name': 'Time',
              'axis': 'T',
              'standard_name': 'time',
              }

# Dataset level attributes
attrs = {
    'title': 'WaPORv3 datasets',
    'data_url1': 'https://gismgr.fao.org/DATA/WAPOR-3/MAPSET/',
    'data_url2': 'https://storage.googleapis.com/fao-gismgr-wapor-3-data/DATA/WAPOR-3/MAPSET/',
    'data_url3': 'https://data.apps.fao.org/gismgr/api/v2/catalog/workspaces/WAPOR-3/mapsets',
    'description': 'The WaPOR project aims to assist partner countries in developing their capacity to monitor and improve water and land productivity in agriculture, both rainfed and irrigated, responding therefore to the challenges that are posed by the dwindling of freshwater resources and the need to sustain agricultural production to ensure food security in the face of a changing climate.',
    'data_version': 3.0
}

# Attributes for net primary production data
NPP_attrs = {'title': 'Net primary production',
             'description': 'Net Primary Production (NPP) is a fundamental characteristic of an ecosystem, expressing the conversion of carbon dioxide into biomass driven by photosynthesis. The pixel value represents the mean daily NPP for that specific dekad. The data is provided in near real time approximately 5 days after the end of the dekad.',
             'publication_date': '2023-05-01',
             'unit': 'gC/m²/season',
             }

# Attributes for net primary production data
AETI_attrs = {'title': 'Actual evapotranspiration and interception',
             'description': 'The actual evapotranspiration and interception (ETIa) is the sum of the soil evaporation (E), canopy transpiration (T), and evaporation from rainfall intercepted by leaves (I). The value of each pixel represents the ETIa in a given year.',
             'publication_date': '2023-05-01',
             'unit': 'mm/season',
             }

# Attributes for estimated yield
yield_attrs = {'title': 'Estimated yield',
               'description': 'Yield estimated from seasonal net primary production.',
               'computed_date': date,
               'unit': 'kg/ha',

               }
# Global level attributes
global_attrs = {'lat': lat_attrs,
                'lon': lon_attrs,
                'time': time_attrs,
                'attrs': attrs}

# Variable level attributes
variable_attrs = {'NPP': NPP_attrs,
                  'yield': yield_attrs,
                  'AETI': AETI_attrs}
