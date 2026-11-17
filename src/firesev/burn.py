"""Compute dNBR and RBR inside the fire perimeter and write them as GeoTIFFs."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np

from firesev import indices
from firesev.scenes import Scene

# Earth Search asset names for the 20 m bands used here.
NIR, SWIR, SCL = "nir08", "swir22", "scl"


def _read(scene: Scene, key: str, bounds: tuple[float, float, float, float]):
    import rasterio
    from rasterio.windows import from_bounds

    with rasterio.open(scene.assets[key]) as src:
        window = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
        return src.read(1, window=window), src.window_transform(window), src.crs


def _scene_nbr(scene: Scene, bounds: tuple[float, float, float, float]):
    nir_dn, transform, crs = _read(scene, NIR, bounds)
    swir_dn, _, _ = _read(scene, SWIR, bounds)
    scl, _, _ = _read(scene, SCL, bounds)
    value = indices.nbr(
        indices.to_reflectance(nir_dn, scene.offset_applied),
        indices.to_reflectance(swir_dn, scene.offset_applied),
    )
    value[np.isin(scl, indices.BAD_SCL)] = np.nan
    return value, transform, crs


def _write(path: Path, array: np.ndarray, transform, crs, nodata) -> None:
    import rasterio

    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": array.dtype.name,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "compress": "deflate",
        "tiled": True,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array, 1)


def run(perimeter: gpd.GeoDataFrame, pre: Scene, post: Scene, out_dir: Path) -> dict:
    """Write nbr_pre.tif, dnbr.tif, rbr.tif, severity.tif and summary.json. Returns the summary."""
    import rasterio
    from rasterio.features import geometry_mask

    out_dir.mkdir(parents=True, exist_ok=True)
    with rasterio.open(pre.assets[NIR]) as src:
        scene_crs = src.crs
    fire = perimeter.to_crs(scene_crs)
    bounds = tuple(fire.total_bounds)

    nbr_pre, transform, crs = _scene_nbr(pre, bounds)
    nbr_post, _, _ = _scene_nbr(post, bounds)
    outside = geometry_mask(fire.geometry, out_shape=nbr_pre.shape, transform=transform, invert=False)
    nbr_pre[outside] = np.nan
    nbr_post[outside] = np.nan

    dnbr = indices.dnbr(nbr_pre, nbr_post)
    rbr = indices.rbr(nbr_pre, nbr_post)
    classes = indices.classify_severity(dnbr)

    _write(out_dir / "nbr_pre.tif", nbr_pre, transform, crs, np.nan)
    _write(out_dir / "dnbr.tif", dnbr, transform, crs, np.nan)
    _write(out_dir / "rbr.tif", rbr, transform, crs, np.nan)
    _write(out_dir / "severity.tif", classes, transform, crs, indices.NODATA_CLASS)

    pixel_area = abs(transform.a * transform.e)
    inside = int((~outside).sum())
    valid = int((classes != indices.NODATA_CLASS).sum())
    summary = {
        "pre_scene": pre.id,
        "post_scene": post.id,
        "pixel_size_m": abs(transform.a),
        "perimeter_ha": round(float(fire.geometry.area.sum()) / 10_000.0, 1),
        "valid_share_of_perimeter": round(valid / inside, 4) if inside else 0.0,
        "dnbr_median": round(float(np.nanmedian(dnbr)), 3),
        "area_ha_by_class": indices.class_areas_ha(classes, pixel_area),
        "note": "Class breaks are the generic Key and Benson values, not calibrated on field plots.",
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
