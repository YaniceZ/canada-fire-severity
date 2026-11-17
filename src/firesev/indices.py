"""Burn indices. Plain numpy, so they are easy to test."""

from __future__ import annotations

import numpy as np

# Sentinel-2 scene classification values that should not be used:
# 0 no data, 1 saturated, 3 cloud shadow, 6 water, 8 and 9 cloud, 10 cirrus, 11 snow.
BAD_SCL = (0, 1, 3, 6, 8, 9, 10, 11)

# dNBR break points proposed by Key and Benson (2006) and widely reused.
# They were not calibrated on field plots from this fire, so the class names are
# a convention for reading the map, not a measured severity.
SEVERITY_BREAKS = (0.10, 0.27, 0.44, 0.66)
SEVERITY_NAMES = ("unburned", "low", "moderate-low", "moderate-high", "high")
NODATA_CLASS = 255


def to_reflectance(dn: np.ndarray, offset_applied: bool) -> np.ndarray:
    """Convert Sentinel-2 L2A digital numbers to surface reflectance.

    Since processing baseline 04.00 the product stores reflectance * 10000 + 1000.
    Some copies of the data have that 1000 removed already and some do not.
    Zero is the no-data value and becomes NaN.
    """
    out = dn.astype("float32")
    out[dn == 0] = np.nan
    if not offset_applied:
        out -= 1000.0
    return out / 10000.0


def nbr(nir: np.ndarray, swir: np.ndarray) -> np.ndarray:
    """Normalized Burn Ratio: (NIR - SWIR2) / (NIR + SWIR2)."""
    total = nir + swir
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(total > 0, (nir - swir) / total, np.nan).astype("float32")


def dnbr(nbr_pre: np.ndarray, nbr_post: np.ndarray) -> np.ndarray:
    """Pre-fire NBR minus post-fire NBR. Higher means more change."""
    return (nbr_pre - nbr_post).astype("float32")


def rbr(nbr_pre: np.ndarray, nbr_post: np.ndarray) -> np.ndarray:
    """Relativized Burn Ratio (Parks et al. 2014): dNBR / (pre-fire NBR + 1.001).

    It corrects dNBR for how much vegetation was there to begin with, which
    matters here because the beetle-killed stands started with a low NBR.
    """
    return (dnbr(nbr_pre, nbr_post) / (nbr_pre + 1.001)).astype("float32")


def classify_severity(values: np.ndarray, breaks: tuple[float, ...] = SEVERITY_BREAKS) -> np.ndarray:
    """Bin dNBR into classes 0..len(breaks). NaN becomes NODATA_CLASS."""
    classes = np.digitize(values, breaks).astype("uint8")
    classes[np.isnan(values)] = NODATA_CLASS
    return classes


def class_areas_ha(classes: np.ndarray, pixel_area_m2: float) -> dict[str, float]:
    """Hectares in each severity class, ignoring no-data pixels."""
    return {
        name: round(float((classes == i).sum()) * pixel_area_m2 / 10_000.0, 1) for i, name in enumerate(SEVERITY_NAMES)
    }
