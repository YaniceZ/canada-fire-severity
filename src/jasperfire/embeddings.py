"""Compare hand-made predictors with AlphaEarth Foundations embeddings.

AlphaEarth is a geospatial foundation model from Google DeepMind. Its Satellite
Embedding dataset gives every 10 m pixel on land a 64-number summary of one year
of satellite observations. The question here is practical: do those 64 numbers
describe a forest stand better than the terrain, LiDAR and NDMI predictors built
by hand for this project, and do they see what the fire did better than the
classic burn ratio?

Every comparison uses the same cells, the same model and the same spatial blocks.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from jasperfire import beetle, blocks, model, sources
from jasperfire.fires import Fire

NODATA = -128
SEEDS = 3  # block splits per comparison


def dequantize(values: np.ndarray) -> np.ndarray:
    """Stored signed bytes to embedding values in [-1, 1]. The no-data byte becomes NaN.

    The formula is the one Google publishes for these files: (v / 127.5)^2 with the sign of v.
    """
    v = values.astype("float32")
    out = np.square(v / 127.5) * np.sign(v)
    out[values == NODATA] = np.nan
    return out


def tile_url(year: int, lon: float, lat: float) -> str:
    """HTTPS address of the embedding file that covers a point, looked up in the dataset index."""
    import geopandas as gpd

    index = gpd.read_file(f"/vsicurl/{sources.AEF_INDEX_URL}", bbox=(lon, lat, lon, lat))
    match = index[index["year"] == year]
    if len(match) != 1:
        raise ValueError(f"expected one embedding file for {year} at {lon}, {lat}, found {len(match)}")
    return match.iloc[0]["path"].replace(sources.AEF_S3_PREFIX, sources.AEF_HTTPS_PREFIX)


def read_embedding(url: str, grid: beetle.Grid) -> np.ndarray:
    """All 64 channels on `grid`, shape (64, height, width).

    The files store rows bottom-up, so a plain window read does not work. Warping
    handles that, and averages the 10 m pixels into each 30 m cell.
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.open(url) as src:
        with WarpedVRT(
            src,
            crs=grid.crs,
            transform=grid.transform,
            width=grid.width,
            height=grid.height,
            resampling=Resampling.average,
            nodata=NODATA,
        ) as vrt:
            return dequantize(vrt.read())


def sample(table, year: int, out_dir: Path) -> np.ndarray:
    """Embedding for each row of the pixel table, shape (rows, 64). Cached as a .npy file."""
    import rasterio
    from pyproj import Transformer

    cached = out_dir / f"embedding_{year}.npy"
    if cached.exists():
        values = np.load(cached)
        if len(values) == len(table):
            return values
    with rasterio.open(out_dir / "ndmi_change.tif") as src:
        t = src.transform
        grid = beetle.Grid(src.crs.to_string(), t.c, t.f, t.a, src.width, src.height)
    left, bottom, right, top = grid.bounds
    lon, lat = Transformer.from_crs(grid.crs, 4326, always_xy=True).transform((left + right) / 2, (bottom + top) / 2)
    cube = read_embedding(tile_url(year, lon, lat), grid)
    cols = ((table["x"].to_numpy() - grid.left) // grid.size).astype(int)
    rows = ((grid.top - table["y"].to_numpy()) // grid.size).astype(int)
    values = cube[:, rows, cols].T.astype("float32")
    np.save(cached, values)
    return values


def _score(x: np.ndarray, y: np.ndarray, block_ids: np.ndarray) -> dict:
    """Mean and range of block-validated R2 over several block splits."""
    from sklearn.metrics import r2_score

    scores = []
    for seed in range(SEEDS):
        predicted = np.full(len(y), np.nan)
        for train, test in blocks.block_kfold(block_ids, k=model.FOLDS, seed=seed):
            predicted[test] = model._estimator().fit(x[train], y[train]).predict(x[test])
        scores.append(float(r2_score(y, predicted)))
    return {"r2": round(float(np.mean(scores)), 3), "min": round(min(scores), 3), "max": round(max(scores), 3)}


def run(fire: Fire, table, out_dir: Path) -> dict:
    """Score every predictor set and write embedding_summary.json."""
    pre_year, fire_year = fire.embedding_years
    before = sample(table, pre_year, out_dir)
    after = sample(table, fire_year, out_dir)
    keep = ~(np.isnan(before).any(axis=1) | np.isnan(after).any(axis=1))
    table, before, after = table[keep].reset_index(drop=True), before[keep], after[keep]
    block_ids = blocks.assign_blocks(table["x"].to_numpy(), table["y"].to_numpy(), model.BLOCK_M)

    def columns(*groups: str) -> np.ndarray:
        names = [name for group in groups for name in available[group]]
        return table[names].to_numpy()

    available = model.groups_for(table)
    weather = columns("fire weather")
    hand = columns("terrain", "structure", "decline")

    # 1. Describing the stand before the fire: which predictors forecast severity best?
    forecast = {}
    for target in model.targets_for(table):
        y = table[target].to_numpy()
        forecast[target] = {
            "hand-made": _score(np.hstack([hand, weather]), y, block_ids),
            "embedding": _score(np.hstack([before, weather]), y, block_ids),
            "both": _score(np.hstack([hand, before, weather]), y, block_ids),
        }

    # 2. Seeing what the fire did: which satellite measure matches the LiDAR canopy loss best?
    detect = None
    if "height_loss" in table:
        loss = table["height_loss"].to_numpy()
        detect = {
            "burn ratio": _score(table[["rbr"]].to_numpy(), loss, block_ids),
            "embedding change": _score(np.hstack([before, after]), loss, block_ids),
            "both": _score(np.hstack([table[["rbr"]].to_numpy(), before, after]), loss, block_ids),
        }

    # 3. Does the pre-fire embedding already know which stands had declined?
    decline = {"embedding": _score(before, table["ndmi_change"].to_numpy(), block_ids)}

    result = {
        "cells": len(table),
        "dropped_for_missing_embedding": int((~keep).sum()),
        "embedding_years": {"before": pre_year, "fire year": fire_year},
        "block_splits": SEEDS,
        "forecast_severity_from_pre_fire_state": forecast,
        "match_lidar_height_loss_after_fire": detect,
        "recover_ndmi_decline": decline,
        "note": (
            "All scores are R2 on held-out 3 km blocks, averaged over the block splits. "
            "Fire weather is in every forecast model."
        ),
    }
    (out_dir / "embedding_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
