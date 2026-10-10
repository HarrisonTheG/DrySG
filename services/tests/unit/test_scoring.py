"""Drying score rules (also serves as the 5.1 'score outputs vs predefined rules' test)."""

from datetime import datetime, timedelta

import pytest

from drysg.core import scoring
from drysg.core.models import Band, Conditions, Forecast, PsiLevel, Reading
from drysg.core.scoring import (
    InsufficientDataError,
    band_for,
    compute_score,
    forecast_subscore,
    humidity_subscore,
    psi_caution,
    rain_subscore,
    temperature_subscore,
    wind_subscore,
)

NOW = datetime.fromisoformat("2026-10-04T12:00:00+08:00")


def make_conditions(
    rain=0.0, humidity=60.0, temp=32.0, wind=10.0, forecast="Fair (Day)", psi=None
) -> Conditions:
    """Defaults are ideal drying conditions. Pass None to drop an input."""

    def r(v):
        return None if v is None else Reading(value=v, observed_at=NOW)

    return Conditions(
        rainfall_mm=r(rain),
        humidity_pct=r(humidity),
        temp_c=r(temp),
        wind_knots=r(wind),
        forecast=None
        if forecast is None
        else Forecast(text=forecast, valid_from=NOW, valid_to=NOW + timedelta(hours=2)),
        psi=r(psi),
    )


def test_weights_sum_to_one():
    total = (
        scoring.WEIGHT_RAIN
        + scoring.WEIGHT_HUMIDITY
        + scoring.WEIGHT_WIND
        + scoring.WEIGHT_TEMPERATURE
    )
    assert total == pytest.approx(1.0)


# --- Sub-scores ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("pct", "expected"), [(40, 100), (60, 100), (77.5, 50), (95, 0), (100, 0)]
)
def test_humidity_subscore(pct, expected):
    assert humidity_subscore(pct) == pytest.approx(expected)


@pytest.mark.parametrize(("c", "expected"), [(20, 0), (24, 0), (28, 50), (32, 100), (36, 100)])
def test_temperature_subscore(c, expected):
    assert temperature_subscore(c) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("knots", "expected"),
    [
        (0, 30),
        (2.5, 65),
        (5, 100),
        (10, 100),
        (15, 100),
        (20, 70),
        (25, 40),
        (30, 20),
        (35, 0),
        (45, 0),
    ],
)
def test_wind_subscore(knots, expected):
    assert wind_subscore(knots) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fair", 100),
        ("Fair (Day)", 100),
        ("Fair (Night)", 100),
        ("Fair and Warm", 100),
        ("Partly Cloudy (Night)", 85),
        ("Windy", 80),
        ("Slightly Hazy", 70),
        ("Cloudy", 60),
        ("Hazy", 60),
        ("Mist", 40),
        ("Fog", 30),
        ("Some New Category", scoring.UNKNOWN_FORECAST_SCORE),
        ("Light Rain", 0),
        ("Passing Showers", 0),
        ("Thundery Showers", 0),
        ("Heavy Thundery Showers with Gusty Winds", 0),
        ("Drizzle", 0),
    ],
)
def test_forecast_subscore(text, expected):
    assert forecast_subscore(text) == expected


@pytest.mark.parametrize(
    ("rain_mm", "forecast", "expected_score", "vetoed"),
    [
        (0.0, "Fair (Day)", 100, False),
        (0.0, "Cloudy", 60, False),
        (0.2, "Fair (Day)", 0, True),  # observed rain beats a fair forecast
        (0.0, "Thundery Showers", 0, True),  # forecast rain vetoes even if dry now
        (None, "Partly Cloudy (Day)", 85, False),  # station missing: forecast only
        (0.0, None, scoring.DRY_NO_FORECAST_SCORE, False),  # forecast missing
        (None, None, None, False),
    ],
)
def test_rain_subscore(rain_mm, forecast, expected_score, vetoed):
    score, reason = rain_subscore(rain_mm, forecast)
    assert score == expected_score
    assert (reason is not None) is vetoed


def test_veto_reasons_are_readable():
    assert rain_subscore(1.2, None)[1] == (
        "Rain detected at nearby station (1.2 mm in last 5 min)"
    )
    assert rain_subscore(0.0, "Heavy Thundery Showers with Gusty Winds (Day)")[1] == (
        "Heavy Thundery Showers with Gusty Winds forecast for the next 2 hours"
    )


# --- Bands and PSI ------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (100, Band.HANG_NOW),
        (70, Band.HANG_NOW),
        (69, Band.WAIT),
        (50, Band.WAIT),
        (49, Band.DO_NOT_HANG),
        (0, Band.DO_NOT_HANG),
    ],
)
def test_band_thresholds(score, band):
    assert band_for(score) is band


