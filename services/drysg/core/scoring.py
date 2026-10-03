"""Drying score: turns fused area Conditions into a 0-100 score and a recommendation.

Pure functions only. The on-demand API, the background ingest/alert path and the
on-prem comparison all call `compute_score`, so they cannot drift apart.

All tunables are module-level constants. Curve values are initial proposals
pending team review (see progress.md, "Open Questions").
"""

import re

from drysg.core.models import Band, Conditions, PsiLevel, ScoreResult, SubScores

# --- Weights ------------------------------------------------------------------
# Reweighted from the preliminary report's 50/25/10/15 (2026-10-04): with rain at
# 50%, "not raining" alone gave 50 points, so humid nights scored Hang Now.
# Rain is still a hard veto; humidity drives drying speed in Singapore.
WEIGHT_RAIN = 0.30
WEIGHT_HUMIDITY = 0.40
WEIGHT_WIND = 0.10
WEIGHT_TEMPERATURE = 0.20

# --- Bands --------------------------------------------------------------------
HANG_NOW_MIN = 70
WAIT_MIN = 50

# A veto forces "Do Not Hang", so the score is capped just below WAIT_MIN.
VETO_SCORE_CAP = WAIT_MIN - 1
# With no rain information at all, never say "Hang Now".
MISSING_RAIN_SCORE_CAP = HANG_NOW_MIN - 1

# --- Rain ---------------------------------------------------------------------
# Any 5-minute rainfall total above this at the nearest station is a veto.
RAIN_VETO_MM = 0.0
# Forecast keywords that mean precipitation within the 2-hour window -> veto.
RAIN_FORECAST_KEYWORDS = ("rain", "shower", "thunder", "drizzle")
# Rain sub-score by normalised NEA forecast text (suffixes like "(Day)" removed).
FORECAST_SCORES = {
    "fair": 100,
    "fair and warm": 100,
    "partly cloudy": 85,
    "windy": 80,
    "slightly hazy": 70,
    "cloudy": 60,
    "hazy": 60,
    "mist": 40,
    "fog": 30,
}
UNKNOWN_FORECAST_SCORE = 50
# Dry station reading but no forecast available: dry now, unknown outlook.
DRY_NO_FORECAST_SCORE = 70

# --- Humidity (%): linear, 100 at or below BEST, 0 at or above WORST ----------
HUMIDITY_BEST = 60.0
HUMIDITY_WORST = 95.0

# --- Temperature (deg C): linear, 0 at or below MIN, 100 at or above MAX ------
TEMP_MIN = 24.0
TEMP_MAX = 32.0

# --- Wind (knots): piecewise linear ------------------------------------------
# Still air dries slowly; a moderate breeze is ideal; strong gusts blow laundry away.
WIND_CALM_SCORE = 30.0  # at 0 kt
WIND_IDEAL_LOW = 5.0  # reaches 100
WIND_IDEAL_HIGH = 15.0  # still 100
WIND_STRONG = 25.0  # drops to WIND_STRONG_SCORE
WIND_STRONG_SCORE = 40.0
WIND_GALE = 35.0  # 0 at or above

# --- PSI descriptor upper bounds (NEA) ----------------------------------------
PSI_BANDS = (
    (50, PsiLevel.GOOD),
    (100, PsiLevel.MODERATE),
    (200, PsiLevel.UNHEALTHY),
    (300, PsiLevel.VERY_UNHEALTHY),
)


