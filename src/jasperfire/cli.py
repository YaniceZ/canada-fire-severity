"""Command line entry point: `jasperfire <step>`."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from jasperfire import beetle, burn, compare, figures, lidar, perimeter, scenes, sources


def _pick(fire):
    start, end = date.fromisoformat(sources.FIRE_START), date.fromisoformat(sources.FIRE_END)
    bbox = tuple(fire.to_crs(4326).total_bounds)
    found = scenes.search(bbox, start - timedelta(days=30), end + timedelta(days=30))
    return scenes.pick_scenes(found, start, end)


def cmd_perimeter(args) -> None:
    fire = perimeter.load_perimeter(args.data)
    row = fire.iloc[0]
    print(f"{row['GID']}: {row['POLY_HA']:,.0f} ha, mapped from {row['FIREMAPS']}")
    print("bounds (lon/lat):", [round(float(v), 4) for v in fire.to_crs(4326).total_bounds])


def cmd_scenes(args) -> None:
    pre, post = _pick(perimeter.load_perimeter(args.data))
    for label, s in (("pre", pre), ("post", post)):
        print(f"{label:4} {s.id}  {s.day}  cloud {s.cloud:.2f}%")


def cmd_dnbr(args) -> None:
    fire = perimeter.load_perimeter(args.data)
    pre, post = _pick(fire)
    print(json.dumps(burn.run(fire, pre, post, args.out), indent=2))


def cmd_beetle(args) -> None:
    print(json.dumps(beetle.run(perimeter.load_perimeter(args.data), args.data, args.out), indent=2))


def cmd_compare(args) -> None:
    print(json.dumps(compare.run(args.out), indent=2))


def cmd_figures(args) -> None:
    for path in figures.run(args.out, args.data, Path("docs")):
        print(path)


def cmd_canopy(args) -> None:
    """Canopy cover before and after the fire in one square window."""
    import geopandas as gpd
    from shapely.geometry import Point

    centre = gpd.GeoSeries([Point(args.lon, args.lat)], crs=4326).to_crs(sources.HRDEM_CRS).iloc[0]
    half = args.size / 2
    bounds = (centre.x - half, centre.y - half, centre.x + half, centre.y + half)
    for year in args.years:
        chm = lidar.read_chm(year, bounds)
        print(f"{year}: canopy cover above 2 m = {lidar.canopy_cover(chm):.1%}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jasperfire", description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"), help="download cache")
    parser.add_argument("--out", type=Path, default=Path("out"), help="results folder")
    sub = parser.add_subparsers(dest="step", required=True)
    sub.add_parser("perimeter", help="download the fire perimeter").set_defaults(func=cmd_perimeter)
    sub.add_parser("scenes", help="show the Sentinel-2 scenes that would be used").set_defaults(func=cmd_scenes)
    sub.add_parser("dnbr", help="compute dNBR, RBR and severity classes").set_defaults(func=cmd_dnbr)
    sub.add_parser("beetle", help="map pre-fire conifer decline from Landsat NDMI").set_defaults(func=cmd_beetle)
    sub.add_parser("compare", help="burn severity by pre-fire decline class").set_defaults(func=cmd_compare)
    sub.add_parser("figures", help="draw the README figure into docs/").set_defaults(func=cmd_figures)
    canopy = sub.add_parser("canopy", help="LiDAR canopy cover in a window, per survey year")
    canopy.add_argument("--lon", type=float, required=True)
    canopy.add_argument("--lat", type=float, required=True)
    canopy.add_argument("--size", type=float, default=500.0, help="window edge in metres")
    canopy.add_argument("--years", nargs="+", default=["2019", "2024"], choices=sorted(sources.HRDEM_PROJECTS))
    canopy.set_defaults(func=cmd_canopy)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
