"""Map pre-fire conifer decline from the Landsat 8/9 record.

Mountain pine beetle kills a tree in one season. The needles turn red the next
summer and fall within about three years. A dying canopy holds less water, so the
Normalized Difference Moisture Index (NDMI) drops. This module builds one summer
NDMI image per year and measures how far each pixel fell between the years before
the outbreak and the years just before the fire.

The result is a proxy. NDMI also falls after drought, windthrow or other insects,
and there is no public field layer inside the park to check it against.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from firesev import sources
from firesev.fires import Fire

# Landsat Collection 2 QA_PIXEL bits that mark a pixel as unusable:
# 0 fill, 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow, 5 snow, 7 water.
BAD_QA_BITS = (0, 1, 2, 3, 4, 5, 7)

# Land Cover of Canada classes counted as conifer forest (temperate and taiga needleleaf).
CONIFER_CLASSES = (1, 2)

# NDMI change classes, for the map only. The analysis uses the continuous value.
DECLINE_BREAKS = (-0.10, -0.05, -0.02)
DECLINE_NAMES = ("strong decline", "moderate decline", "slight decline", "stable or gain")


def landsat_reflectance(dn: np.ndarray) -> np.ndarray:
    """Landsat Collection 2 Level-2 surface reflectance. Zero is fill and becomes NaN."""
    out = dn.astype("float32") * 0.0000275 - 0.2
    out[dn == 0] = np.nan
    return out


def clear_mask(qa: np.ndarray) -> np.ndarray:
    """True where QA_PIXEL has none of the bad bits set."""
    bad = 0
    for bit in BAD_QA_BITS:
        bad |= 1 << bit
    return (qa.astype("uint16") & bad) == 0


def ndmi(nir: np.ndarray, swir1: np.ndarray) -> np.ndarray:
    """Normalized Difference Moisture Index: (NIR - SWIR1) / (NIR + SWIR1)."""
    total = nir + swir1
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(total > 0, (nir - swir1) / total, np.nan).astype("float32")


def composite(stack: np.ndarray, min_obs: int = 2) -> np.ndarray:
    """Per-pixel median over scenes (axis 0). Pixels with fewer than `min_obs` clear looks are NaN."""
    count = (~np.isnan(stack)).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels are expected
        out = np.nanmedian(stack, axis=0).astype("float32")
    out[count < min_obs] = np.nan
    return out


def period_change(annual: dict[int, np.ndarray], baseline: tuple[int, ...], recent: tuple[int, ...]) -> np.ndarray:
    """Median of the recent years minus median of the baseline years.

    A pixel needs at least two valid years in each period.
    """
    before = composite(np.stack([annual[y] for y in baseline]))
    after = composite(np.stack([annual[y] for y in recent]))
    return (after - before).astype("float32")


def largest_drop_year(annual: dict[int, np.ndarray]) -> np.ndarray:
    """Year in which each pixel lost the most NDMI compared with the year before. 0 where unknown."""
    years = sorted(annual)
    stack = np.stack([annual[y] for y in years])
    steps = np.diff(stack, axis=0)
    unknown = np.isnan(steps).any(axis=0)
    filled = np.where(np.isnan(steps), np.inf, steps)
    out = np.array(years[1:], dtype="uint16")[np.argmin(filled, axis=0)]
    out[unknown] = 0
    return out


def classify_decline(change: np.ndarray) -> np.ndarray:
    """Bin NDMI change into DECLINE_NAMES. NaN becomes 255."""
    classes = np.digitize(change, DECLINE_BREAKS).astype("uint8")
    classes[np.isnan(change)] = 255
    return classes


# --- reading -----------------------------------------------------------------


@dataclass(frozen=True)
class Grid:
    """A north-up raster grid."""

    crs: str
    left: float
    top: float
    size: float
    width: int
    height: int

    @property
    def transform(self):
        from rasterio.transform import from_origin

        return from_origin(self.left, self.top, self.size, self.size)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.left, self.top - self.height * self.size, self.left + self.width * self.size, self.top)


def grid_for(bounds: tuple[float, float, float, float], crs: str, size: float = 30.0) -> Grid:
    """Smallest grid covering `bounds` whose pixel centres sit on multiples of `size`, as Landsat's do."""
    half = size / 2
    left = np.floor((bounds[0] - half) / size) * size + half
    top = np.ceil((bounds[3] + half) / size) * size - half
    width = int(np.ceil((bounds[2] - left) / size))
    height = int(np.ceil((top - bounds[1]) / size))
    return Grid(crs, float(left), float(top), size, width, height)


