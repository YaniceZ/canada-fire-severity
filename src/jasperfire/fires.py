"""What is specific to each fire: where it is, when it burned, and which files describe it.

Everything else in the package is the same for every fire. To add one, add an
entry here and run the steps with `--fire <key>`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fire:
    key: str  # short name used for folders and the --fire option
    name: str
    grid_crs: str  # UTM zone of the Landsat and Sentinel-2 scenes there
    start: str  # first detection, ISO date
    end: str  # declared under control, ISO date
    perimeter_url: str  # zipped shapefile that holds the final perimeter
    perimeter_id: str  # value of its GID field for this fire
    decline_years: tuple[int, int]  # first and last year of the yearly NDMI record
    decline_baseline: tuple[int, ...]  # years before the die-off
    decline_recent: tuple[int, ...]  # last years before the fire
    decline_season: tuple[str, str]  # month-day window for the summer composite
    landcover_url: str  # 30 m land cover from before the die-off, NALCMS classes
    dem_url: str  # 30 m terrain model
    hillshade_url: str  # shaded relief, map background only
    hotspots_url: str  # zipped CSV of satellite fire detections for the fire year
    town: tuple[str, float, float]  # label, lon, lat of a place to mark on the maps
    embedding_years: tuple[int, int]  # last full year before the fire, and the fire year


_CANELEVATION = "https://canelevation-dem.s3.ca-central-1.amazonaws.com"

JASPER = Fire(
    key="jasper",
    name="2024 Jasper wildfire",
    grid_crs="EPSG:32611",
    # First detected on 2024-07-22 and declared under control on 2024-09-07.
    start="2024-07-22",
    end="2024-09-07",
    # National Burned Area Composite (Natural Resources Canada): 31,645 ha, mapped from Sentinel-2.
    perimeter_url="https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/NBAC_2024_20260513.zip",
    perimeter_id="2024_451",
    # Landsat 8 began in 2013, so one sensor family covers the whole outbreak. Parks Canada
    # recorded little beetle activity in Jasper before 2014 and a peak in 2017-2019.
    decline_years=(2013, 2023),
    decline_baseline=(2013, 2014, 2015),
    decline_recent=(2021, 2022, 2023),
    decline_season=("07-01", "09-15"),
    # Land Cover of Canada, 2010 edition: the forest as it was before the outbreak.
    landcover_url=(
        "https://datacube-prod-data-public.s3.ca-central-1.amazonaws.com"
        "/store/land/landcover/landcover-2010-classification.tif"
    ),
    dem_url=f"{_CANELEVATION}/mrdem-30/mrdem-30-dtm.tif",
    hillshade_url=f"{_CANELEVATION}/mrdem-30/mrdem-30-dtm-hillshade.tif",
    # Canadian Wildland Fire Information System. Each record carries the time and the
    # fire weather interpolated to that spot.
    hotspots_url="https://cwfis.cfs.nrcan.gc.ca/downloads/hotspots/archive/2024_hotspots.zip",
    town=("Jasper", -118.0814, 52.8737),
    embedding_years=(2023, 2024),
)

FIRES = {fire.key: fire for fire in (JASPER,)}


def get(key: str) -> Fire:
    try:
        return FIRES[key]
    except KeyError:
        raise ValueError(f"unknown fire {key!r}; known fires: {', '.join(sorted(FIRES))}") from None
