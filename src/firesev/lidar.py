"""Canopy height from the 1 m LiDAR surface and terrain models."""

from __future__ import annotations

import numpy as np

from firesev import sources


def canopy_height(dsm: np.ndarray, dtm: np.ndarray, nodata: float | None = None) -> np.ndarray:
    """Surface minus terrain, in metres. Small negative values are set to zero."""
    chm = dsm.astype("float32") - dtm.astype("float32")
    if nodata is not None:
        chm[(dsm == nodata) | (dtm == nodata)] = np.nan
    return np.where(chm < 0, 0, chm).astype("float32")


def canopy_cover(chm: np.ndarray, min_height: float = 2.0) -> float:
    """Share of valid cells taller than `min_height` metres."""
    valid = ~np.isnan(chm)
    if not valid.any():
        return float("nan")
    return float((chm[valid] > min_height).mean())


def read_chm(year: str, bounds: tuple[float, float, float, float]) -> np.ndarray:
    """Read a canopy height window for one LiDAR survey.

    `bounds` is (left, bottom, right, top) in EPSG:3979. Only the requested window
    is fetched, which matters because the 2019 surface model is an 11.8 GB file.
    """
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
