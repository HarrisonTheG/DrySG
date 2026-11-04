"""Replaying archived NEA history through the live scoring and alert rules."""

from datetime import timedelta

from drysg.core.models import Band
from drysg.experience.replay import (
    FRAME_COUNT,
    area_series,
    classify,
    simulate_alerts,
    windows,
)
from tests.experience_data import COOL_HUMID, START, make_day


def one_hour(**kwargs):
    series = area_series(make_day(**kwargs))["Bishan"]
    return next(windows(series))


def test_every_mark_is_scored_with_the_live_rules_and_linked_to_the_previous():
    series = area_series(make_day(rain_from=8))["Bishan"]
    assert len(series) == 14
    assert [s.result.veto for s in series] == [False] * 8 + [True] * 6
    assert series[0].prev_score is None
    assert series[1].prev_score == series[0].result.score
    assert series[8].prev_band is Band.HANG_NOW
    assert series[8].result.band is Band.DO_NOT_HANG  # rain vetoes whatever the score
    assert series[0].conditions.forecast.text == "Partly Cloudy (Day)"


def test_forecast_issued_before_the_moment_is_used_despite_the_archive_stamp():
    # The archive stamps forecasts with a 2025 re-publication date; normalize_weather
    # would otherwise reject them as issued in the future.
    series = area_series(make_day())["Bishan"]
    assert all(s.conditions.forecast is not None for s in series)


def test_a_mark_missing_a_feed_is_skipped_and_breaks_the_run():
    series = area_series(make_day(marks=20, skip={9}))["Bishan"]
    assert len(series) == 19
    assert series[9].prev_score is None  # first mark after the gap
    assert series[10].prev_score == series[9].result.score
    assert len(list(windows(series))) == 0  # no 13 consecutive marks left


def test_windows_are_runs_of_consecutive_five_minute_snapshots():
    series = area_series(make_day(marks=15))["Bishan"]
    runs = list(windows(series))
    assert len(runs) == 15 - FRAME_COUNT + 1
    for run in runs:
        assert run[-1].updated_at - run[0].updated_at == timedelta(minutes=5 * (FRAME_COUNT - 1))


def test_rain_arriving_alerts_at_the_first_rain_frame():
    run = one_hour(rain_from=6)
    [(frame, message)] = simulate_alerts(run)
    assert frame == 6
    assert message.new_veto
    assert message.prev_band is Band.HANG_NOW
    assert message.snapshot_ts == START + timedelta(minutes=30)
    assert classify(run, [(frame, message)]) == "rain_arrives"


def test_steady_good_weather_never_alerts():
    run = one_hour()
    assert simulate_alerts(run) == []
    assert classify(run, []) == "hang_now"


def test_steady_poor_weather_is_poor_drying():
    run = one_hour(weather=COOL_HUMID)
    assert classify(run, simulate_alerts(run)) == "poor_drying"


def test_rain_all_hour_is_raining():
    run = one_hour(rain_from=0)
    assert classify(run, simulate_alerts(run)) == "raining"


def test_a_stray_alert_spoils_a_raining_hour():
    run = one_hour(rain_from=0)
    assert classify(run, [(3, None)]) is None
