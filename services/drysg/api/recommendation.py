"""Build the frontend `DryingRecommendation` from a stored area snapshot."""

from datetime import timedelta, timezone

from drysg.core.models import AreaSnapshot, Band, Reading
from drysg.core.scoring import KNOTS_TO_MS, percentile_description

from .models import (
    DryingRecommendation,
    RecommendationState,
    StationSources,
    WeatherConditions,
)

SGT = timezone(timedelta(hours=8))

class SnapshotIncompleteError(ValueError):
    """A stored snapshot lacks inputs that scoring now requires."""


_STATE = {
    Band.HANG_NOW: RecommendationState.HANG_NOW,
    Band.WAIT: RecommendationState.WAIT,
    Band.DO_NOT_HANG: RecommendationState.DO_NOT_HANG,
}


def _value(reading: Reading | None) -> float | None:
    return None if reading is None else reading.value


def _source(reading: Reading | None) -> str | None:
    return None if reading is None else reading.source


def _summary(snapshot: AreaSnapshot) -> str:
    result = snapshot.result
    if result.veto:
        text = f"{result.veto_reason}. Do not hang."
    elif result.band is Band.HANG_NOW:
        text = "Good drying conditions. A good time to hang laundry."
    elif result.band is Band.WAIT:
        text = "Drying conditions are fair. Consider waiting for a better time."
    else:
        text = "Poor drying conditions right now. Laundry will dry slowly."
    psi, level = snapshot.conditions.psi, result.psi_caution
    if result.air_quality_warning and psi is not None and level is not None:
        text += f" Air quality is {level.value.lower()} (PSI {psi.value:g})."
    return text


def to_recommendation(snapshot: AreaSnapshot, postal_code: str) -> DryingRecommendation:
    """Map a stored snapshot to the API shape.

    Scoring guarantees temperature, humidity and wind are present, so only rainfall,
    forecast and PSI can be null.
    """
    c, r = snapshot.conditions, snapshot.result
    if c.temp_c is None or c.humidity_pct is None or c.wind_knots is None:
        # Only possible for a snapshot written by older code; the next ingest replaces it.
        raise SnapshotIncompleteError(snapshot.area)
    return DryingRecommendation(
        postalCode=postal_code,
        areaName=snapshot.area,
        score=r.score,
        recommendation=_STATE[r.band],
        summary=_summary(snapshot),
        percentileDescription=None if r.veto else percentile_description(r.score),
        updatedAt=snapshot.updated_at.astimezone(SGT).isoformat(),
        conditions=WeatherConditions(
            rainfallMm=_value(c.rainfall_mm),
            temperatureC=c.temp_c.value,
            humidityPercent=c.humidity_pct.value,
            windSpeedMs=round(c.wind_knots.value * KNOTS_TO_MS, 1),
            forecast=None if c.forecast is None else c.forecast.text,
            psi=_value(c.psi),
            psiBand=None if r.psi_caution is None else r.psi_caution.value,
        ),
        rainVeto=r.veto,
        airQualityWarning=r.air_quality_warning,
        stationSources=StationSources(
            rainfall=_source(c.rainfall_mm),
            humidity=_source(c.humidity_pct),
            temperature=_source(c.temp_c),
            wind=_source(c.wind_knots),
        ),
    )
