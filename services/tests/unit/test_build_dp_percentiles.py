"""Pure logic of services/scripts/build_dp_percentiles.py (no network)."""

import importlib.util
from pathlib import Path

import pytest

from drysg.core.scoring import KNOTS_TO_MS, drying_potential

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_dp_percentiles.py"
spec = importlib.util.spec_from_file_location("build_dp_percentiles", SCRIPT)
assert spec is not None and spec.loader is not None
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def test_page_offset_one_minute_feed():
    assert build.page_offset(23 * 60 + 59, 1) == 0  # newest reading
    assert build.page_offset(23 * 60 + 35, 1) == 0  # 25th newest
    assert build.page_offset(23 * 60 + 34, 1) == 25
    assert build.page_offset(0, 1) == 1425  # 1440 readings: last page


def test_page_offset_five_minute_feed():
    assert build.page_offset(23 * 60 + 55, 5) == 0
    assert build.page_offset(21 * 60 + 55, 5) == 0  # 25 readings span 125 minutes
    assert build.page_offset(21 * 60 + 50, 5) == 25


def test_windows_rotate_through_every_hour():
    hours = {m // 60 for d in range(24) for m in build.window_minutes(d, 3)}
    assert hours == set(range(24))
    assert len(build.window_minutes(0, 4)) == 4


def test_mark_dp_uses_station_median_and_converts_knots():
    dp = build.mark_dp([30, 31, 99], [70, 72, 71], [4, 6, 5], [0, 0, None])
    assert dp == pytest.approx(drying_potential(31, 71, 5 * KNOTS_TO_MS))


@pytest.mark.parametrize(
    ("rain", "temp"),
    [([0, 0.2], [30]), ([], [30]), ([None], [30]), ([0], []), ([0], [None])],
)
def test_mark_dp_excludes_rain_and_missing_data(rain, temp):
    assert build.mark_dp(temp, [70], [4], rain) is None


def test_collect_dp_keeps_only_complete_rain_free_five_minute_marks():
    def feed(value, ts):
        return {t: [value] for t in ts}

    ts = ["2024-01-01T12:00:00+08:00", "2024-01-01T12:03:00+08:00", "2024-01-01T12:05:00+08:00"]
    readings = {
        "air-temperature": feed(30, ts),
        "relative-humidity": feed(70, ts),
        "wind-speed": feed(4, ts),
        "rainfall": {ts[0]: [0], ts[1]: [0], ts[2]: [1.0]},
    }
    assert len(build.collect_dp(readings)) == 1  # 12:03 is off-grid, 12:05 rained


def test_percentile_table_nearest_rank():
    table = build.percentile_table([float(i) for i in range(101)])
    assert table == tuple(float(k) for k in range(101))
    assert build.percentile_table([2.0]) == (2.0,) * 101
    with pytest.raises(ValueError):
        build.percentile_table([])


def test_render_module_round_trips_and_validates():
    table = tuple(round(k / 10, 3) for k in range(101))
    namespace: dict = {}
    exec(build.render_module(table, year=2024, marks=1234), namespace)
    assert namespace["DP_PERCENTILES"] == table
    assert (namespace["DP_PERCENTILES_YEAR"], namespace["DP_PERCENTILES_MARKS"]) == (2024, 1234)
    assert namespace["DP_PERCENTILES_SOURCE"] == "data.gov.sg"
    with pytest.raises(ValueError):
        build.render_module(table[:-1], year=2024, marks=1)
    with pytest.raises(ValueError):
        build.render_module(tuple(reversed(table)), year=2024, marks=1)