def read_on_grid(href: str, grid: Grid, resampling: str = "nearest") -> np.ndarray:
    """Read band 1 of a remote raster, warped onto `grid`."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.open(href) as src:
        with WarpedVRT(
            src,
            crs=grid.crs,
            transform=grid.transform,
            width=grid.width,
            height=grid.height,
            resampling=Resampling[resampling],
        ) as vrt:
            return vrt.read(1)


def _token() -> str:
    """Short-lived read token for the Landsat files on the Planetary Computer. No account needed."""
    with urllib.request.urlopen(sources.PC_TOKEN_URL, timeout=60) as response:
        return json.load(response)["token"]


def search_landsat(
    bbox: tuple[float, float, float, float], year: int, season: tuple[str, str], max_cloud: float = 95.0
) -> list[dict]:
    """Landsat 8/9 summer scenes for one year, as {id, nir, swir1, qa} with signed URLs.

    The scene-level cloud limit is loose on purpose. Only one Landsat path covers the
    whole fire, so a scene that is 80% cloud can still hold the only clear look at a
    valley that year. Clouds are removed per pixel in `scene_ndmi`.
    """
    from pystac_client import Client

    start, end = season
    client = Client.open(sources.PC_STAC_URL)
    result = client.search(
        collections=[sources.LANDSAT_COLLECTION],
        bbox=bbox,
        datetime=f"{year}-{start}/{year}-{end}",
        query={"platform": {"in": ["landsat-8", "landsat-9"]}, "eo:cloud_cover": {"lt": max_cloud}},
    )
    token = _token()
    return [
        {
            "id": item.id,
            "nir": f"{item.assets['nir08'].href}?{token}",
            "swir1": f"{item.assets['swir16'].href}?{token}",
            "qa": f"{item.assets['qa_pixel'].href}?{token}",
        }
        for item in result.items()
    ]


def scene_ndmi(scene: dict, grid: Grid) -> np.ndarray:
    nir = landsat_reflectance(read_on_grid(scene["nir"], grid))
    swir1 = landsat_reflectance(read_on_grid(scene["swir1"], grid))
    value = ndmi(nir, swir1)
    value[~clear_mask(read_on_grid(scene["qa"], grid))] = np.nan
    return value


def annual_ndmi(
    bbox: tuple[float, float, float, float], grid: Grid, year: int, season: tuple[str, str], workers: int = 8
) -> tuple[np.ndarray, int]:
    """Summer median NDMI for one year and the number of scenes that went into it."""
    found = search_landsat(bbox, year, season)
    if not found:
        raise ValueError(f"no Landsat 8/9 scenes for {year}")
    with ThreadPoolExecutor(workers) as pool:
        stack = np.stack(list(pool.map(lambda s: scene_ndmi(s, grid), found)))
    return composite(stack, min_obs=1), len(found)


def conifer_mask(grid: Grid, landcover_url: str) -> np.ndarray:
    """True where the land cover from before the die-off shows needleleaf forest."""
    return np.isin(read_on_grid(landcover_url, grid), CONIFER_CLASSES)


def earlier_fire_mask(data_dir: Path, grid: Grid, years: tuple[int, ...]) -> np.ndarray:
    """True where the national burned-area record shows a fire in any of `years`."""
    import geopandas as gpd
    from rasterio.features import rasterize
    from shapely.geometry import box

    from firesev.perimeter import download_nbac

    area = box(*grid.bounds)
    shapes = []
    for year in years:
        shp = download_nbac(data_dir / "nbac_history" / str(year), sources.nbac_url(year))
        fires = gpd.read_file(shp, bbox=gpd.GeoSeries([area], crs=grid.crs)).to_crs(grid.crs)
        shapes.extend(geom for geom in fires.geometry if geom is not None and geom.intersects(area))
    if not shapes:
        return np.zeros((grid.height, grid.width), dtype=bool)
    return rasterize(shapes, out_shape=(grid.height, grid.width), transform=grid.transform, dtype="uint8").astype(bool)


def harvest_mask(data_dir: Path, grid: Grid, years: tuple[int, ...]) -> np.ndarray:
    """True where BC's consolidated cutblocks show a harvest that started in one of `years`.

    A clearcut drops NDMI as sharply as a beetle kill does, so in a working forest the
    decline map is mostly a logging map unless these areas are taken out.
    """
    import geopandas as gpd
    from pyproj import Transformer
    from rasterio.features import rasterize

    cached = data_dir / "bc_cutblocks.geojson"
    if not cached.exists():
        left, bottom, right, top = grid.bounds
        lon, lat = Transformer.from_crs(grid.crs, 4326, always_xy=True).transform(
            [left, right, left, right], [bottom, bottom, top, top]
        )
        query = urllib.parse.urlencode(
            {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeName": sources.BC_CUTBLOCKS_LAYER,
                "outputFormat": "application/json",
                "srsName": "EPSG:4326",
                "bbox": f"{min(lat)},{min(lon)},{max(lat)},{max(lon)},urn:ogc:def:crs:EPSG:4326",
            }
        )
        data_dir.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f"{sources.BC_WFS_URL}?{query}", timeout=300) as response:
            cached.write_bytes(response.read())
    blocks = gpd.read_file(cached)
    blocks = blocks[blocks["HARVEST_START_YEAR_CALENDAR"].isin(years)].to_crs(grid.crs)
    if blocks.empty:
        return np.zeros((grid.height, grid.width), dtype=bool)
    shape = (grid.height, grid.width)
    return rasterize(blocks.geometry, out_shape=shape, transform=grid.transform, dtype="uint8").astype(bool)


# --- pipeline ----------------------------------------------------------------


def _write(path: Path, array: np.ndarray, grid: Grid, nodata, descriptions: list[str] | None = None) -> None:
    import rasterio

    bands = array if array.ndim == 3 else array[np.newaxis]
    profile = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": bands.shape[0],
        "dtype": bands.dtype.name,
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": nodata,
        "compress": "deflate",
        "tiled": True,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(bands)
        for i, text in enumerate(descriptions or [], start=1):
            dst.set_band_description(i, text)


def run(fire: Fire, perimeter, data_dir: Path, out_dir: Path) -> dict:
    """Write the annual NDMI stack, the change map and the masks. Returns a summary."""
    from rasterio.features import geometry_mask

    out_dir.mkdir(parents=True, exist_ok=True)
    outline = perimeter.to_crs(fire.grid_crs)
    grid = grid_for(tuple(outline.total_bounds), fire.grid_crs)
    bbox = tuple(float(v) for v in perimeter.to_crs(4326).total_bounds)

    years = list(range(fire.decline_years[0], fire.decline_years[1] + 1))
    annual, scene_counts = {}, {}
    for year in years:
        annual[year], scene_counts[year] = annual_ndmi(bbox, grid, year, fire.decline_season)
        print(f"  {year}: {scene_counts[year]} scenes")

    change = period_change(annual, fire.decline_baseline, fire.decline_recent)
    drop_year = largest_drop_year(annual)

    inside = ~geometry_mask(outline.geometry, out_shape=(grid.height, grid.width), transform=grid.transform)
    conifer = conifer_mask(grid, fire.landcover_url)
    burned_before = earlier_fire_mask(data_dir, grid, tuple(years))
    harvested = np.zeros_like(inside)
    if fire.bc_cutblocks:
        harvested = harvest_mask(data_dir, grid, tuple(years))
    study = inside & conifer & ~burned_before & ~harvested & ~np.isnan(change)

    _write(out_dir / "ndmi_annual.tif", np.stack([annual[y] for y in years]), grid, np.nan, [str(y) for y in years])
    _write(out_dir / "ndmi_change.tif", change, grid, np.nan)
    _write(out_dir / "ndmi_drop_year.tif", drop_year, grid, 0)
    _write(out_dir / "study_mask.tif", study.astype("uint8"), grid, None)

    hectares = grid.size**2 / 10_000.0
    classes = classify_decline(np.where(study, change, np.nan))
    declining = study & (change < DECLINE_BREAKS[1])
    drop_counts = {int(y): int((drop_year[declining] == y).sum()) for y in years[1:]}
    summary = {
        "years": [years[0], years[-1]],
        "baseline_years": list(fire.decline_baseline),
        "recent_years": list(fire.decline_recent),
        "scenes_per_year": scene_counts,
        "pixel_size_m": grid.size,
        "perimeter_ha": round(float(inside.sum()) * hectares, 1),
        "conifer_2010_ha": round(float((inside & conifer).sum()) * hectares, 1),
        "burned_2013_2023_ha": round(float((inside & burned_before).sum()) * hectares, 1),
        "harvested_in_record_ha": round(float((inside & conifer & harvested).sum()) * hectares, 1),
        "study_ha": round(float(study.sum()) * hectares, 1),
        "ndmi_change_median": round(float(np.nanmedian(change[study])), 3),
        "area_ha_by_class": {name: round(float((classes == i).sum()) * hectares, 1) for i, name in enumerate(DECLINE_NAMES)},
        "largest_drop_year_ha_in_declining_pixels": {y: round(n * hectares, 1) for y, n in drop_counts.items()},
        "study_mean_ndmi_by_year": {y: round(float(np.nanmean(annual[y][study])), 3) for y in years},
        "note": "NDMI decline is a proxy for beetle mortality and has not been checked against field data.",
    }
    (out_dir / "beetle_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
