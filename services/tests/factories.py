"""Builders for test data. Defaults describe an active session in Bishan baselined at Hang Now."""

from datetime import datetime, timedelta

from drysg.core.models import (
    AlertMessage,
    AreaSnapshot,
    Band,
    Conditions,
    Forecast,
    MonitoringSession,
    Reading,
    ScoreResult,
    SessionStatus,
)
from drysg.core.scoring import SCORE_METHOD, band_for, compute_score

NOW = datetime.fromisoformat("2026-10-04T14:00:00+08:00")


def make_session(**overrides) -> MonitoringSession:
    fields = {
        "session_id": "11111111-1111-4111-8111-111111111111",
        "email": "user@example.com",
        "postal_code": "570150",
        "area": "Bishan",
        "status": SessionStatus.ACTIVE,
        "started_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(hours=11),
        "baseline_score": 85,
        "baseline_band": Band.HANG_NOW,
        "baseline_method": SCORE_METHOD,
    }
    fields.update(overrides)
    return MonitoringSession(**fields)


def make_snapshot(
    score: int,
    *,
    area: str = "Bishan",
    veto_reason: str | None = None,
    at: datetime = NOW,
    prev_score: int | None = None,
    prev_band: Band | None = None,
) -> AreaSnapshot:
    return AreaSnapshot(
        area=area,
        region="central",
        latitude=1.35,
        longitude=103.84,
        conditions=Conditions(),
        result=ScoreResult(
            score=score,
            band=Band.DO_NOT_HANG if veto_reason else band_for(score),
            veto=veto_reason is not None,
            veto_reason=veto_reason,
            method=SCORE_METHOD,
        ),
        prev_score=prev_score,
        prev_band=prev_band,
        updated_at=at,
    )


def make_alert(**overrides) -> AlertMessage:
    fields = {
        "alert_id": f"11111111-1111-4111-8111-111111111111#{NOW.isoformat()}",
        "session_id": "11111111-1111-4111-8111-111111111111",
        "email": "user@example.com",
        "area": "Bishan",
        "prev_score": 85,
        "prev_band": Band.HANG_NOW,
        "new_score": 30,
        "new_band": Band.DO_NOT_HANG,
        "new_veto": True,
        "new_method": SCORE_METHOD,
        "reason": "Rain detected at nearby station (0.4 mm in last 5 min)",
        "snapshot_ts": NOW,
    }
    fields.update(overrides)
    return AlertMessage(**fields)


AREA_POINTS = {"Bishan": (1.3526, 103.8352), "Bedok": (1.3236, 103.9273)}


def make_weather_snapshot(
    area: str = "Bishan",
    *,
    at: datetime = NOW,
    rain: float | None = 0.0,
    temp: float = 31.0,
    humidity: float = 70.0,
    wind_knots: float = 6.0,
    forecast: str | None = "Fair (Day)",
    psi: float | None = 48.0,
) -> AreaSnapshot:
    """A scored snapshot built from raw conditions, with station sources set."""

    def reading(value: float | None, source: str) -> Reading | None:
        return None if value is None else Reading(value=value, observed_at=at, source=source)

    conditions = Conditions(
        rainfall_mm=reading(rain, "S07"),
        humidity_pct=reading(humidity, "S109"),
        temp_c=reading(temp, "S24"),
        wind_knots=reading(wind_knots, "S81"),
        forecast=None
        if forecast is None
        else Forecast(text=forecast, valid_from=at, valid_to=at + timedelta(hours=2)),
        psi=reading(psi, "central"),
    )
    latitude, longitude = AREA_POINTS[area]
    return AreaSnapshot(
        area=area,
        region="central",
        latitude=latitude,
        longitude=longitude,
        conditions=conditions,
        result=compute_score(conditions),
        updated_at=at,
    )
