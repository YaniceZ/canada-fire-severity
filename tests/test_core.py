"""Offline tests. Nothing here touches the network."""

from datetime import date

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

from firesev import beetle, blocks, canopy, compare, embeddings, fires, indices, lidar, model, sources
from firesev.perimeter import select_fire
from firesev.scenes import Scene, pick_scenes

# --- indices ---------------------------------------------------------------


def test_reflectance_removes_offset_only_when_needed():
    dn = np.array([0, 1000, 3000], dtype="uint16")
    raw = indices.to_reflectance(dn, offset_applied=False)
    done = indices.to_reflectance(dn, offset_applied=True)
    assert np.isnan(raw[0]) and np.isnan(done[0])
    assert raw[1:] == pytest.approx([0.0, 0.2])
    assert done[1:] == pytest.approx([0.1, 0.3])


def test_nbr_known_values_and_zero_denominator():
    out = indices.nbr(np.array([0.4, 0.1, 0.0]), np.array([0.1, 0.4, 0.0]))
    assert out[:2] == pytest.approx([0.6, -0.6])
    assert np.isnan(out[2])


def test_dnbr_is_positive_where_vegetation_was_lost():
    assert indices.dnbr(np.array([0.6]), np.array([-0.2]))[0] == pytest.approx(0.8)


def test_rbr_is_larger_for_sparse_stands_with_equal_dnbr():
    dense = indices.rbr(np.array([0.7]), np.array([0.3]))[0]
    sparse = indices.rbr(np.array([0.1]), np.array([-0.3]))[0]
    assert sparse > dense


def test_classify_severity_breaks_and_nodata():
    values = np.array([-0.2, 0.05, 0.10, 0.30, 0.50, 0.90, np.nan], dtype="float32")
    assert indices.classify_severity(values).tolist() == [0, 0, 1, 2, 3, 4, indices.NODATA_CLASS]


def test_class_areas_ignore_nodata():
    classes = np.array([0, 4, 4, indices.NODATA_CLASS], dtype="uint8")
    areas = indices.class_areas_ha(classes, pixel_area_m2=400.0)
    assert areas["high"] == pytest.approx(0.1, abs=0.05)
    assert sum(areas.values()) == pytest.approx(0.12, abs=0.1)


# --- spatial blocks --------------------------------------------------------


def test_points_in_the_same_square_share_a_block():
    ids = blocks.assign_blocks(np.array([10, 90, 110]), np.array([10, 90, 10]), block_size=100)
    assert ids[0] == ids[1] != ids[2]


def test_block_kfold_never_splits_a_block():
    rng = np.random.default_rng(1)
    ids = blocks.assign_blocks(rng.uniform(0, 5000, 2000), rng.uniform(0, 5000, 2000), block_size=500)
    tested = []
    for train, test in blocks.block_kfold(ids, k=5, seed=3):
        assert not set(ids[train]) & set(ids[test])
        tested.extend(test.tolist())
    assert sorted(tested) == list(range(2000))


def test_block_kfold_needs_enough_blocks():
    with pytest.raises(ValueError):
        list(blocks.block_kfold(np.array([0, 0, 1, 1]), k=5))


# --- lidar -----------------------------------------------------------------


def test_canopy_height_clips_negatives_and_keeps_nodata():
    dsm = np.array([[110.0, 99.5, -32767.0]])
    dtm = np.array([[100.0, 100.0, 100.0]])
    chm = lidar.canopy_height(dsm, dtm, nodata=-32767.0)
    assert chm[0, 0] == pytest.approx(10.0)
    assert chm[0, 1] == 0.0
    assert np.isnan(chm[0, 2])


def test_canopy_cover_counts_only_valid_cells():
    chm = np.array([5.0, 1.0, 12.0, np.nan], dtype="float32")
    assert lidar.canopy_cover(chm) == pytest.approx(2 / 3)


def test_hrdem_url_rejects_unknown_kind():
    assert sources.hrdem_url("2024", "dsm").endswith("PC-Jasper_2024-1m-dsm.tif")
    with pytest.raises(ValueError):
        sources.hrdem_url("2024", "chm")


# --- perimeter -------------------------------------------------------------


def test_select_fire_returns_one_row_or_fails():
    fires = gpd.GeoDataFrame({"GID": ["2024_1", "2024_451"]}, geometry=[box(0, 0, 1, 1), box(2, 2, 3, 3)], crs=3979)
    assert select_fire(fires, "2024_451").iloc[0]["GID"] == "2024_451"
    with pytest.raises(ValueError):
        select_fire(fires, "2024_999")


# --- scene choice ----------------------------------------------------------

START, END = date(2024, 7, 22), date(2024, 9, 7)


def scene(day: date, cloud: float, tile: str = "11UMU") -> Scene:
    return Scene(id=f"S2A_{tile}_{day:%Y%m%d}_0_L2A", day=day, cloud=cloud, assets={}, offset_applied=True)


