"""Canopy height and cover before and after the fire, from the 1 m LiDAR models.

The satellite burn index is close to its ceiling over most of this fire. Canopy
structure measured by LiDAR is a second, independent look at what the fire did:
how much height and cover each 30 m cell lost between 2019 and October 2024.

The rasters are read at full resolution in tiles. Their built-in overviews are
not block averages (they differ from the true mean by 0.3 to 0.7 m), so they are
not used. Canopy height is surface minus terrain at 1 m, then summarised per cell.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from jasperfire import beetle, compare, indices, sources
from jasperfire.fires import Fire
from jasperfire.lidar import canopy_height

CELL = 30  # metres; output cell, a whole number of 1 m LiDAR pixels
TILE = 2400  # metres; a whole number of cells
MIN_HEIGHT = 2.0  # metres; a 1 m pixel taller than this counts as canopy
BUFFER_M = 1500.0  # how far outside the perimeter to read, for the unburned comparison
EDGE_M = 90.0  # cells within three cells of the perimeter are in neither group
FOREST_HEIGHT = 3.0  # metres; cells with a lower 2019 mean height are not treated as forest
SURVEYS = ("2019", "2024")


def block_stats(chm: np.ndarray, block: int = CELL, min_height: float = MIN_HEIGHT, min_valid: float = 0.5):
    """Mean height and canopy cover per block of `block` x `block` pixels.

    Blocks where less than `min_valid` of the pixels have data are NaN.
    """
    rows, cols = chm.shape[0] // block, chm.shape[1] // block
    cells = chm[: rows * block, : cols * block].reshape(rows, block, cols, block)
    valid = ~np.isnan(cells)
    count = valid.sum(axis=(1, 3))
    enough = count >= min_valid * block * block
    safe = np.where(count > 0, count, 1)
    height = np.where(valid, cells, 0).sum(axis=(1, 3)) / safe
    cover = (valid & (cells > min_height)).sum(axis=(1, 3)) / safe
    height = np.where(enough, height, np.nan).astype("float32")
    cover = np.where(enough, cover, np.nan).astype("float32")
    return height, cover


def relative_loss(before: np.ndarray, after: np.ndarray, floor: float) -> np.ndarray:
    """(before - after) / before, only where `before` is at least `floor`. Can be negative (growth)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (before - after) / before
    return np.where(before >= floor, out, np.nan).astype("float32")


def _read_tile(year: str, bounds: tuple[float, float, float, float]) -> np.ndarray:
    """1 m canopy height for one tile. Opens its own file handles, so it is safe in a thread."""
    import rasterio
    from rasterio.windows import from_bounds

    layers = {}
    nodata = None
    for kind in ("dsm", "dtm"):
        with rasterio.open(sources.hrdem_url(year, kind)) as src:
            window = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
            layers[kind] = src.read(1, window=window, boundless=True, fill_value=src.nodata)
            nodata = src.nodata
    return canopy_height(layers["dsm"], layers["dtm"], nodata)


