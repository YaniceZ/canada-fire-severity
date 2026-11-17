"""Does pre-fire decline help predict burn severity once other things are accounted for?

The descriptive comparison cannot answer that, because the declined stands were
also the tall, dense ones. Here four groups of predictors are added one at a time
to a gradient-boosted model, and each model is scored on blocks of land it never
saw in training:

1. terrain: elevation, slope, aspect
2. stand structure before the fire: LiDAR canopy height and cover in 2019, NDMI in 2013-15
3. when and in what weather the cell burned, from satellite fire detections
4. pre-fire decline: NDMI change from 2013-15 to 2021-23

If decline matters beyond the first three groups, the score should rise at step 4.
"""

from __future__ import annotations

import json
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

from jasperfire import beetle, blocks, canopy
from jasperfire.fires import Fire

FEATURE_GROUPS = {
    "terrain": ("elevation_m", "slope_deg", "northness", "eastness"),
    "structure": ("canopy_height_2019_m", "canopy_cover_2019", "ndmi_2013_15"),
    "fire weather": ("burn_hours", "wind_kmh", "humidity_pct", "fire_weather_index"),
    "decline": ("ndmi_change",),
}
TARGETS = {"rbr": "Relativized burn ratio", "height_loss": "LiDAR canopy height loss"}
THIN = 3  # keep every third cell each way (90 m apart) to cut down near-duplicate neighbours
BLOCK_M = 3000.0
FOLDS = 5
REPEATS = 10  # block splits used to check the gain from the decline predictor
MAX_DETECTION_M = 1000.0  # cells farther than this from any fire detection get no burn time


