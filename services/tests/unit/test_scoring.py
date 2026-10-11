"""Drying score rules: Penman drying potential, percentile score, rain veto, PSI warning."""

from datetime import datetime, timedelta

import pytest

from drysg.core import dp_percentiles, scoring
from drysg.core.models import Band, Conditions, Forecast, PsiLevel, Reading
from drysg.core.scoring import (
    InsufficientDataError,
    band_for,
    compute_score,
    dp_to_score,
    drying_potential,
    percentile_description,
    psi_caution,
    rain_veto_reason,
    saturation_vapour_pressure,
    vapour_pressure_deficit,
)

NOW = datetime.fromisoformat("2026-10-04T12:00:00+08:00")


def make_conditions(
    rain: float | None = 0.0,
    humidity: float | None = 60.0,
    temp: float | None = 32.0,
    wind: float | None = 10.0,
    forecast: str | None = "Fair (Day)",
    psi: float | None = None,
) -> Conditions:
    """Wind is in knots. Defaults are dry, warm and breezy. Pass None to drop an input."""

    def r(v: float | None) -> Reading | None:
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


# --- Percentile table ---------------------------------------------------------


def test_percentile_table_shape():
    table = dp_percentiles.DP_PERCENTILES
    assert len(table) == 101
    assert all(a <= b for a, b in zip(table, table[1:], strict=False))
    assert table[0] >= 0


# --- Physics ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("temp_c", "kpa"),
    [(0, 0.6108), (20, 2.338), (25, 3.168), (30, 4.243)],  # standard Tetens/FAO-56 values
)
def test_saturation_vapour_pressure(temp_c, kpa):
    assert saturation_vapour_pressure(temp_c) == pytest.approx(kpa, abs=0.002)


def test_vpd_scales_with_dryness_and_clamps_humidity():
    es = saturation_vapour_pressure(30)
    assert vapour_pressure_deficit(30, 100) == 0
    assert vapour_pressure_deficit(30, 50) == pytest.approx(es / 2)
    assert vapour_pressure_deficit(30, 0) == pytest.approx(es)
    assert vapour_pressure_deficit(30, 120) == 0  # over-range clamped
    assert vapour_pressure_deficit(30, -5) == pytest.approx(es)


def test_drying_potential_worked_example():
    # T=30C, RH=75%: VPD = 4.243 * 0.25 = 1.0608 kPa
    assert drying_potential(30, 75, 0) == pytest.approx(1.0608, abs=0.005)
    # u=2 m/s: (1 + 0.536 * 2) * VPD
    assert drying_potential(30, 75, 2) == pytest.approx(2.0720 * 1.0608, abs=0.01)


def test_drying_potential_is_monotonic_in_each_input():
    base = drying_potential(29, 75, 2)
    assert drying_potential(32, 75, 2) > base  # hotter
    assert drying_potential(29, 60, 2) > base  # drier air
    assert drying_potential(29, 75, 4) > base  # windier


def test_saturated_air_has_no_drying_potential_even_with_wind():
    assert drying_potential(28, 100, 10) == 0


def test_negative_wind_is_treated_as_calm():
    assert drying_potential(30, 75, -3) == drying_potential(30, 75, 0)


# --- DP to score --------------------------------------------------------------

TABLE = tuple(float(k) for k in range(101))  # DP k == k-th percentile, for easy reading


@pytest.mark.parametrize(
    ("dp", "expected"),
    [
        (-1.0, 0),  # below the historical minimum
        (0.0, 0),
        (0.99, 0),
        (1.0, 1),
        (82.0, 82),  # exactly the 82nd percentile
        (82.9, 82),  # between 82 and 83: still 82
        (100.0, 100),
        (250.0, 100),  # above the historical maximum
    ],
)
def test_dp_to_score_percentile_position(dp, expected):
    assert dp_to_score(dp, TABLE) == expected


def test_dp_to_score_matches_brief_example():
    # DP 3.0 is higher than 82% of history -> score 82.
    table = tuple(3.0 * k / 82 for k in range(101))
    assert dp_to_score(3.0, table) == 82


def test_dp_to_score_never_decreases_as_dp_rises():
    scores = [dp_to_score(i / 20) for i in range(0, 300)]
    assert scores == sorted(scores)
    assert scores[0] == 0
    assert scores[-1] == 100


def test_percentile_description_uses_score_and_year():
    assert percentile_description(82) == (
        f"Drier than 82% of Singapore dry-weather readings in {dp_percentiles.DP_PERCENTILES_YEAR}"
    )


# --- Rain veto ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("rain_mm", "forecast", "vetoed"),
    [
        (0.0, "Fair (Day)", False),
        (0.0, "Cloudy", False),
        (0.2, "Fair (Day)", True),  # observed rain beats a fair forecast
        (0.0, "Thundery Showers", True),  # forecast rain vetoes even if dry now
        (0.0, "Light Rain", True),
        (0.0, "Passing Showers", True),
        (0.0, "Heavy Thundery Showers with Gusty Winds", True),
        (0.0, "Drizzle", True),
        (None, "Partly Cloudy (Day)", False),  # station missing: forecast only
        (None, "Light Rain (Night)", True),
        (0.0, None, False),  # forecast missing: dry observation only
        (0.4, None, True),
        (None, None, False),
    ],
)
def test_rain_veto(rain_mm, forecast, vetoed):
    assert (rain_veto_reason(rain_mm, forecast) is not None) is vetoed


