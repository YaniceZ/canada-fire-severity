"""Draw the README figures from the rasters in the results folder."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from jasperfire import beetle, indices

INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e1e0d9", "#fcfcfb"
BLUE, ORANGE, NEUTRAL = "#2a78d6", "#eb6834", "#f0efec"
# One hue, light to dark, for the five ordered severity classes.
SEVERITY_COLORS = ("#f3e9df", "#f6c9a8", "#eb9a63", "#c9571f", "#7a2a0c")


def _style():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "text.color": INK,
            "axes.labelcolor": MUTED,
            "axes.edgecolor": "#c3c2b7",
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    return plt


def _read(path: Path):
    import rasterio

    with rasterio.open(path) as src:
        b = src.bounds
        return src.read(), (b.left / 1000, b.right / 1000, b.bottom / 1000, b.top / 1000), src.descriptions


def _map_axes(ax, title: str) -> None:
    ax.set_title(title, loc="left", fontsize=11, color=INK)
    ax.set_aspect("equal")
    ax.set_xlabel("UTM 11N easting (km)")
    ax.set_ylabel("northing (km)")
    for side in ("top", "right"):
        ax.spines[side].set_visible(True)


def severity_map(out_dir: Path, fig_dir: Path) -> Path:
    plt = _style()
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch

    data, extent, _ = _read(out_dir / "severity.tif")
    classes = np.ma.masked_equal(data[0], indices.NODATA_CLASS)
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    total = sum(summary["area_ha_by_class"].values())

    fig, ax = plt.subplots(figsize=(6.2, 7.4))
    cmap, norm = ListedColormap(SEVERITY_COLORS), BoundaryNorm(range(6), 5)
    ax.imshow(classes, extent=extent, cmap=cmap, norm=norm, interpolation="nearest")
    _map_axes(ax, "Burn severity (dNBR classes), 2024 Jasper wildfire")
    handles = [
        Patch(facecolor=color, edgecolor="#c3c2b7", label=f"{name}  {summary['area_ha_by_class'][name] / total:.0%}")
        for name, color in zip(indices.SEVERITY_NAMES, SEVERITY_COLORS, strict=True)
    ]
    ax.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(1.02, 1),
        frameon=False,
        title="Class, share of area",
        alignment="left",
    )
    path = fig_dir / "severity.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def beetle_figure(out_dir: Path, fig_dir: Path) -> Path:
    plt = _style()
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

    change, extent, _ = _read(out_dir / "ndmi_change.tif")
    study, _, _ = _read(out_dir / "study_mask.tif")
    annual, _, years = _read(out_dir / "ndmi_annual.tif")
    mask = study[0].astype(bool)
    shown = np.ma.masked_where(~mask, change[0])

    fig, (ax_map, ax_line) = plt.subplots(1, 2, figsize=(11.5, 6.6), gridspec_kw={"width_ratios": [1, 1.05], "wspace": 0.3})
    cmap = LinearSegmentedColormap.from_list("decline", ["#8f3410", ORANGE, NEUTRAL, BLUE, "#104281"])
    image = ax_map.imshow(shown, extent=extent, cmap=cmap, norm=TwoSlopeNorm(0, -0.25, 0.25), interpolation="nearest")
    _map_axes(ax_map, "NDMI change, 2013–15 to 2021–23")
    bar = fig.colorbar(image, ax=ax_map, orientation="horizontal", fraction=0.04, pad=0.09)
    bar.set_label("NDMI change (negative = canopy drying or dying)")
    bar.outline.set_visible(False)

    groups = np.where(mask, beetle.classify_decline(change[0]), 255)
    x = [int(y) for y in years]
    for index, color in ((0, ORANGE), (len(beetle.DECLINE_NAMES) - 1, BLUE)):
        series = [float(np.nanmean(band[groups == index])) for band in annual]
        ax_line.plot(x, series, color=color, linewidth=2, marker="o", markersize=5, markeredgecolor=SURFACE)
        label, end = beetle.DECLINE_NAMES[index], (x[-1], series[-1])
        ax_line.annotate(label, end, xytext=(6, 0), textcoords="offset points", va="center")
    ax_line.set_title("Mean summer NDMI by year", loc="left", fontsize=11, color=INK)
    ax_line.set_ylabel("NDMI")
    ax_line.set_xticks(x[::2])
    ax_line.set_xlim(x[0] - 0.4, x[-1] + 3.2)
    ax_line.grid(axis="y", color=GRID, linewidth=0.8)
    ax_line.set_axisbelow(True)
    path = fig_dir / "beetle.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def comparison_figure(out_dir: Path, fig_dir: Path) -> Path:
    plt = _style()

    rows = json.loads((out_dir / "comparison.json").read_text(encoding="utf-8"))["by_decline_class"]
    names = [row["group"] for row in rows]
    panels = (
        ("nbr_pre_median", "Pre-fire NBR (median)"),
        ("dnbr_median", "dNBR (median)"),
        ("rbr_median", "RBR (median)"),
    )
    fig, axes = plt.subplots(1, 3, figsize=(11.5, 3.4), sharey=True)
    for ax, (key, title) in zip(axes, panels, strict=True):
        values = [row[key] for row in rows]
        bars = ax.barh(names, values, color=BLUE, height=0.55)
        ax.bar_label(bars, fmt="%.2f", padding=4, color=INK)
        ax.set_title(title, loc="left", fontsize=11, color=INK)
        ax.set_xlim(0, max(values) * 1.2)
        ax.invert_yaxis()
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(axis="y", length=0)
    axes[0].set_ylabel("Pre-fire NDMI change")
    path = fig_dir / "severity_by_decline.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def run(out_dir: Path, fig_dir: Path) -> list[Path]:
    fig_dir.mkdir(parents=True, exist_ok=True)
    return [severity_map(out_dir, fig_dir), beetle_figure(out_dir, fig_dir), comparison_figure(out_dir, fig_dir)]
