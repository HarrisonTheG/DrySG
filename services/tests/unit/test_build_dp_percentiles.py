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


LOC = {"S1": (1.30, 103.80), "S2": (1.40, 103.90)}
GAUGES = {"S1": (1.30, 103.80), "R9": (1.401, 103.901)}


def samples(temp, humidity, wind, rain, station_locations=LOC, rain_locations=GAUGES):
    return build.station_samples(
        temp, humidity, wind, rain,
        station_locations=station_locations, rain_locations=rain_locations,
    )


def test_one_sample_per_station_with_its_own_readings_and_knots_converted():
    out = samples(
        {"S1": 30, "S2": 32}, {"S1": 70, "S2": 60}, {"S1": 4, "S2": 8}, {"S1": 0, "R9": 0}
    )
    assert [s["station"] for s in out] == ["S1", "S2"]
    assert out[0]["dp"] == pytest.approx(drying_potential(30, 70, 4 * KNOTS_TO_MS))
    assert out[1]["dp"] == pytest.approx(drying_potential(32, 60, 8 * KNOTS_TO_MS))


def test_rain_only_drops_the_station_where_it_rained():
    # S1's own gauge is wet; S2 has no gauge, so its nearest gauge (R9, dry) decides.
    out = samples({"S1": 30, "S2": 30}, {"S1": 70, "S2": 70}, {"S1": 4, "S2": 4},
                  {"S1": 0.2, "R9": 0})
    assert [s["station"] for s in out] == ["S2"]
    # Now R9 is wet, so S2 is dropped and S1 (dry own gauge) is kept.
    out = samples({"S1": 30, "S2": 30}, {"S1": 70, "S2": 70}, {"S1": 4, "S2": 4},
                  {"S1": 0, "R9": 1.0})
    assert [s["station"] for s in out] == ["S1"]


def test_station_rain_falls_back_to_nearest_gauge_with_data():
    rain = {"S1": None, "R9": 0.0}
    assert build.station_rain("S2", rain, LOC["S2"], GAUGES) == 0.0
    assert build.station_rain("S1", rain, LOC["S1"], GAUGES) == 0.0  # own gauge empty
    assert build.station_rain("S2", {}, LOC["S2"], GAUGES) is None
    assert build.station_rain("S9", {"R9": 0.0}, None, GAUGES) is None


@pytest.mark.parametrize(
    ("temp", "humidity", "wind", "rain"),
    [
        ({"S1": None}, {"S1": 70}, {"S1": 4}, {"S1": 0}),  # missing input
        ({"S1": 30}, {}, {"S1": 4}, {"S1": 0}),  # station absent from one feed
        ({"S1": 30}, {"S1": 70}, {"S1": 4}, {}),  # no rain data anywhere
    ],
)
def test_incomplete_stations_are_skipped(temp, humidity, wind, rain):
    assert samples(temp, humidity, wind, rain) == []


def test_collect_samples_keeps_only_complete_rain_free_five_minute_marks():
    def feed(value, ts):
        return {t: {"S1": value} for t in ts}

    ts = ["2024-01-01T12:00:00+08:00", "2024-01-01T12:03:00+08:00", "2024-01-01T12:05:00+08:00"]
    readings = {
        "air-temperature": feed(30, ts),
        "relative-humidity": feed(70, ts),
        "wind-speed": feed(4, ts),
        "rainfall": {ts[0]: {"S1": 0}, ts[1]: {"S1": 0}, ts[2]: {"S1": 1.0}},
    }
    locations = {"wind-speed": {"S1": LOC["S1"]}, "rainfall": {"S1": LOC["S1"]}}
    out = build.collect_samples(readings, locations)
    assert [s["timestamp"] for s in out] == [ts[0]]  # 12:03 is off-grid, 12:05 rained


def test_input_medians():
    rows = [{"temp_c": t, "humidity_pct": 70.0, "wind_knots": 4.0} for t in (28.0, 30.0, 31.0)]
    assert build.input_medians(rows) == {"temp_c": 30.0, "humidity_pct": 70.0, "wind_knots": 4.0}
    with pytest.raises(ValueError):
        build.input_medians([])


def test_percentile_table_nearest_rank():
    table = build.percentile_table([float(i) for i in range(101)])
    assert table == tuple(float(k) for k in range(101))
    assert build.percentile_table([2.0]) == (2.0,) * 101
    with pytest.raises(ValueError):
        build.percentile_table([])


def test_render_module_round_trips_and_validates():
    table = tuple(round(k / 10, 3) for k in range(101))
    namespace: dict = {}
    medians = {"temp_c": 28.4, "humidity_pct": 79.0, "wind_knots": 4.6}
    exec(build.render_module(table, year=2024, samples=1234, medians=medians), namespace)
    assert namespace["DP_PERCENTILES"] == table
    assert (namespace["DP_PERCENTILES_YEAR"], namespace["DP_PERCENTILES_SAMPLES"]) == (2024, 1234)
    assert namespace["DP_PERCENTILES_SOURCE"] == "data.gov.sg"
    assert namespace["DP_INPUT_MEDIANS"] == medians
    with pytest.raises(ValueError):
        build.render_module(table[:-1], year=2024, samples=1, medians=medians)
    with pytest.raises(ValueError):
        build.render_module(tuple(reversed(table)), year=2024, samples=1, medians=medians)
    with pytest.raises(ValueError):
        build.render_module(table, year=2024, samples=1, medians={"temp_c": 28.0})
