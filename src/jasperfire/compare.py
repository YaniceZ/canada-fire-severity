"""Put burn severity next to pre-fire conifer decline.

This is a first look, not a test of cause. Pixels next to each other are not
independent, so no p-values are reported, and nothing here controls for terrain,
weather on the day a stand burned, or how much canopy each stand had.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from jasperfire import beetle, indices


def ranks(values: np.ndarray) -> np.ndarray:
    """Ranks starting at 1, with ties sharing their average rank."""
    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    starts = np.flatnonzero(np.r_[True, sorted_values[1:] != sorted_values[:-1]])
    ends = np.r_[starts[1:], len(values)]
    out = np.empty(len(values), dtype="float64")
    for start, end in zip(starts, ends, strict=True):
        out[order[start:end]] = (start + end + 1) / 2
    return out


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman rank correlation of two equal-length arrays without NaN."""
    if len(a) < 3:
        return float("nan")
    return float(np.corrcoef(ranks(a), ranks(b))[0, 1])


def group_table(groups: np.ndarray, names: tuple[str, ...], columns: dict[str, np.ndarray], hectares: float) -> list[dict]:
    """One row per group: area, and the median of each column."""
    rows = []
    for i, name in enumerate(names):
        member = groups == i
        row = {"group": name, "area_ha": round(float(member.sum()) * hectares, 1)}
        for label, values in columns.items():
            row[label] = round(float(np.nanmedian(values[member])), 3) if member.any() else None
        rows.append(row)
    return rows


def run(out_dir: Path) -> dict:
    """Read the burn and beetle rasters from `out_dir` and write comparison.json."""
    import rasterio

    with rasterio.open(out_dir / "ndmi_change.tif") as src:
        change = src.read(1)
        grid = beetle.Grid(src.crs.to_string(), src.transform.c, src.transform.f, src.transform.a, src.width, src.height)
    with rasterio.open(out_dir / "study_mask.tif") as src:
        study = src.read(1).astype(bool)

    # The burn rasters are 20 m. Average them onto the 30 m Landsat grid.
    names = ("rbr", "dnbr", "nbr_pre")
    layers = {name: beetle.read_on_grid(str(out_dir / f"{name}.tif"), grid, "average") for name in names}
    keep = study & ~np.isnan(change)
    for layer in layers.values():
        keep &= ~np.isnan(layer)

    groups = np.where(keep, beetle.classify_decline(change), 255)
    high = (layers["dnbr"] >= indices.SEVERITY_BREAKS[-1]).astype("float32")
    hectares = grid.size**2 / 10_000.0
    table = group_table(
        groups,
        beetle.DECLINE_NAMES,
        {"rbr_median": layers["rbr"], "dnbr_median": layers["dnbr"], "nbr_pre_median": layers["nbr_pre"]},
        hectares,
    )
    for i, row in enumerate(table):
        member = groups == i
        row["high_severity_share"] = round(float(high[member].mean()), 3) if member.any() else None

    result = {
        "pixels": int(keep.sum()),
        "area_ha": round(float(keep.sum()) * hectares, 1),
        "spearman_ndmi_change_vs_rbr": round(spearman(change[keep], layers["rbr"][keep]), 3),
        "spearman_ndmi_change_vs_dnbr": round(spearman(change[keep], layers["dnbr"][keep]), 3),
        "spearman_ndmi_change_vs_nbr_pre": round(spearman(change[keep], layers["nbr_pre"][keep]), 3),
        "by_decline_class": table,
        "note": "Descriptive only. Not adjusted for terrain, fire weather or pre-fire canopy.",
    }
    (out_dir / "comparison.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
