"""Domain models shared by every DrySG component.

This module is the single source of truth for data shapes passed between
ingestion (Stream A), scoring/alerts (Stream B) and the API (Stream C).
Changes here are contract changes: update docs/contracts/ alongside them.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Band(StrEnum):
    """Recommendation shown to the user. Ordered best to worst."""

    HANG_NOW = "Hang Now"
    WAIT = "Wait"
    DO_NOT_HANG = "Do Not Hang"


class PsiLevel(StrEnum):
    """NEA PSI descriptor bands. Shown as a caution, never part of the score."""

    GOOD = "Good"  # 0-50
    MODERATE = "Moderate"  # 51-100
    UNHEALTHY = "Unhealthy"  # 101-200
    VERY_UNHEALTHY = "Very Unhealthy"  # 201-300
    HAZARDOUS = "Hazardous"  # >300


class Reading(BaseModel):
    """A single numeric observation mapped to an area."""

    value: float
    observed_at: datetime
    source: str | None = Field(
        default=None, description="Station id (e.g. 'S109') or PSI region (e.g. 'central')"
    )


class Forecast(BaseModel):
    """NEA 2-hour forecast for an area, e.g. 'Partly Cloudy (Day)'."""

    text: str
    valid_from: datetime
    valid_to: datetime


class Conditions(BaseModel):
    """Fused environmental conditions for one forecast area.

    Any field may be None when the upstream source is missing or stale. Scoring needs
    temperature, humidity and wind, plus rainfall or a forecast for the rain veto.
    """

    rainfall_mm: Reading | None = None  # 5-minute rainfall total at nearest station
    humidity_pct: Reading | None = None
    temp_c: Reading | None = None
    wind_knots: Reading | None = None  # NEA v2 reports wind speed in knots
    forecast: Forecast | None = None
    psi: Reading | None = None  # 24-hour PSI for the area's region


class ScoreResult(BaseModel):
    score: int = Field(ge=0, le=100, description="Percentile of drying potential, 0-100")
    band: Band
    veto: bool = False
    veto_reason: str | None = None
    psi_caution: PsiLevel | None = None
    air_quality_warning: bool = Field(
        default=False, description="True when PSI is Unhealthy or worse (> 100)"
    )
    dp: float | None = Field(
        default=None, description="Drying potential (Penman aerodynamic term) behind the score"
    )
    degraded: bool = Field(
        default=False,
        description="True if a missing input was replaced by its historical median",
    )
    estimated_inputs: list[str] = Field(
        default_factory=list,
        description="Inputs replaced by their historical median: temp_c, humidity_pct, wind_knots",
    )
    method: str | None = Field(
        default=None,
        description=(
            "Scoring method and table that produced `score`, e.g. 'dp-percentile-2024'. "
            "Scores from different methods are not comparable. None: written by older code."
        ),
    )


class AreaSnapshot(BaseModel):
    """Latest conditions and score for a forecast area (DynamoDB AreaSnapshot table)."""

    area: str  # NEA forecast area name, e.g. 'Bishan'
    region: str  # PSI region: north | south | east | west | central
    latitude: float
    longitude: float
    conditions: Conditions
    result: ScoreResult
    prev_score: int | None = None
    prev_band: Band | None = None
    updated_at: datetime


class SessionStatus(StrEnum):
    ACTIVE = "active"
    STOPPED = "stopped"
    EXPIRED = "expired"


class MonitoringSession(BaseModel):
    """A user's monitoring session (DynamoDB MonitoringSession table).

    `baseline_*` is the state the user was last told about: set by the API at
    session start, and moved to the new state by the notifier after each alert.
    Alerts fire when conditions get worse than the baseline.
    """

    session_id: str
    email: str
    postal_code: str
    area: str
    status: SessionStatus
    started_at: datetime
    expires_at: datetime
    stopped_at: datetime | None = None
    baseline_score: int
    baseline_band: Band
    baseline_veto: bool = False
    baseline_method: str | None = None  # ScoreResult.method of the baseline score
    last_alert_at: datetime | None = None
    last_alert_band: Band | None = None


class AlertMessage(BaseModel):
    """SQS message body sent from the alert trigger to the notifier Lambda."""

    alert_id: str = Field(description="'{session_id}#{snapshot_ts}' - idempotency key")
    session_id: str
    email: str
    area: str
    prev_score: int
    prev_band: Band
    new_score: int
    new_band: Band
    new_veto: bool = False
    new_method: str | None = None  # ScoreResult.method of new_score
    reason: str = Field(description="Human-readable cause, e.g. 'Rain detected nearby'")
    snapshot_ts: datetime