class InsufficientDataError(ValueError):
    """Raised when no scoring input is available at all."""


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _lerp(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    """Linear interpolation of y at x between (x0, y0) and (x1, y1)."""
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _strip_suffix(text: str) -> str:
    """'Partly Cloudy (Day)' -> 'Partly Cloudy'."""
    return re.sub(r"\s*\(.*?\)\s*", " ", text).strip()


def normalise_forecast(text: str) -> str:
    """'Partly Cloudy (Day)' -> 'partly cloudy'."""
    return _strip_suffix(text).lower()


def is_rain_forecast(text: str) -> bool:
    norm = normalise_forecast(text)
    return any(k in norm for k in RAIN_FORECAST_KEYWORDS)


def forecast_subscore(text: str) -> float:
    if is_rain_forecast(text):
        return 0.0
    return float(FORECAST_SCORES.get(normalise_forecast(text), UNKNOWN_FORECAST_SCORE))


def rain_subscore(
    rainfall_mm: float | None, forecast_text: str | None
) -> tuple[float | None, str | None]:
    """Returns (sub-score, veto reason). The veto reason is None when there is no veto.

    Observed rain and rain in the forecast both veto. Otherwise the forecast sets the
    score. With only a dry observation and no forecast, a cautious fallback is used.
    """
    if rainfall_mm is not None and rainfall_mm > RAIN_VETO_MM:
        return 0.0, f"Rain detected at nearby station ({rainfall_mm:g} mm in last 5 min)"
    if forecast_text is not None:
        if is_rain_forecast(forecast_text):
            return 0.0, f"{_strip_suffix(forecast_text)} forecast for the next 2 hours"
        return forecast_subscore(forecast_text), None
    if rainfall_mm is not None:
        return float(DRY_NO_FORECAST_SCORE), None
    return None, None


def humidity_subscore(pct: float) -> float:
    return _clamp(_lerp(pct, HUMIDITY_BEST, 100.0, HUMIDITY_WORST, 0.0))


def temperature_subscore(celsius: float) -> float:
    return _clamp(_lerp(celsius, TEMP_MIN, 0.0, TEMP_MAX, 100.0))


def wind_subscore(knots: float) -> float:
    if knots <= 0:
        return WIND_CALM_SCORE
    if knots < WIND_IDEAL_LOW:
        return _lerp(knots, 0.0, WIND_CALM_SCORE, WIND_IDEAL_LOW, 100.0)
    if knots <= WIND_IDEAL_HIGH:
        return 100.0
    if knots <= WIND_STRONG:
        return _lerp(knots, WIND_IDEAL_HIGH, 100.0, WIND_STRONG, WIND_STRONG_SCORE)
    return _clamp(_lerp(knots, WIND_STRONG, WIND_STRONG_SCORE, WIND_GALE, 0.0))


def psi_caution(psi: float | None) -> PsiLevel | None:
    if psi is None:
        return None
    for upper, level in PSI_BANDS:
        if psi <= upper:
            return level
    return PsiLevel.HAZARDOUS


def band_for(score: int) -> Band:
    if score >= HANG_NOW_MIN:
        return Band.HANG_NOW
    if score >= WAIT_MIN:
        return Band.WAIT
    return Band.DO_NOT_HANG


def _value(reading) -> float | None:
    return reading.value if reading is not None else None


def compute_score(conditions: Conditions) -> ScoreResult:
    """Score an area's conditions.

    Missing inputs: their weight is redistributed across the available factors and
    the result is flagged `degraded`. If rain information is missing, the score is
    capped below "Hang Now". Raises InsufficientDataError if every factor is missing.
    """
    forecast_text = conditions.forecast.text if conditions.forecast else None
    rain, veto_reason = rain_subscore(_value(conditions.rainfall_mm), forecast_text)

    humidity = _value(conditions.humidity_pct)
    temp = _value(conditions.temp_c)
    wind = _value(conditions.wind_knots)
    subs = SubScores(
        rain=None if rain is None else round(rain, 1),
        humidity=None if humidity is None else round(humidity_subscore(humidity), 1),
        wind=None if wind is None else round(wind_subscore(wind), 1),
        temperature=None if temp is None else round(temperature_subscore(temp), 1),
    )

    weighted = [
        (subs.rain, WEIGHT_RAIN),
        (subs.humidity, WEIGHT_HUMIDITY),
        (subs.wind, WEIGHT_WIND),
        (subs.temperature, WEIGHT_TEMPERATURE),
    ]
    available = [(s, w) for s, w in weighted if s is not None]
    if not available:
        raise InsufficientDataError("No rain, humidity, wind or temperature data")

    total_weight = sum(w for _, w in available)
    # Round half up (Python's round() is banker's rounding).
    score = int(sum(s * w for s, w in available) / total_weight + 0.5)

    veto = veto_reason is not None
    if veto:
        score = min(score, VETO_SCORE_CAP)
    elif subs.rain is None:
        score = min(score, MISSING_RAIN_SCORE_CAP)

    return ScoreResult(
        score=score,
        band=band_for(score),
        sub_scores=subs,
        veto=veto,
        veto_reason=veto_reason,
        psi_caution=psi_caution(_value(conditions.psi)),
        degraded=len(available) < len(weighted) or forecast_text is None,
    )
