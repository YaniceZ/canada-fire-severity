"""Where the data comes from. Every URL here was checked on 2026-11-13."""

from __future__ import annotations

# National Burned Area Composite (Natural Resources Canada), one shapefile per fire year.
NBAC_URL = "https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/NBAC_2024_20260513.zip"
# The Jasper Wildfire Complex in that file: 31,645 ha, mapped from Sentinel-2.
JASPER_GID = "2024_451"



def nbac_url(year: int) -> str:
    """NBAC shapefile for another fire year (same release as NBAC_URL)."""
    return f"https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/NBAC_{year}_20260513.zip"


# Landsat Collection 2 Level-2 on the Microsoft Planetary Computer. The files need
# a short-lived read token, which the token endpoint hands out without an account.
PC_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_TOKEN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/landsat-c2-l2"
LANDSAT_COLLECTION = "landsat-c2-l2"

# Landsat 8 began in 2013, so one sensor family covers the whole outbreak. Parks
# Canada recorded little beetle activity in Jasper before 2014 and a peak in 2017-2019.
BEETLE_YEARS = (2013, 2023)
BEETLE_BASELINE = (2013, 2014, 2015)
BEETLE_RECENT = (2021, 2022, 2023)
BEETLE_SEASON = ("07-01", "09-15")

# 30 m Land Cover of Canada, 2010 edition: the forest as it was before the outbreak.
LANDCOVER_2010_URL = (
    "https://datacube-prod-data-public.s3.ca-central-1.amazonaws.com/store/land/landcover/landcover-2010-classification.tif"
)

# Working grid: UTM zone 11N, the native projection of the Landsat and Sentinel-2 scenes here.
GRID_CRS = "EPSG:32611"

# The fire was first detected on 2024-07-22 and declared under control on 2024-09-07.
FIRE_START = "2024-07-22"
FIRE_END = "2024-09-07"

# Sentinel-2 L2A as cloud-optimized GeoTIFFs, searched through Element 84's Earth Search.
STAC_URL = "https://earth-search.aws.element84.com/v1"
S2_COLLECTION = "sentinel-2-l2a"

# 1 m LiDAR surface (DSM) and terrain (DTM) models from the CanElevation series.
# All three share one grid (EPSG:3979, 1 m), so they can be differenced directly.
HRDEM_BASE = "https://canelevation-dem.s3.ca-central-1.amazonaws.com/hrdem-lidar"
HRDEM_PROJECTS = {
    "2019": "NRCAN-Jasper_Park_2019_2-1m",  # before the fire, whole park
    "2024": "PC-Jasper_2024-1m",  # about three months after the fire
    "2025": "PC-Jasper_2025-1m",  # one year later, smaller area around the town
}
HRDEM_CRS = "EPSG:3979"


def hrdem_url(year: str, kind: str) -> str:
    """URL of a LiDAR raster. `kind` is "dsm" or "dtm"."""
    if kind not in ("dsm", "dtm"):
        raise ValueError(f"kind must be 'dsm' or 'dtm', got {kind!r}")
    return f"{HRDEM_BASE}/{HRDEM_PROJECTS[year]}-{kind}.tif"
