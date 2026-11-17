"""Find one clear Sentinel-2 scene before the fire and one after it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from jasperfire import sources


@dataclass(frozen=True)
class Scene:
    id: str
    day: date
    cloud: float  # percent of the tile
    assets: dict[str, str]  # asset key -> URL
    offset_applied: bool  # False means reflectance = (DN - 1000) / 10000


def pick_scenes(
    scenes: list[Scene],
    fire_start: date,
    fire_end: date,
    max_cloud: float = 10.0,
    pre_days: int = 30,
    post_days: int = 30,
) -> tuple[Scene, Scene]:
    """Choose the pre-fire and post-fire scenes.

    Both are the least cloudy scene in their window. The windows sit close to the
    fire on purpose: a pre-fire scene from weeks earlier would mix seasonal change
    into the difference.
    """
    clear = [s for s in scenes if s.cloud <= max_cloud]
    pre = [s for s in clear if fire_start - timedelta(days=pre_days) <= s.day < fire_start]
    post = [s for s in clear if fire_end - timedelta(days=7) <= s.day <= fire_end + timedelta(days=post_days)]
    if not pre:
        raise ValueError(f"no scene under {max_cloud}% cloud in the {pre_days} days before {fire_start}")
    if not post:
        raise ValueError(f"no scene under {max_cloud}% cloud around {fire_end}")

    def by_cloud(s: Scene) -> tuple[float, str]:
        return (s.cloud, s.id)

    # Where a fire sits in the overlap of two tiles, both scenes must come from the same
    # tile, or the two images would be on different grids. Take the tile whose pair is clearest.
    pairs = []
    for name in sorted({tile(s) for s in pre} & {tile(s) for s in post}):
        best_pre = min((s for s in pre if tile(s) == name), key=by_cloud)
        best_post = min((s for s in post if tile(s) == name), key=by_cloud)
        pairs.append((max(best_pre.cloud, best_post.cloud), name, best_pre, best_post))
    if not pairs:
        raise ValueError("no Sentinel-2 tile has a clear scene both before and after the fire")
    _, _, best_pre, best_post = min(pairs, key=lambda pair: pair[:2])
    return best_pre, best_post


def tile(scene: Scene) -> str:
    """Sentinel-2 tile code from a scene id such as S2B_11UMU_20240707_0_L2A."""
    parts = scene.id.split("_")
    return parts[1] if len(parts) > 1 else scene.id


def search(bbox: tuple[float, float, float, float], start: date, end: date) -> list[Scene]:
    """Search Earth Search for Sentinel-2 L2A scenes that contain the whole bbox."""
    from pystac_client import Client
    from shapely.geometry import box, shape

    target = box(*bbox)
    client = Client.open(sources.STAC_URL)
    result = client.search(collections=[sources.S2_COLLECTION], bbox=bbox, datetime=f"{start}/{end}")
    scenes = []
    for item in result.items():
        if not shape(item.geometry).contains(target):
            continue  # a scene that covers only part of the fire would leave a gap
        scenes.append(
            Scene(
                id=item.id,
                day=item.datetime.date(),
                cloud=float(item.properties["eo:cloud_cover"]),
                assets={key: asset.href for key, asset in item.assets.items()},
                offset_applied=bool(item.properties.get("earthsearch:boa_offset_applied", False)),
            )
        )
    return scenes