def test_pick_scenes_takes_the_clearest_on_each_side():
    found = [
        scene(date(2024, 7, 5), 2.4),
        scene(date(2024, 7, 20), 0.5),
        scene(date(2024, 7, 23), 0.0),  # fire already burning, must not be the pre scene
        scene(date(2024, 8, 4), 0.1),  # fire still burning, must not be the post scene
        scene(date(2024, 9, 5), 0.3),
        scene(date(2024, 9, 10), 3.0),
    ]
    pre, post = pick_scenes(found, START, END)
    assert pre.day == date(2024, 7, 20)
    assert post.day == date(2024, 9, 5)


def test_pick_scenes_fails_when_everything_is_cloudy():
    with pytest.raises(ValueError):
        pick_scenes([scene(date(2024, 7, 20), 80.0), scene(date(2024, 9, 5), 0.0)], START, END)


# --- beetle proxy ----------------------------------------------------------


def test_landsat_reflectance_scale_and_fill():
    out = beetle.landsat_reflectance(np.array([0, 7273, 20000], dtype="uint16"))
    assert np.isnan(out[0])
    assert out[1:] == pytest.approx([0.0, 0.35], abs=1e-4)


def test_clear_mask_rejects_cloud_shadow_snow_and_water():
    clear = 1 << 6
    qa = np.array([clear, clear | 1 << 3, 1 << 4, 1 << 5, 1 << 7, 1], dtype="uint16")
    assert beetle.clear_mask(qa).tolist() == [True, False, False, False, False, False]


def test_composite_is_a_median_and_needs_enough_looks():
    stack = np.array([[0.2, 0.5], [0.4, np.nan], [0.9, np.nan]], dtype="float32")
    out = beetle.composite(stack, min_obs=2)
    assert out[0] == pytest.approx(0.4)
    assert np.isnan(out[1])


def test_period_change_and_drop_year():
    values = {2013: 0.30, 2014: 0.30, 2015: 0.28, 2016: 0.27, 2017: 0.15, 2018: 0.14, 2019: 0.15}
    annual = {year: np.array([value, 0.30], dtype="float32") for year, value in values.items()}
    change = beetle.period_change(annual, (2013, 2014, 2015), (2017, 2018, 2019))
    assert change.tolist() == pytest.approx([-0.15, 0.0], abs=1e-6)
    assert beetle.largest_drop_year(annual)[0] == 2017


def test_drop_year_is_unknown_when_a_year_is_missing():
    annual = {2013: np.array([0.3]), 2014: np.array([np.nan]), 2015: np.array([0.1])}
    assert beetle.largest_drop_year(annual)[0] == 0


def test_classify_decline_orders_classes_from_worst():
    change = np.array([-0.2, -0.07, -0.03, 0.01, np.nan], dtype="float32")
    assert beetle.classify_decline(change).tolist() == [0, 1, 2, 3, 255]


def test_grid_puts_pixel_centres_on_multiples_of_the_pixel_size():
    grid = beetle.grid_for((1000.0, 2000.0, 1100.0, 2100.0), "EPSG:32611", size=30.0)
    left, bottom, right, top = grid.bounds
    assert left <= 1000 and right >= 1100 and bottom <= 2000 and top >= 2100
    assert (left + 15) % 30 == 0 and (top - 15) % 30 == 0


# --- comparison ------------------------------------------------------------


def test_spearman_is_one_for_any_increasing_relation_and_handles_ties():
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert compare.spearman(a, a**3) == pytest.approx(1.0)
    assert compare.spearman(a, -a) == pytest.approx(-1.0)
    assert compare.ranks(np.array([10.0, 20.0, 10.0])).tolist() == [1.5, 3.0, 1.5]


def test_group_table_reports_area_and_medians():
    groups = np.array([0, 0, 1, 255])
    rows = compare.group_table(groups, ("a", "b"), {"x": np.array([1.0, 3.0, 10.0, 99.0])}, hectares=0.09)
    assert rows[0] == {"group": "a", "area_ha": 0.2, "x": 2.0}
    assert rows[1]["x"] == 10.0


# --- canopy loss -----------------------------------------------------------


def test_block_stats_gives_mean_height_and_cover_per_block():
    chm = np.zeros((4, 4), dtype="float32")
    chm[:2, :2] = [[10, 10], [0, 0]]  # half of this block is 10 m canopy
    chm[2:, 2:] = np.nan  # a block with no data
    height, cover = canopy.block_stats(chm, block=2)
    assert height[0, 0] == pytest.approx(5.0) and cover[0, 0] == pytest.approx(0.5)
    assert height[0, 1] == 0.0 and cover[0, 1] == 0.0
    assert np.isnan(height[1, 1]) and np.isnan(cover[1, 1])


