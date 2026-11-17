"""Data services that are the same for every fire. Settings for one fire live in `fires.py`."""

from __future__ import annotations

# National Burned Area Composite (Natural Resources Canada), one shapefile per fire year.


def nbac_url(year: int) -> str:
    """NBAC shapefile for one fire year (release of 2026-05-13)."""
    return f"https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/NBAC_{year}_20260513.zip"


# Landsat Collection 2 Level-2 on the Microsoft Planetary Computer. The files need
# a short-lived read token, which the token endpoint hands out without an account.
PC_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_TOKEN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/landsat-c2-l2"
LANDSAT_COLLECTION = "landsat-c2-l2"

# AlphaEarth Foundations annual embeddings, free copy on Source Cooperative (CC-BY 4.0).
AEF_INDEX_URL = "https://data.source.coop/tge-labs/aef/v1/annual/aef_index.gpkg"
AEF_S3_PREFIX = "s3://us-west-2.opendata.source.coop/"
AEF_HTTPS_PREFIX = "https://data.source.coop/"

# Sentinel-2 L2A as cloud-optimized GeoTIFFs, searched through Element 84's Earth Search.
STAC_URL = "https://earth-search.aws.element84.com/v1"
S2_COLLECTION = "sentinel-2-l2a"

# 1 m LiDAR surface (DSM) and terrain (DTM) models from the CanElevation series, for Jasper.
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