def slope_aspect(dem: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Slope in degrees, northness and eastness of the downslope direction.

    `dem` is north-up with row 0 at the top. Flat cells get northness and eastness of 0.
    """
    d_row, d_col = np.gradient(dem.astype("float64"), cell)
    d_north, d_east = -d_row, d_col  # rows run south, so flip the sign
    steepness = np.hypot(d_north, d_east)
    slope = np.degrees(np.arctan(steepness))
    with np.errstate(divide="ignore", invalid="ignore"):
        northness = np.where(steepness > 0, -d_north / steepness, 0.0)
        eastness = np.where(steepness > 0, -d_east / steepness, 0.0)
    return slope.astype("float32"), northness.astype("float32"), eastness.astype("float32")


def nearest_detection(x, y, det_x, det_y, max_distance: float) -> tuple[np.ndarray, np.ndarray]:
    """Index of the closest detection for each point, and whether it is within `max_distance`."""
    from scipy.spatial import cKDTree

    distance, index = cKDTree(np.column_stack([det_x, det_y])).query(np.column_stack([x, y]))
    return index, distance <= max_distance


def load_hotspots(fire: Fire, data_dir: Path, bbox: tuple[float, float, float, float]):
    """VIIRS fire detections inside `bbox` (lon/lat) during the fire, cached as a small CSV."""
    import pandas as pd

    cached = data_dir / f"{fire.key}_hotspots.csv"
    if cached.exists():
        return pd.read_csv(cached, parse_dates=["rep_date"])
    folder = data_dir / "hotspots"
    folder.mkdir(parents=True, exist_ok=True)
    member = Path(fire.hotspots_url).name.replace(".zip", ".csv")
    csv = folder / member
    if not csv.exists():
        zip_path = folder / "hotspots.zip"
        request = urllib.request.Request(fire.hotspots_url, headers={"User-Agent": "jasperfire"})
        with urllib.request.urlopen(request, timeout=600) as response, open(zip_path, "wb") as f:
            while chunk := response.read(1 << 20):
                f.write(chunk)
        with zipfile.ZipFile(zip_path) as z:
            z.extract(member, folder)
        zip_path.unlink()
    columns = ["rep_date", "sensor", "lat", "lon", "ws", "rh", "fwi"]
    spots = pd.read_csv(csv, usecols=columns, parse_dates=["rep_date"], low_memory=False)
    keep = (
        spots["lon"].between(bbox[0], bbox[2])
        & spots["lat"].between(bbox[1], bbox[3])
        & (spots["sensor"] == "VIIRS-I")
        & spots["rep_date"].between(fire.start, f"{fire.end} 23:59")
    )
    spots = spots[keep].reset_index(drop=True)
    spots.to_csv(cached, index=False)
    return spots


def build_table(fire: Fire, perimeter, data_dir: Path, out_dir: Path):
    """One row per kept 30 m cell, with both targets and all predictors. Written to pixel_table.csv.gz."""
    import geopandas as gpd
    import pandas as pd
    import rasterio
    from rasterio.features import geometry_mask

    with rasterio.open(out_dir / "ndmi_annual.tif") as src:
        t = src.transform
        grid = beetle.Grid(src.crs.to_string(), t.c, t.f, t.a, src.width, src.height)
        years = [int(y) for y in src.descriptions]
        annual = src.read()
    baseline = beetle.composite(np.stack([annual[years.index(y)] for y in fire.decline_baseline]))

    def local(name: str) -> np.ndarray:
        return beetle.read_on_grid(str(out_dir / f"{name}.tif"), grid)

    study = local("study_mask").astype(bool)
    core = perimeter.to_crs(grid.crs).geometry.union_all().buffer(-canopy.EDGE_M)
    study &= ~geometry_mask([core], out_shape=study.shape, transform=grid.transform)

    dem = beetle.read_on_grid(fire.dem_url, grid, "bilinear").astype("float32")
    slope, northness, eastness = slope_aspect(dem, grid.size)
    layers = {
        "rbr": beetle.read_on_grid(str(out_dir / "rbr.tif"), grid, "average"),
        "elevation_m": dem,
        "slope_deg": slope,
        "northness": northness,
        "eastness": eastness,
        "ndmi_2013_15": baseline,
        "ndmi_change": local("ndmi_change"),
    }
    if fire.lidar_change:
        canopy_path = out_dir / "canopy_30m.tif"
        layers["height_loss"] = local("canopy_height_loss")
        layers["canopy_height_2019_m"] = canopy._band_on_grid(canopy_path, 1, grid)
        layers["canopy_cover_2019"] = canopy._band_on_grid(canopy_path, 2, grid)

    rows, cols = np.mgrid[0 : grid.height : THIN, 0 : grid.width : THIN]
    rows, cols = rows.ravel(), cols.ravel()
    keep = study[rows, cols]
    for layer in layers.values():
        keep &= ~np.isnan(layer[rows, cols])
    rows, cols = rows[keep], cols[keep]
    table = pd.DataFrame({name: layer[rows, cols] for name, layer in layers.items()})
    table.insert(0, "y", grid.top - (rows + 0.5) * grid.size)
    table.insert(0, "x", grid.left + (cols + 0.5) * grid.size)

    # When each cell burned, and the weather then: taken from the nearest fire detection.
    bbox = tuple(float(v) for v in perimeter.to_crs(4326).buffer(0.02).total_bounds)
    spots = load_hotspots(fire, data_dir, bbox)
    points = gpd.GeoSeries(gpd.points_from_xy(spots["lon"], spots["lat"]), crs=4326).to_crs(grid.crs)
    index, near = nearest_detection(table["x"], table["y"], points.x, points.y, MAX_DETECTION_M)
    start = spots["rep_date"].min()
    table["burn_hours"] = ((spots["rep_date"] - start).dt.total_seconds() / 3600.0).to_numpy()[index]
    table["wind_kmh"] = spots["ws"].to_numpy()[index]
    table["humidity_pct"] = spots["rh"].to_numpy()[index]
    table["fire_weather_index"] = spots["fwi"].to_numpy()[index]
    table = table[near].dropna().reset_index(drop=True)
    table.to_csv(out_dir / "pixel_table.csv.gz", index=False)
    return table


def groups_for(table) -> dict[str, tuple[str, ...]]:
    """FEATURE_GROUPS limited to the columns the table has. Fires without LiDAR lose the LiDAR predictors."""
    columns = set(getattr(table, "columns", table))
    return {group: tuple(n for n in names if n in columns) for group, names in FEATURE_GROUPS.items()}


def targets_for(table) -> dict[str, str]:
    """The targets the table has: the burn ratio always, LiDAR height loss only where it was measured."""
    columns = set(getattr(table, "columns", table))
    return {name: label for name, label in TARGETS.items() if name in columns}


def _features(upto: int, groups: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    groups = FEATURE_GROUPS if groups is None else groups
    names = []
    for group in list(groups)[: upto + 1]:
        names.extend(groups[group])
    return names


def _estimator():
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(max_iter=120, learning_rate=0.1, max_leaf_nodes=24, random_state=0)


def cv_score(table, features: list[str], target: str, splits) -> dict:
    """R2 and RMSE of out-of-fold predictions pooled over `splits`."""
    from sklearn.metrics import r2_score, root_mean_squared_error

    x, y = table[features].to_numpy(), table[target].to_numpy()
    predicted = np.full(len(y), np.nan)
    for train, test in splits:
        predicted[test] = _estimator().fit(x[train], y[train]).predict(x[test])
    return {"r2": round(float(r2_score(y, predicted)), 3), "rmse": round(float(root_mean_squared_error(y, predicted)), 4)}


def decline_gain(table, target: str, seeds: int = REPEATS) -> list[float]:
    """Block-validated R2 with the decline predictor minus R2 without it, for several block splits.

    With about thirty blocks, one split says little: the gain can come from which
    blocks happened to be held out together. Repeating the split shows the spread.
    """
    block_ids = blocks.assign_blocks(table["x"].to_numpy(), table["y"].to_numpy(), BLOCK_M)
    groups = groups_for(table)
    last = len(groups) - 1
    gains = []
    for seed in range(seeds):
        splits = list(blocks.block_kfold(block_ids, k=FOLDS, seed=seed))
        without = cv_score(table, _features(last - 1, groups), target, splits)["r2"]
        gains.append(round(cv_score(table, _features(last, groups), target, splits)["r2"] - without, 3))
    return gains


def fit(table, out_dir: Path) -> dict:
    """Score the nested models with block and random splits, then explain the full model."""
    from sklearn.inspection import partial_dependence, permutation_importance
    from sklearn.model_selection import KFold

    block_ids = blocks.assign_blocks(table["x"].to_numpy(), table["y"].to_numpy(), BLOCK_M)
    block_splits = list(blocks.block_kfold(block_ids, k=FOLDS, seed=0))
    random_splits = list(KFold(FOLDS, shuffle=True, random_state=0).split(table))
    groups = groups_for(table)
    everything = _features(len(groups) - 1, groups)

    result = {
        "cells": len(table),
        "cell_spacing_m": 30 * THIN,
        "blocks": int(len(np.unique(block_ids))),
        "block_size_m": BLOCK_M,
        "folds": FOLDS,
        "feature_groups": {group: list(names) for group, names in groups.items()},
        "targets": {},
    }
    for target in targets_for(table):
        steps = []
        for i, group in enumerate(groups):
            features = _features(i, groups)
            steps.append(
                {
                    "added": group,
                    "features": len(features),
                    "block_cv": cv_score(table, features, target, block_splits),
                    "random_cv": cv_score(table, features, target, random_splits),
                }
            )

        # Importance: how much the held-out score drops when one predictor is shuffled.
        x, y = table[everything].to_numpy(), table[target].to_numpy()
        drops = np.zeros((FOLDS, len(everything)))
        for fold, (train, test) in enumerate(block_splits):
            model = _estimator().fit(x[train], y[train])
            drops[fold] = permutation_importance(model, x[test], y[test], n_repeats=5, random_state=0).importances_mean
        importance = {name: round(float(v), 4) for name, v in zip(everything, drops.mean(axis=0), strict=True)}

        model = _estimator().fit(x, y)
        column = everything.index("ndmi_change")
        low, high = np.percentile(x[:, column], [2, 98])
        curve = partial_dependence(model, x, [column], custom_values={column: np.linspace(low, high, 25)}, kind="average")
        gains = decline_gain(table, target)
        result["targets"][target] = {
            "mean": round(float(y.mean()), 3),
            "std": round(float(y.std()), 3),
            "steps": steps,
            "decline_gain_r2": {
                "splits": len(gains),
                "mean": round(float(np.mean(gains)), 3),
                "min": min(gains),
                "max": max(gains),
                "splits_with_a_gain": int(sum(g > 0 for g in gains)),
            },
            "permutation_importance": dict(sorted(importance.items(), key=lambda item: -item[1])),
            "partial_dependence_ndmi_change": {
                "ndmi_change": [round(float(v), 4) for v in curve["grid_values"][0]],
                "predicted": [round(float(v), 4) for v in curve["average"][0]],
            },
        }
    (out_dir / "model_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
