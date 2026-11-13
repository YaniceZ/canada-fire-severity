"""Download the national burned-area file and pull out one fire."""

from __future__ import annotations

import urllib.request
import zipfile
from pathlib import Path

import geopandas as gpd

from jasperfire import sources


def download_nbac(dest: Path, url: str = sources.NBAC_URL) -> Path:
    """Download and unzip the NBAC shapefile into `dest`. Returns the .shp path."""
    dest.mkdir(parents=True, exist_ok=True)
    existing = sorted(dest.glob("*.shp"))
    if existing:
        return existing[0]
    zip_path = dest / "nbac.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "jasperfire"})
    with urllib.request.urlopen(request, timeout=300) as response, open(zip_path, "wb") as f:
        while chunk := response.read(1 << 20):
            f.write(chunk)
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            if Path(member).name != member:
                raise ValueError(f"unexpected path inside the archive: {member}")
        z.extractall(dest)
    zip_path.unlink()
    return sorted(dest.glob("*.shp"))[0]


def select_fire(fires: gpd.GeoDataFrame, gid: str = sources.JASPER_GID) -> gpd.GeoDataFrame:
    """Return the single row whose GID matches. Fails if there is not exactly one."""
    match = fires[fires["GID"] == gid]
    if len(match) != 1:
        raise ValueError(f"expected one fire with GID {gid}, found {len(match)}")
    return match.reset_index(drop=True)


def load_perimeter(data_dir: Path) -> gpd.GeoDataFrame:
    """Fetch the Jasper fire perimeter, caching it as GeoPackage under `data_dir`."""
    cached = data_dir / "jasper_perimeter.gpkg"
    if cached.exists():
        return gpd.read_file(cached)
    shp = download_nbac(data_dir / "nbac")
    fire = select_fire(gpd.read_file(shp))
    fire.to_file(cached, driver="GPKG")
    return fire