def test_block_stats_ignores_missing_pixels_when_most_are_present():
    chm = np.array([[8.0, 8.0], [8.0, np.nan]], dtype="float32")
    height, cover = canopy.block_stats(chm, block=2)
    assert height[0, 0] == pytest.approx(8.0) and cover[0, 0] == pytest.approx(1.0)


def test_relative_loss_skips_low_canopy_and_allows_growth():
    before = np.array([20.0, 10.0, 1.0], dtype="float32")
    after = np.array([5.0, 12.0, 0.0], dtype="float32")
    out = canopy.relative_loss(before, after, floor=3.0)
    assert out[:2] == pytest.approx([0.75, -0.2])
    assert np.isnan(out[2])


# --- model inputs ----------------------------------------------------------


def test_slope_aspect_on_a_plane_that_drops_to_the_south():
    # Row 0 is the north edge. Elevation falls 10 m per 10 m cell going south: a 45 degree, south-facing slope.
    dem = np.repeat(np.arange(50, 0, -10, dtype="float32")[:, None], 5, axis=1)
    slope, northness, eastness = model.slope_aspect(dem, cell=10.0)
    assert slope[2, 2] == pytest.approx(45.0)
    assert northness[2, 2] == pytest.approx(-1.0)
    assert eastness[2, 2] == pytest.approx(0.0, abs=1e-6)


def test_slope_aspect_flat_ground_has_no_direction():
    slope, northness, eastness = model.slope_aspect(np.full((3, 3), 100.0), cell=30.0)
    assert slope[1, 1] == 0.0 and northness[1, 1] == 0.0 and eastness[1, 1] == 0.0


def test_nearest_detection_picks_the_closest_and_flags_far_points():
    x, y = [0.0, 95.0, 5000.0], [0.0, 0.0, 0.0]
    index, near = model.nearest_detection(x, y, [0.0, 100.0], [0.0, 0.0], max_distance=1000.0)
    assert index.tolist() == [0, 1, 1]
    assert near.tolist() == [True, True, False]


def test_feature_groups_are_nested_and_do_not_repeat():
    sizes = [len(model._features(i)) for i in range(len(model.FEATURE_GROUPS))]
    assert sizes == sorted(sizes) and len(set(model._features(3))) == sizes[-1]
    assert model._features(3)[-1] == "ndmi_change"


# --- embeddings ------------------------------------------------------------


def test_dequantize_follows_the_published_formula_and_masks_nodata():
    stored = np.array([127, -127, 0, 64, -128], dtype="int8")
    out = embeddings.dequantize(stored)
    assert out[:3] == pytest.approx([(127 / 127.5) ** 2, -((127 / 127.5) ** 2), 0.0])
    assert out[3] == pytest.approx((64 / 127.5) ** 2)
    assert np.isnan(out[4])


def test_dequantize_keeps_the_sign_and_the_range():
    out = embeddings.dequantize(np.arange(-127, 128, dtype="int16").astype("int8"))
    assert out.min() >= -1 and out.max() <= 1
    assert (np.sign(out) == np.sign(np.arange(-127, 128))).all()


# --- fire settings ---------------------------------------------------------


def test_every_fire_has_consistent_dates_and_periods():
    for fire in fires.FIRES.values():
        assert fire.start < fire.end
        first, last = fire.decline_years
        assert first <= min(fire.decline_baseline) and max(fire.decline_recent) <= last
        assert max(fire.decline_baseline) < min(fire.decline_recent)
        assert int(fire.start[:4]) > last  # the decline record must end before the fire year
        assert fire.embedding_years == (int(fire.start[:4]) - 1, int(fire.start[:4]))


def test_unknown_fire_is_rejected_with_the_known_names():
    with pytest.raises(ValueError, match="jasper"):
        fires.get("nowhere")


def test_pick_scenes_keeps_both_scenes_on_one_tile():
    found = [
        scene(date(2024, 7, 20), 0.0, "10UGA"),  # clearest pre scene, but no clear post scene on this tile
        scene(date(2024, 7, 18), 1.0, "11ULR"),
        scene(date(2024, 9, 5), 0.5, "11ULR"),
    ]
    pre, post = pick_scenes(found, START, END)
    assert pre.id.split("_")[1] == post.id.split("_")[1] == "11ULR"


def test_model_drops_lidar_predictors_and_target_when_the_table_has_none():
    columns = ["x", "y", "rbr", "elevation_m", "slope_deg", "northness", "eastness", "ndmi_2013_15", "ndmi_change"]
    columns += ["burn_hours", "wind_kmh", "humidity_pct", "fire_weather_index"]
    groups = model.groups_for(columns)
    assert groups["structure"] == ("ndmi_2013_15",)
    assert list(model.targets_for(columns)) == ["rbr"]
    assert model._features(3, groups)[-1] == "ndmi_change"
