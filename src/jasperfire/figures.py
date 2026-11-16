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


def _violins(ax, samples: list[np.ndarray], colors, labels, value_y: float) -> None:
    """Violins with an interquartile bar, a median dot and the median printed below."""
    parts = ax.violinplot(samples, positions=range(len(samples)), widths=0.82, showextrema=False)
    for body, color in zip(parts["bodies"], colors, strict=True):
        body.set_facecolor(color)
        body.set_edgecolor(INK)
        body.set_linewidth(0.3)
        body.set_alpha(0.8)
    for i, values in enumerate(samples):
        q1, median, q3 = np.percentile(values, [25, 50, 75])
        ax.plot([i, i], [q1, q3], color=INK, linewidth=1.6, solid_capstyle="butt", zorder=3)
        ax.plot(i, median, marker="o", markersize=3.2, color="white", markeredgecolor=INK, markeredgewidth=0.7, zorder=4)
        ax.text(i, value_y, f"{median:.2f}", ha="center", va="top", fontsize=6.5)
    ax.set_xticks(range(len(samples)), labels)


def canopy_figure(out_dir: Path, data_dir: Path, fig_dir: Path) -> list[Path]:
    """Figure 2: LiDAR canopy height loss as a map, by burn severity class and by decline class."""
    plt = _style()
    from rasterio.features import geometry_mask

    from jasperfire import canopy, indices
    from jasperfire.perimeter import load_perimeter

    data = _layers(out_dir)
    grid = data["grid"]
    perimeter = load_perimeter(data_dir).to_crs(grid.crs)
    stats = json.loads((out_dir / "canopy_summary.json").read_text(encoding="utf-8"))

    loss = beetle.read_on_grid(str(out_dir / "canopy_height_loss.tif"), grid)
    core = perimeter.geometry.union_all().buffer(-canopy.EDGE_M)
    inside = ~geometry_mask([core], out_shape=loss.shape, transform=grid.transform)
    keep = inside & data["study"] & ~np.isnan(loss) & ~np.isnan(data["rbr"])
    # A few cells grew or lost nothing; clip so the violins share one readable axis.
    shown_loss = np.clip(loss, -0.1, 1.0)

    fig = plt.figure(figsize=(WIDTH_IN, 4.45))
    outer = fig.add_gridspec(1, 2, width_ratios=[1, 2.0], wspace=0.2, left=0.03, right=0.985, top=0.95, bottom=0.115)
    ax_a = fig.add_subplot(outer[0])
    charts = outer[1].subgridspec(2, 1, hspace=0.5)
    ax_b = fig.add_subplot(charts[0])
    ax_c = fig.add_subplot(charts[1])

    _base_map(ax_a, data, perimeter, "a", "Canopy height loss, 2019 to 2024")
    extent = _extent(grid)
    shown = np.ma.masked_invalid(loss)
    image = ax_a.imshow(shown, extent=extent, cmap="YlOrBr", vmin=0, vmax=1, interpolation="nearest", alpha=0.9, zorder=2)
    cax = ax_a.inset_axes([0.14, -0.055, 0.72, 0.022])
    bar = fig.colorbar(image, cax=cax, orientation="horizontal", ticks=[0, 0.25, 0.5, 0.75, 1.0], extend="min")
    bar.outline.set_linewidth(0.4)
    bar.ax.tick_params(length=2, pad=1.5)
    bar.set_label("Share of 2019 canopy height lost", labelpad=1.5)

    samples = [shown_loss[keep & (data["severity"] == i)] for i in range(len(indices.SEVERITY_NAMES))]
    labels = [name.capitalize() for name in indices.SEVERITY_NAMES]
    _violins(ax_b, samples, SEVERITY_COLORS, labels, value_y=-0.2)
    rho = stats["spearman_height_loss_vs_rbr"]
    ax_b.set_title(f"LiDAR height loss by satellite severity class (ρ with RBR = {rho:.2f})", loc="left", pad=3)
    ax_b.set_xlabel("dNBR severity class")
    ax_b.text(-0.09, 1.03, "b", transform=ax_b.transAxes, fontsize=9, fontweight="bold", ha="right", va="bottom")

    groups = np.where(keep, beetle.classify_decline(data["change"]), 255)
    samples = [shown_loss[groups == i] for i in range(len(DECLINE_COLORS))]
    _violins(ax_c, samples, DECLINE_COLORS, DECLINE_SHORT, value_y=-0.2)
    unburned = [row["unburned"]["height_loss_share"] for row in stats["by_decline_class"]]
    for i, value in enumerate(unburned):
        ax_c.plot([i - 0.3, i + 0.3], [value, value], color=INK, linewidth=0.8, linestyle=(0, (3, 2)), zorder=5)
    ax_c.annotate(
        "unburned forest nearby",
        (len(unburned) - 1 + 0.3, unburned[-1]),
        xytext=(4, 0),
        textcoords="offset points",
        va="center",
        fontsize=6,
        color=MUTED,
    )
    ax_c.set_xlim(-0.6, len(unburned) - 1 + 1.25)
    rho = stats["spearman_height_loss_vs_ndmi_change"]
    ax_c.set_title(f"LiDAR height loss by pre-fire decline class (ρ = {rho:.2f})", loc="left", pad=3)
    ax_c.set_xlabel("Pre-fire decline class")
    ax_c.text(-0.09, 1.03, "c", transform=ax_c.transAxes, fontsize=9, fontweight="bold", ha="right", va="bottom")

    for ax in (ax_b, ax_c):
        ax.set_ylim(-0.38, 1.02)
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
        ax.axhline(0, color=RULE, linewidth=0.5, zorder=0)
        ax.set_ylabel("Height loss share")

    paths = [fig_dir / "figure2.png", fig_dir / "figure2.pdf"]
    for path in paths:
        fig.savefig(path, dpi=300)
    plt.close(fig)
    return paths


