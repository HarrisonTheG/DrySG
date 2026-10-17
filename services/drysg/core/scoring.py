"""Drying score: turns fused area Conditions into a 0-100 score and a recommendation.

Pure functions only. The on-demand API, the background ingest/alert path and the
on-prem comparison all call `compute_score`, so they cannot drift apart.

Rain and air quality are separate from the score:
  * Rain is a hard veto. Observed or forecast rain forces "Do Not Hang".
  * PSI never changes the recommendation; it only raises an air-quality warning.
Temperature, humidity and wind are combined into one physical quantity, the Penman
aerodynamic drying potential (DP), instead of hand-picked weights. The 0-100 score is
the percentile of the current DP within historical Singapore conditions.

References: Penman (1948); Bayliss et al. (2011), "Do you want to hang out?";
Huang & Chen (2010), Textile Research Journal.
"""

import math
import re
from bisect import bisect_right

from drysg.core.dp_percentiles import DP_INPUT_MEDIANS, DP_PERCENTILES, DP_PERCENTILES_YEAR
from drysg.core.models import Band, Conditions, PsiLevel, ScoreResult

# Identifies the formula and table behind a score. It changes when the table is rebuilt,
# so scores from before and after are never compared (see ingestion and alerts).
SCORE_METHOD = f"dp-percentile-{DP_PERCENTILES_YEAR}"

# --- Bands --------------------------------------------------------------------
HANG_NOW_MIN = 70
WAIT_MIN = 50

# --- Rain ---------------------------------------------------------------------
# Any 5-minute rainfall total above this at the nearest station is a veto.
RAIN_VETO_MM = 0.0
# Forecast keywords that mean precipitation within the 2-hour window -> veto.
RAIN_FORECAST_KEYWORDS = ("rain", "shower", "thunder", "drizzle")

# --- Drying potential ---------------------------------------------------------
KNOTS_TO_MS = 0.514444
WIND_COEFFICIENT = 0.536  # Penman aerodynamic wind function: 1 + 0.536 * u (u in m/s)

# --- PSI descriptor upper bounds (NEA) ----------------------------------------
PSI_BANDS = (
    (50, PsiLevel.GOOD),
    (100, PsiLevel.MODERATE),
    (200, PsiLevel.UNHEALTHY),
    (300, PsiLevel.VERY_UNHEALTHY),
)
# Air-quality warning applies above this PSI.
PSI_WARNING_ABOVE = 100


class InsufficientDataError(ValueError):
    """Raised when the inputs needed for a score or a rain decision are missing."""


# --- Forecast / rain veto -----------------------------------------------------


def _strip_suffix(text: str) -> str:
    """'Partly Cloudy (Day)' -> 'Partly Cloudy'."""
    return re.sub(r"\s*\(.*?\)\s*", " ", text).strip()


def normalise_forecast(text: str) -> str:
    """'Partly Cloudy (Day)' -> 'partly cloudy'."""
    return _strip_suffix(text).lower()


def is_rain_forecast(text: str) -> bool:
    norm = normalise_forecast(text)
    return any(k in norm for k in RAIN_FORECAST_KEYWORDS)


def rain_veto_reason(rainfall_mm: float | None, forecast_text: str | None) -> str | None:
    """Returns why laundry must not be hung, or None when there is no rain veto.

    Observed rain and rain in the 2-hour forecast both veto.
    """
    if rainfall_mm is not None and rainfall_mm > RAIN_VETO_MM:
        return f"Rain detected at nearby station ({rainfall_mm:g} mm in last 5 min)"
    if forecast_text is not None and is_rain_forecast(forecast_text):
        return f"{_strip_suffix(forecast_text)} forecast for the next 2 hours"
    return None


# --- Drying potential and score -----------------------------------------------


def saturation_vapour_pressure(temp_c: float) -> float:
    """Es(T) in kPa (Tetens form)."""
    return 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))


def vapour_pressure_deficit(temp_c: float, humidity_pct: float) -> float:
    """VPD in kPa. Humidity is clamped to 0-100."""
    rh = max(0.0, min(100.0, humidity_pct))
    return saturation_vapour_pressure(temp_c) * (1 - rh / 100)


def drying_potential(temp_c: float, humidity_pct: float, wind_ms: float) -> float:
    """DP = (1 + 0.536 u) * VPD, with wind speed u in m/s. Higher dries faster."""
    return (1 + WIND_COEFFICIENT * max(0.0, wind_ms)) * vapour_pressure_deficit(
        temp_c, humidity_pct
    )


def dp_to_score(dp: float, table: tuple[float, ...] = DP_PERCENTILES) -> int:
    """Percentile position of `dp` in the historical table, as an integer 0-100.

    `table[k]` is the k-th percentile, so the score is the highest k with
    table[k] <= dp (0 when below the minimum, 100 at or above the maximum).
    """
    return max(0, min(100, bisect_right(table, dp) - 1))


def percentile_description(score: int) -> str:
    return f"Drier than {score}% of Singapore dry-weather readings in {DP_PERCENTILES_YEAR}"


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

    The score is always the DP percentile; a rain veto only changes the band (and
    `veto` fields). A missing temperature, humidity or wind reading (e.g. one NEA feed
    is down) is replaced by its historical median and the result is flagged `degraded`,
    so one failed feed does not take every area offline.

    Raises InsufficientDataError if all three drying inputs are missing (the score would
    be meaningless), or if neither rainfall nor a forecast is available to decide the veto.
    """
    inputs = {
        "temp_c": _value(conditions.temp_c),
        "humidity_pct": _value(conditions.humidity_pct),
        "wind_knots": _value(conditions.wind_knots),
    }
    estimated = [name for name, value in inputs.items() if value is None]
    if len(estimated) == len(inputs):
        raise InsufficientDataError("Missing drying inputs: temperature, humidity and wind")
    for name in estimated:
        inputs[name] = DP_INPUT_MEDIANS[name]
    temp, humidity, wind_knots = inputs["temp_c"], inputs["humidity_pct"], inputs["wind_knots"]

    rainfall = _value(conditions.rainfall_mm)
    forecast_text = conditions.forecast.text if conditions.forecast else None
    if rainfall is None and forecast_text is None:
        raise InsufficientDataError("No rainfall or forecast data to decide the rain veto")

    dp = drying_potential(temp, humidity, wind_knots * KNOTS_TO_MS)
    score = dp_to_score(dp)
    veto_reason = rain_veto_reason(rainfall, forecast_text)
    psi = _value(conditions.psi)

    return ScoreResult(
        score=score,
        band=Band.DO_NOT_HANG if veto_reason else band_for(score),
        veto=veto_reason is not None,
        veto_reason=veto_reason,
        psi_caution=psi_caution(psi),
        air_quality_warning=psi is not None and psi > PSI_WARNING_ABOVE,
        dp=round(dp, 4),
        degraded=bool(estimated),
        estimated_inputs=estimated,
        method=SCORE_METHOD,
    )
