"""Draw the README figure from the rasters in the results folder.

One four-panel figure at double-column journal width (180 mm), 300 dpi, with the
maps drawn over shaded relief. Colours are ColorBrewer schemes that stay readable
for colour-blind readers, and the decline classes keep the same colour in every panel.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from jasperfire import beetle, sources

INK, MUTED, RULE = "#1a1a1a", "#555555", "#d9d9d9"
# Sequential yellow-orange-red-brown for the five ordered severity classes.
SEVERITY_COLORS = ("#ffffd4", "#fed98e", "#fe9929", "#d95f0e", "#993404")
# Brown for drying, blue-green for wetting, matching the BrBG map ramp.
DECLINE_COLORS = ("#8c510a", "#d8a657", "#9c9c9c", "#35978f")
DECLINE_SHORT = ("Strong", "Moderate", "Slight", "Stable")
TOWN = (-118.0814, 52.8737)  # Jasper townsite, lon/lat
MARGIN_M = 2500.0
WIDTH_IN = 180 / 25.4


def _style():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": ["Arial", "DejaVu Sans"],
            "font.size": 7,
            "axes.titlesize": 7,
            "axes.labelsize": 7,
            "xtick.labelsize": 6.5,
            "ytick.labelsize": 6.5,
            "legend.fontsize": 6.5,
            "text.color": INK,
            "axes.labelcolor": INK,
            "axes.edgecolor": INK,
            "axes.linewidth": 0.5,
            "xtick.color": INK,
            "ytick.color": INK,
            "xtick.major.width": 0.5,
            "ytick.major.width": 0.5,
            "xtick.major.size": 2.5,
            "ytick.major.size": 2.5,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "pdf.fonttype": 42,
        }
    )
    return plt


def _layers(out_dir: Path) -> dict:
    """Everything the figure needs, on the 30 m analysis grid."""
    import rasterio

    with rasterio.open(out_dir / "ndmi_change.tif") as src:
        change = src.read(1)
        t = src.transform
        grid = beetle.Grid(src.crs.to_string(), t.c, t.f, t.a, src.width, src.height)
    with rasterio.open(out_dir / "study_mask.tif") as src:
        study = src.read(1).astype(bool)
    with rasterio.open(out_dir / "ndmi_annual.tif") as src:
        annual, years = src.read(), [int(y) for y in src.descriptions]
    severity = beetle.read_on_grid(str(out_dir / "severity.tif"), grid)
    rbr = beetle.read_on_grid(str(out_dir / "rbr.tif"), grid, "average")

    # Shaded relief on a slightly larger grid, so the fire sits in its valley.
    pad = int(MARGIN_M // grid.size)
    relief_grid = beetle.Grid(
        grid.crs,
        grid.left - pad * grid.size,
        grid.top + pad * grid.size,
        grid.size,
        grid.width + 2 * pad,
        grid.height + 2 * pad,
    )
    relief = beetle.read_on_grid(sources.HILLSHADE_URL, relief_grid, "bilinear")
    return {
        "grid": grid,
        "relief_grid": relief_grid,
        "relief": relief,
        "change": change,
        "study": study,
        "annual": annual,
        "years": years,
        "severity": severity,
        "rbr": rbr,
    }


def _extent(grid: beetle.Grid) -> tuple[float, float, float, float]:
    left, bottom, right, top = grid.bounds
    return (left, right, bottom, top)


def _stroke(width: float):
    from matplotlib import patheffects

    return patheffects.withStroke(linewidth=width, foreground="white")


def _base_map(ax, data: dict, perimeter, label: str, title: str) -> None:
    """Shaded relief, fire outline, town marker, scale bar and north arrow."""
    import geopandas as gpd
    from shapely.geometry import Point

    box = _extent(data["relief_grid"])
    ax.imshow(data["relief"], extent=box, cmap="gray", vmin=-60, vmax=255, interpolation="bilinear", zorder=0)
    ax.imshow(np.full_like(data["relief"], 255), extent=box, cmap="gray", vmin=0, vmax=255, alpha=0.5, zorder=1)
    perimeter.boundary.plot(ax=ax, color=INK, linewidth=0.4, zorder=4)
    ax.set_xlim(box[0], box[1])
    ax.set_ylim(box[2], box[3])
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("")
    ax.set_ylabel("")
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(True)
    ax.set_title(title, loc="left", pad=3)
    ax.text(-0.02, 1.012, label, transform=ax.transAxes, fontsize=9, fontweight="bold", ha="right", va="bottom")

    halo = [_stroke(1.6)]
    town = gpd.GeoSeries([Point(*TOWN)], crs=4326).to_crs(data["grid"].crs).iloc[0]
    ax.plot(town.x, town.y, marker="s", markersize=3, color="white", markeredgecolor=INK, markeredgewidth=0.6, zorder=6)
    ax.annotate("Jasper", (town.x, town.y), xytext=(3, 3), textcoords="offset points", zorder=6, path_effects=halo)

    # 5 km scale bar, lower left.
    x0, y0 = box[0] + 1200, box[2] + 1300
    ax.plot([x0, x0 + 5000], [y0, y0], color=INK, linewidth=1.4, solid_capstyle="butt", zorder=6)
    ax.text(x0 + 2500, y0 + 500, "5 km", ha="center", va="bottom", zorder=6, path_effects=halo)
    # North arrow, upper right. UTM grid north is within about 1 degree of true north here.
    xa, ya = box[1] - 1500, box[3] - 1400
    arrow = {"arrowstyle": "-|>", "color": INK, "linewidth": 0.8}
    ax.annotate("", (xa, ya), xytext=(xa, ya - 2600), arrowprops=arrow, zorder=6)
    ax.text(xa, ya - 3000, "N", ha="center", va="top", zorder=6, path_effects=halo)


def figure(out_dir: Path, data_dir: Path, fig_dir: Path) -> list[Path]:
    plt = _style()
    from matplotlib.colors import BoundaryNorm, ListedColormap, TwoSlopeNorm
    from matplotlib.patches import Patch

    from jasperfire import indices
    from jasperfire.perimeter import load_perimeter

    data = _layers(out_dir)
    grid = data["grid"]
    perimeter = load_perimeter(data_dir).to_crs(grid.crs)
    burn = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    stats = json.loads((out_dir / "comparison.json").read_text(encoding="utf-8"))
    extent = _extent(grid)

    fig = plt.figure(figsize=(WIDTH_IN, 4.45))
    outer = fig.add_gridspec(1, 3, width_ratios=[1, 1, 1.02], wspace=0.1, left=0.03, right=0.985, top=0.95, bottom=0.115)
    ax_a = fig.add_subplot(outer[0])
    ax_b = fig.add_subplot(outer[1])
    right = outer[2].subgridspec(2, 1, hspace=0.42)
    ax_c = fig.add_subplot(right[0])
    ax_d = fig.add_subplot(right[1])
    for ax in (ax_c, ax_d):  # leave room for the y labels of the two charts
        pos = ax.get_position()
        ax.set_position([pos.x0 + 0.055, pos.y0, pos.width - 0.055, pos.height])

    # (a) burn severity
    _base_map(ax_a, data, perimeter, "a", "Burn severity, 2024")
    classes = np.ma.masked_equal(data["severity"], indices.NODATA_CLASS)
    cmap, norm = ListedColormap(SEVERITY_COLORS), BoundaryNorm(range(6), 5)
    ax_a.imshow(classes, extent=extent, cmap=cmap, norm=norm, interpolation="nearest", alpha=0.88, zorder=2)
    areas = burn["area_ha_by_class"]
    total = sum(areas.values())
    handles = [
        Patch(facecolor=color, edgecolor=INK, linewidth=0.3, label=f"{name.capitalize()} ({areas[name] / total:.0%})")
        for name, color in zip(indices.SEVERITY_NAMES, SEVERITY_COLORS, strict=True)
    ]
    ax_a.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.012),
        ncol=2,
        frameon=False,
        handlelength=1.1,
        handleheight=0.9,
        columnspacing=1.0,
        labelspacing=0.3,
        borderaxespad=0.3,
    )

    # (b) NDMI change
    _base_map(ax_b, data, perimeter, "b", "Conifer NDMI change, 2013–15 to 2021–23")
    shown = np.ma.masked_where(~data["study"], data["change"])
    norm = TwoSlopeNorm(0, -0.25, 0.25)
    image = ax_b.imshow(shown, extent=extent, cmap="BrBG", norm=norm, interpolation="nearest", alpha=0.9, zorder=2)
    cax = ax_b.inset_axes([0.14, -0.055, 0.72, 0.022])
    bar = fig.colorbar(image, cax=cax, orientation="horizontal", ticks=[-0.2, -0.1, 0, 0.1, 0.2], extend="both")
    bar.outline.set_linewidth(0.4)
    bar.ax.tick_params(length=2, pad=1.5)
    bar.set_label("NDMI change", labelpad=1.5)

    # (c) yearly NDMI by decline class
    groups = np.where(data["study"], beetle.classify_decline(data["change"]), 255)
    years = data["years"]
    for period in (sources.BEETLE_BASELINE, sources.BEETLE_RECENT):
        ax_c.axvspan(period[0] - 0.4, period[-1] + 0.4, color="#f0f0f0", linewidth=0, zorder=0)
    for index, color in enumerate(DECLINE_COLORS):
        series = [float(np.nanmean(band[groups == index])) for band in data["annual"]]
        ax_c.plot(years, series, color=color, linewidth=1.2, marker="o", markersize=2.6, markeredgewidth=0, zorder=3)
        end = (years[-1], series[-1])
        ax_c.annotate(DECLINE_SHORT[index], end, xytext=(4, 0), textcoords="offset points", va="center")
    low = ax_c.get_ylim()[0]
    ax_c.text(sources.BEETLE_BASELINE[1], low, "baseline", ha="center", va="bottom", color=MUTED, fontsize=6)
    ax_c.text(sources.BEETLE_RECENT[1] - 0.6, low, "recent", ha="center", va="bottom", color=MUTED, fontsize=6)
    ax_c.set_xticks(years[::2])
    ax_c.set_xlim(years[0] - 0.6, years[-1] + 4.6)
    ax_c.set_ylabel("Mean summer NDMI")
    ax_c.set_title("Yearly NDMI by decline class", loc="left", pad=3)
    ax_c.text(-0.2, 1.03, "c", transform=ax_c.transAxes, fontsize=9, fontweight="bold", ha="right", va="bottom")

    # (d) distribution of RBR by decline class
    samples = [data["rbr"][(groups == i) & ~np.isnan(data["rbr"])] for i in range(len(DECLINE_COLORS))]
    parts = ax_d.violinplot(samples, positions=range(len(samples)), widths=0.82, showextrema=False)
    for body, color in zip(parts["bodies"], DECLINE_COLORS, strict=True):
        body.set_facecolor(color)
        body.set_edgecolor("none")
        body.set_alpha(0.75)
    for i, values in enumerate(samples):
        q1, median, q3 = np.percentile(values, [25, 50, 75])
        ax_d.plot([i, i], [q1, q3], color=INK, linewidth=1.6, solid_capstyle="butt", zorder=3)
        ax_d.plot(i, median, marker="o", markersize=3.2, color="white", markeredgecolor=INK, markeredgewidth=0.7, zorder=4)
        ax_d.text(i, -0.14, f"{median:.2f}", ha="center", va="top", fontsize=6.5)
    ax_d.set_xticks(range(len(samples)), DECLINE_SHORT)
    ax_d.set_ylim(-0.25, 1.0)
    ax_d.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax_d.axhline(0, color=RULE, linewidth=0.5, zorder=0)
    ax_d.set_ylabel("Relativized burn ratio (RBR)")
    ax_d.set_xlabel("Pre-fire decline class")
    rho = stats["spearman_ndmi_change_vs_rbr"]
    ax_d.set_title(f"Burn severity by decline class (ρ = {rho:.3f})", loc="left", pad=3)
    ax_d.text(-0.2, 1.03, "d", transform=ax_d.transAxes, fontsize=9, fontweight="bold", ha="right", va="bottom")

    fig_dir.mkdir(parents=True, exist_ok=True)
    paths = [fig_dir / "figure1.png", fig_dir / "figure1.pdf"]
    for path in paths:
        fig.savefig(path, dpi=300)
    plt.close(fig)
    return paths


def run(out_dir: Path, data_dir: Path, fig_dir: Path) -> list[Path]:
    return figure(out_dir, data_dir, fig_dir)