GROUP_COLORS = {"terrain": "#8a8a8a", "structure": "#1b7837", "fire weather": "#2166ac", "decline": "#8c510a"}
FEATURE_LABELS = {
    "elevation_m": "Elevation",
    "slope_deg": "Slope",
    "northness": "Northness",
    "eastness": "Eastness",
    "canopy_height_2019_m": "Canopy height 2019",
    "canopy_cover_2019": "Canopy cover 2019",
    "ndmi_2013_15": "NDMI 2013–15",
    "burn_hours": "Time of burning",
    "wind_kmh": "Wind speed",
    "humidity_pct": "Relative humidity",
    "fire_weather_index": "Fire weather index",
    "ndmi_change": "NDMI change",
}


def model_figure(out_dir: Path, fig_dir: Path) -> list[Path]:
    """Figure 3: validation scores, predictor importance and the decline effect, one row per target."""
    plt = _style()
    from matplotlib.patches import Patch

    from jasperfire import model

    summary = json.loads((out_dir / "model_summary.json").read_text(encoding="utf-8"))
    group_of = {name: group for group, names in model.FEATURE_GROUPS.items() for name in names}
    steps = ["Terrain", "+ structure", "+ fire\nweather", "+ decline"]

    fig, axes = plt.subplots(2, 3, figsize=(WIDTH_IN, 4.7), gridspec_kw={"width_ratios": [1, 1.05, 0.95]})
    fig.subplots_adjust(left=0.075, right=0.985, top=0.93, bottom=0.14, wspace=0.82, hspace=0.62)
    letters = iter("abcdef")
    for row, (target, title) in enumerate(model.TARGETS.items()):
        result = summary["targets"][target]
        ax_score, ax_importance, ax_effect = axes[row]

        x = range(len(steps))
        scored = (("random_cv", "Random split", MUTED, (0, (3, 2))), ("block_cv", "Spatial blocks", INK, "-"))
        for key, label, color, dash in scored:
            values = [step[key]["r2"] for step in result["steps"]]
            ax_score.plot(x, values, color=color, linestyle=dash, linewidth=1.2, marker="o", markersize=3)
            ax_score.annotate(label, (x[-1], values[-1]), xytext=(0, 5), textcoords="offset points", ha="right", color=color)
        ax_score.set_xticks(x, steps)
        ax_score.set_ylim(-0.03, 0.6)
        ax_score.set_ylabel("Cross-validated R²")
        ax_score.set_title(title, loc="left", pad=3)

        importance = result["permutation_importance"]
        names = list(importance)[::-1]
        colors = [GROUP_COLORS[group_of[name]] for name in names]
        labels, values = [FEATURE_LABELS[name] for name in names], [importance[name] for name in names]
        ax_importance.barh(labels, values, color=colors, height=0.7)
        ax_importance.set_xlabel("Drop in held-out R² when shuffled")
        ax_importance.tick_params(axis="y", length=0)
        ax_importance.set_title("Predictor importance", loc="left", pad=3)

        curve = result["partial_dependence_ndmi_change"]
        effect = np.array(curve["predicted"]) - np.mean(curve["predicted"])
        ax_effect.axhline(0, color=RULE, linewidth=0.5, zorder=0)
        ax_effect.plot(curve["ndmi_change"], effect, color=GROUP_COLORS["decline"], linewidth=1.4)
        spread = result["std"]
        ax_effect.set_ylim(-spread, spread)
        ax_effect.set_xlabel("NDMI change (negative = decline)", loc="right")
        ax_effect.set_ylabel("Change in prediction")
        ax_effect.set_title("Effect of decline alone", loc="left", pad=3)
        note = "axis spans ±1 s.d.\nof the target"
        ax_effect.text(0.97, 0.05, note, transform=ax_effect.transAxes, ha="right", color=MUTED, fontsize=6)

        for ax in axes[row]:
            letter = next(letters)
            ax.text(-0.02, 1.07, letter, transform=ax.transAxes, fontsize=9, fontweight="bold", ha="right", va="bottom")

    handles = [Patch(facecolor=color, label=group.capitalize()) for group, color in GROUP_COLORS.items()]
    box = {"handlelength": 1.1, "handleheight": 0.9, "columnspacing": 1.6}
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False, **box)

    paths = [fig_dir / "figure3.png", fig_dir / "figure3.pdf"]
    for path in paths:
        fig.savefig(path, dpi=300)
    plt.close(fig)
    return paths