@pytest.mark.parametrize(
    ("psi", "level"),
    [
        (None, None),
        (0, PsiLevel.GOOD),
        (50, PsiLevel.GOOD),
        (51, PsiLevel.MODERATE),
        (100, PsiLevel.MODERATE),
        (101, PsiLevel.UNHEALTHY),
        (200, PsiLevel.UNHEALTHY),
        (201, PsiLevel.VERY_UNHEALTHY),
        (300, PsiLevel.VERY_UNHEALTHY),
        (301, PsiLevel.HAZARDOUS),
    ],
)
def test_psi_caution(psi, level):
    assert psi_caution(psi) is level


# --- compute_score end to end -------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "score", "band", "veto"),
    [
        # Ideal conditions.
        ({}, 100, Band.HANG_NOW, False),
        # Typical sunny SG afternoon: 0.3*100 + 0.4*58.6 + 0.1*88.8 + 0.2*87.5 = 79.82
        (
            {"humidity": 74.5, "wind": 4.2, "temp": 31.0, "forecast": "Fair (Day)"},
            80,
            Band.HANG_NOW,
            False,
        ),
        # Overcast and humid: 0.3*60 + 0.4*40 + 0.1*59.4 + 0.2*62.5 = 52.44
        (
            {"humidity": 81.0, "wind": 2.1, "temp": 29.0, "forecast": "Cloudy"},
            52,
            Band.WAIT,
            False,
        ),
        # Humid night, dry forecast (real NEA fixture values, 2026-10-04 00:45):
        # 0.3*100 + 0.4*37.1 + 0.1*69.2 + 0.2*62.5 = 64.28 -> must not be Hang Now.
        (
            {"humidity": 82.0, "wind": 2.8, "temp": 29.0, "forecast": "Fair (Night)"},
            64,
            Band.WAIT,
            False,
        ),
        # Thunderstorm: 0 + 0.4*11.4 + 0.1*100 + 0.2*37.5 = 22.06
        (
            {"humidity": 91.0, "wind": 9.8, "temp": 27.0, "forecast": "Thundery Showers"},
            22,
            Band.DO_NOT_HANG,
            True,
        ),
        # Observed rain with otherwise perfect conditions: 70, capped to 49 by veto.
        ({"rain": 0.2}, scoring.VETO_SCORE_CAP, Band.DO_NOT_HANG, True),
    ],
)
def test_compute_score(kwargs, score, band, veto):
    result = compute_score(make_conditions(**kwargs))
    assert (result.score, result.band, result.veto) == (score, band, veto)
    assert result.degraded is False


def test_psi_does_not_affect_score():
    clean = compute_score(make_conditions(psi=20))
    hazy = compute_score(make_conditions(psi=350))
    assert clean.score == hazy.score
    assert (clean.psi_caution, hazy.psi_caution) == (PsiLevel.GOOD, PsiLevel.HAZARDOUS)


def test_missing_factor_redistributes_weight_and_flags_degraded():
    result = compute_score(make_conditions(humidity=None))
    assert result.sub_scores.humidity is None
    assert result.score == 100  # remaining factors are all perfect
    assert result.degraded is True


def test_missing_rain_info_caps_below_hang_now():
    result = compute_score(make_conditions(rain=None, forecast=None))
    assert result.score == scoring.MISSING_RAIN_SCORE_CAP
    assert result.band is Band.WAIT
    assert result.degraded is True


def test_missing_forecast_uses_dry_fallback_and_flags_degraded():
    # 0.3*70 + 0.7*100 = 91
    result = compute_score(make_conditions(forecast=None))
    assert result.score == 91
    assert result.degraded is True


def test_all_inputs_missing_raises():
    with pytest.raises(InsufficientDataError):
        compute_score(
            make_conditions(rain=None, humidity=None, temp=None, wind=None, forecast=None)
        )


def test_score_always_in_range_across_input_grid():
    for rain in (0.0, 5.0):
        for humidity in (30, 80, 100):
            for temp in (20, 28, 38):
                for wind in (0, 10, 50):
                    for forecast in ("Fair (Day)", "Cloudy", "Heavy Rain", "Mist"):
                        result = compute_score(
                            make_conditions(rain, humidity, temp, wind, forecast)
                        )
                        assert 0 <= result.score <= 100
                        assert result.band is band_for(result.score)
                        if result.veto:
                            assert result.band is Band.DO_NOT_HANG