def _tile_stats(bounds: tuple[float, float, float, float]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    return {year: block_stats(_read_tile(year, bounds)) for year in SURVEYS}


def build(perimeter, out_dir: Path, workers: int = 6) -> Path:
    """Write canopy_30m.tif: mean height and cover for both surveys, 30 m cells, EPSG:3979."""
    import rasterio
    from rasterio.transform import from_origin
    from shapely.geometry import box

    out_dir.mkdir(parents=True, exist_ok=True)
    area = perimeter.to_crs(sources.HRDEM_CRS).geometry.buffer(BUFFER_M).union_all()
    left, bottom, right, top = area.bounds
    left, bottom = np.floor(left / TILE) * TILE, np.floor(bottom / TILE) * TILE
    right, top = np.ceil(right / TILE) * TILE, np.ceil(top / TILE) * TILE
    cols, rows = int((right - left) / CELL), int((top - bottom) / CELL)
    per_tile = TILE // CELL

    tiles = []
    for ty in range(int((top - bottom) / TILE)):
        for tx in range(int((right - left) / TILE)):
            bounds = (left + tx * TILE, top - (ty + 1) * TILE, left + (tx + 1) * TILE, top - ty * TILE)
            if area.intersects(box(*bounds)):
                tiles.append((ty, tx, bounds))

    stack = np.full((4, rows, cols), np.nan, dtype="float32")
    with ThreadPoolExecutor(workers) as pool:
        results = pool.map(lambda tile: _tile_stats(tile[2]), tiles)
        for done, ((ty, tx, _), stats) in enumerate(zip(tiles, results, strict=True), start=1):
            window = (slice(ty * per_tile, (ty + 1) * per_tile), slice(tx * per_tile, (tx + 1) * per_tile))
            for i, year in enumerate(SURVEYS):
                stack[2 * i][window], stack[2 * i + 1][window] = stats[year]
            if done % 20 == 0 or done == len(tiles):
                print(f"  {done}/{len(tiles)} tiles", flush=True)

    path = out_dir / "canopy_30m.tif"
    profile = {
        "driver": "GTiff",
        "height": rows,
        "width": cols,
        "count": 4,
        "dtype": "float32",
        "crs": sources.HRDEM_CRS,
        "transform": from_origin(left, top, CELL, CELL),
        "nodata": np.nan,
        "compress": "deflate",
        "tiled": True,
    }
    names = [f"{what}_{year}" for year in SURVEYS for what in ("height_m", "cover")]
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(stack)
        for i, name in enumerate(names, start=1):
            dst.set_band_description(i, name)
    return path


def _band_on_grid(path: Path, band: int, grid: beetle.Grid) -> np.ndarray:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.open(path) as src:
        with WarpedVRT(
            src, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height, resampling=Resampling.bilinear
        ) as vrt:
            return vrt.read(band)


def _medians(mask: np.ndarray, columns: dict[str, np.ndarray], hectares: float) -> dict:
    row = {"area_ha": round(float(mask.sum()) * hectares, 1)}
    for name, values in columns.items():
        row[name] = round(float(np.nanmedian(values[mask])), 3) if mask.any() else None
    return row


def analyse(fire: Fire, perimeter, out_dir: Path) -> dict:
    """Compare canopy loss inside the fire with the unburned ring around it, and by class."""
    import rasterio
    from rasterio.features import geometry_mask

    burn_area = perimeter.to_crs(fire.grid_crs)
    ring = burn_area.geometry.buffer(BUFFER_M).union_all()
    grid = beetle.grid_for(ring.bounds, fire.grid_crs)
    shape = (grid.height, grid.width)

    canopy = out_dir / "canopy_30m.tif"
    h19, c19, h24, c24 = (_band_on_grid(canopy, band, grid) for band in (1, 2, 3, 4))
    height_loss = relative_loss(h19, h24, FOREST_HEIGHT)
    cover_change = np.where(h19 >= FOREST_HEIGHT, c24 - c19, np.nan).astype("float32")

    def inside(geometry) -> np.ndarray:
        return ~geometry_mask([geometry], out_shape=shape, transform=grid.transform)

    outline = burn_area.geometry.union_all()
    burned = inside(outline.buffer(-EDGE_M))
    control = inside(ring) & ~inside(outline.buffer(EDGE_M))

    names = ("ndmi_change", "rbr", "dnbr", "severity")
    layers = {name: beetle.read_on_grid(str(out_dir / f"{name}.tif"), grid, "nearest") for name in names}
    conifer = beetle.conifer_mask(grid, fire.landcover_url)
    valid = conifer & ~np.isnan(height_loss) & ~np.isnan(layers["ndmi_change"])
    burned &= valid & ~np.isnan(layers["rbr"])
    control &= valid

    columns = {
        "height_2019_m": h19,
        "height_2024_m": h24,
        "height_loss_share": height_loss,
        "cover_2019": c19,
        "cover_2024": c24,
        "cover_change": cover_change,
    }
    hectares = grid.size**2 / 10_000.0
    decline = beetle.classify_decline(layers["ndmi_change"])
    by_decline = []
    for i, name in enumerate(beetle.DECLINE_NAMES):
        fire_row = _medians(burned & (decline == i), columns, hectares)
        control_row = _medians(control & (decline == i), columns, hectares)
        gap = None
        if fire_row["height_loss_share"] is not None and control_row["height_loss_share"] is not None:
            gap = round(fire_row["height_loss_share"] - control_row["height_loss_share"], 3)
        by_decline.append({"group": name, "burned": fire_row, "unburned": control_row, "extra_height_loss_in_fire": gap})

    by_severity = [
        {"group": name, **_medians(burned & (layers["severity"] == i), columns, hectares)}
        for i, name in enumerate(indices.SEVERITY_NAMES)
    ]

    result = {
        "cell_m": grid.size,
        "forest_rule": f"conifer in 2010 and mean canopy height of at least {FOREST_HEIGHT} m in 2019",
        "lidar_2019_share_of_fire": round(float((~np.isnan(h19))[inside(outline)].mean()), 3),
        "lidar_2024_share_of_fire": round(float((~np.isnan(h24))[inside(outline)].mean()), 3),
        "height_loss_quartiles_burned": [round(float(v), 3) for v in np.percentile(height_loss[burned], [25, 50, 75])],
        "burned": _medians(burned, columns, hectares),
        "unburned": _medians(control, columns, hectares),
        "spearman_height_loss_vs_rbr": round(compare.spearman(height_loss[burned], layers["rbr"][burned]), 3),
        "spearman_height_loss_vs_ndmi_change": round(
            compare.spearman(height_loss[burned], layers["ndmi_change"][burned]), 3
        ),
        "spearman_height_loss_vs_ndmi_change_unburned": round(
            compare.spearman(height_loss[control], layers["ndmi_change"][control]), 3
        ),
        "by_severity_class": by_severity,
        "by_decline_class": by_decline,
        "note": (
            f"The unburned group is conifer forest {EDGE_M:.0f} to {BUFFER_M:.0f} m outside the perimeter. "
            "It shows how much canopy the two surveys differ without fire, "
            "from tree fall, growth and differences in processing. "
            "The 2024 survey was flown over the burned area, so this group is small."
        ),
    }
    (out_dir / "canopy_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    for name, array in (("height_loss", height_loss), ("cover_change", cover_change)):
        profile = {
            "driver": "GTiff",
            "height": grid.height,
            "width": grid.width,
            "count": 1,
            "dtype": "float32",
            "crs": grid.crs,
            "transform": grid.transform,
            "nodata": np.nan,
            "compress": "deflate",
            "tiled": True,
        }
        with rasterio.open(out_dir / f"canopy_{name}.tif", "w", **profile) as dst:
            dst.write(array, 1)
    return result