def embedding_figure(out_dir: Path, fig_dir: Path) -> list[Path]:
    """Figure 4: hand-made predictors and the burn ratio against AlphaEarth embeddings."""
    plt = _style()

    summary = json.loads((out_dir / "embedding_summary.json").read_text(encoding="utf-8"))
    forecast = summary["forecast_severity_from_pre_fire_state"]
    panels = (
        ("Forecast burn ratio from pre-fire state", forecast["rbr"], ("hand-made", "embedding", "both")),
        ("Forecast LiDAR height loss from pre-fire state", forecast["height_loss"], ("hand-made", "embedding", "both")),
        (
            "Match LiDAR height loss after the fire",
            summary["match_lidar_height_loss_after_fire"],
            ("burn ratio", "embedding change", "both"),
        ),
    )
    labels = {
        "hand-made": "Hand-made predictors",
        "embedding": "AlphaEarth embedding",
        "embedding change": "AlphaEarth, before and after",
        "burn ratio": "Burn ratio (RBR)",
        "both": "Both together",
    }
    colors = {"hand-made": "#8a8a8a", "burn ratio": "#8a8a8a", "embedding": "#2166ac", "embedding change": "#2166ac"}

    fig, axes = plt.subplots(3, 1, figsize=(WIDTH_IN * 0.62, 4.3), sharex=True)
    fig.subplots_adjust(left=0.36, right=0.95, top=0.93, bottom=0.11, hspace=0.55)
    for ax, letter, (title, scores, order) in zip(axes, "abc", panels, strict=True):
        for i, key in enumerate(order):
            score = scores[key]
            ax.barh(i, score["r2"], color=colors.get(key, INK), height=0.62)
            ax.plot([score["min"], score["max"]], [i, i], color="white", linewidth=2.2, solid_capstyle="butt")
            ax.plot([score["min"], score["max"]], [i, i], color=INK, linewidth=0.9, solid_capstyle="butt")
            ax.text(score["max"] + 0.015, i, f"{score['r2']:.2f}", va="center")
        ax.set_yticks(range(len(order)), [labels[key] for key in order])
        ax.invert_yaxis()
        ax.tick_params(axis="y", length=0)
        ax.set_xlim(0, 1)
        ax.grid(axis="x", color=RULE, linewidth=0.5)
        ax.set_axisbelow(True)
        ax.set_title(title, loc="left", pad=3)
        ax.text(-0.57, 1.06, letter, transform=ax.transAxes, fontsize=9, fontweight="bold", va="bottom")
    axes[-1].set_xlabel("R² on held-out 3 km blocks (line: range over block splits)")

    paths = [fig_dir / "figure4.png", fig_dir / "figure4.pdf"]
    for path in paths:
        fig.savefig(path, dpi=300)
    plt.close(fig)
    return paths


def run(out_dir: Path, data_dir: Path, fig_dir: Path) -> list[Path]:
    paths = figure(out_dir, data_dir, fig_dir)
    if (out_dir / "canopy_summary.json").exists():
        paths += canopy_figure(out_dir, data_dir, fig_dir)
    if (out_dir / "model_summary.json").exists():
        paths += model_figure(out_dir, fig_dir)
    if (out_dir / "embedding_summary.json").exists():
        paths += embedding_figure(out_dir, fig_dir)
    return paths