def test_veto_reasons_are_readable():
    assert rain_veto_reason(1.2, None) == "Rain detected at nearby station (1.2 mm in last 5 min)"
    assert rain_veto_reason(0.0, "Heavy Thundery Showers with Gusty Winds (Day)") == (
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


def test_good_drying_conditions_score_high_and_hang_now():
    # 33C, 55% RH, ~5 m/s: strongly favourable.
    result = compute_score(make_conditions(temp=33, humidity=55, wind=10))
    assert result.score >= 95
    assert result.band is Band.HANG_NOW
    assert result.veto is False
    assert result.veto_reason is None


def test_poor_drying_conditions_score_low():
    result = compute_score(make_conditions(temp=25, humidity=98, wind=1))
    assert result.score <= 5
    assert result.band is Band.DO_NOT_HANG
    assert result.veto is False  # poor air is not a veto, just a low score


def test_score_matches_dp_percentile_with_knots_converted_to_ms():
    result = compute_score(make_conditions(temp=30, humidity=70, wind=6))
    dp = drying_potential(30, 70, 6 * scoring.KNOTS_TO_MS)
    assert result.dp == pytest.approx(dp, abs=1e-4)
    assert result.score == dp_to_score(dp)
    assert result.band is band_for(result.score)


def test_wind_in_knots_not_ms_drives_the_score():
    still = compute_score(make_conditions(temp=30, humidity=70, wind=0))
    breezy = compute_score(make_conditions(temp=30, humidity=70, wind=10))
    assert breezy.dp is not None and still.dp is not None
    assert breezy.dp > still.dp
    assert breezy.score > still.score


def test_rain_vetoes_but_score_still_reflects_drying_potential():
    dry = compute_score(make_conditions(rain=0.0, temp=33, humidity=55, wind=10))
    rainy = compute_score(make_conditions(rain=0.6, temp=33, humidity=55, wind=10))
    assert rainy.veto is True
    assert rainy.band is Band.DO_NOT_HANG
    assert rainy.veto_reason is not None
    assert rainy.veto_reason.startswith("Rain detected")
    assert rainy.score == dry.score  # score is never capped by the veto
    assert dry.band is Band.HANG_NOW


def test_forecast_rain_vetoes_even_when_station_is_dry():
    result = compute_score(make_conditions(rain=0.0, forecast="Thundery Showers"))
    assert result.veto is True
    assert result.band is Band.DO_NOT_HANG


def test_forecast_alone_can_decide_the_veto():
    assert compute_score(make_conditions(rain=None, forecast="Light Rain")).veto is True
    assert compute_score(make_conditions(rain=None, forecast="Fair (Day)")).veto is False


def test_rainfall_alone_can_decide_the_veto():
    assert compute_score(make_conditions(rain=0.2, forecast=None)).veto is True
    assert compute_score(make_conditions(rain=0.0, forecast=None)).veto is False


@pytest.mark.parametrize(
    ("psi", "warning", "level"),
    [
        (None, False, None),
        (48, False, PsiLevel.GOOD),
        (100, False, PsiLevel.MODERATE),
        (101, True, PsiLevel.UNHEALTHY),
        (250, True, PsiLevel.VERY_UNHEALTHY),
        (350, True, PsiLevel.HAZARDOUS),
    ],
)
def test_air_quality_warning_threshold(psi, warning, level):
    result = compute_score(make_conditions(psi=psi))
    assert result.air_quality_warning is warning
    assert result.psi_caution is level


def test_psi_does_not_affect_score_or_band():
    clean = compute_score(make_conditions(psi=20))
    hazy = compute_score(make_conditions(psi=350))
    assert (clean.score, clean.band) == (hazy.score, hazy.band)


@pytest.mark.parametrize("missing", ["temp", "humidity", "wind"])
def test_missing_drying_input_raises(missing):
    with pytest.raises(InsufficientDataError, match="Missing drying inputs"):
        compute_score(make_conditions(**{missing: None}))


def test_missing_rain_information_raises():
    with pytest.raises(InsufficientDataError, match="rain veto"):
        compute_score(make_conditions(rain=None, forecast=None))


def test_all_inputs_missing_raises():
    with pytest.raises(InsufficientDataError):
        compute_score(
            make_conditions(rain=None, humidity=None, temp=None, wind=None, forecast=None)
        )


def test_zero_humidity_and_out_of_range_humidity_do_not_break_scoring():
    for humidity in (0, 100, 105):
        assert 0 <= compute_score(make_conditions(humidity=humidity)).score <= 100


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
                        if result.veto:
                            assert result.band is Band.DO_NOT_HANG
                        else:
                            assert result.band is band_for(result.score)
