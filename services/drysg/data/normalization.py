"""Combine independently selected weather observations for each forecast area."""

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from drysg.core.locations import AreaLocation, Coordinates
from drysg.core.models import Conditions, Forecast, Reading

from ._schemas import (
    AreaMetadata,
    ForecastItem,
    MetricFeed,
    ObservationBatch,
    PsiItem,
    Station,
    StationValue,
)
from .errors import DataUnavailableError

logger = logging.getLogger(__name__)
OBSERVATION_MAX_AGE = timedelta(minutes=15)
PSI_MAX_AGE = timedelta(hours=2)

# Official NEA region label points; nearest point is an MVP approximation.
PSI_REGIONS = (
    AreaLocation(area="north", latitude=1.41803, longitude=103.82),
    AreaLocation(area="south", latitude=1.29587, longitude=103.82),
    AreaLocation(area="east", latitude=1.35735, longitude=103.94),
    AreaLocation(area="west", latitude=1.35735, longitude=103.7),
    AreaLocation(area="central", latitude=1.35735, longitude=103.82),
)


class AreaWeather(AreaLocation):
    """Normalized conditions ready for Isaac's scoring function."""

    region: str
    conditions: Conditions


def require_aware(now: datetime) -> None:
    """Reject clocks without timezone information."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Clock must include timezone information")


def distance_km(first: Coordinates, second: Coordinates) -> float:
    """Return great-circle distance between two latitude/longitude points."""
    lat1, lat2 = radians(first.latitude), radians(second.latitude)
    dlat = lat2 - lat1
    dlon = radians(second.longitude - first.longitude)
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(min(1.0, max(0.0, a))))


def nearest_area(point: Coordinates, areas: list[AreaLocation]) -> AreaLocation:
    """Select the nearest area label, breaking distance ties by area name."""
    if not areas:
        raise DataUnavailableError("Forecast area catalogue is unavailable")
    return min(areas, key=lambda area: (distance_km(point, area), area.area))


def _items[T: BaseModel](schema: type[T], values: Any) -> list[T]:
    """Parse independent records without letting one bad record discard others."""
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        try:
            result.append(schema.model_validate(value))
        except ValidationError:
            logger.warning("Ignoring malformed NEA %s record", schema.__name__)
    return result


def _data(payloads: Mapping[str, Any], name: str) -> dict[str, Any]:
    """Return a successful envelope's data, or an empty missing feed."""
    envelope = payloads.get(name)
    if not isinstance(envelope, dict) or envelope.get("code") != 0:
        return {}
    data = envelope.get("data")
    return data if isinstance(data, dict) else {}


def _metric_readings(
    data: dict[str, Any],
    *,
    unit: str,
    minimum: float,
    maximum: float | None,
    now: datetime,
    max_age: timedelta,
) -> list[tuple[str, Coordinates, Reading]]:
    """Keep each station's newest usable reading in the expected unit."""
    try:
        feed = MetricFeed.model_validate(data)
    except ValidationError:
        return []
    if feed.reading_unit != unit:
        return []
    stations = {
        station.id: station.location for station in _items(Station, feed.stations)
    }
    readings = {}
    for batch in sorted(
        _items(ObservationBatch, feed.readings), key=lambda batch: batch.timestamp
    ):
        if not timedelta(0) <= now - batch.timestamp <= max_age:
            continue
        for value in _items(StationValue, batch.data):
            if value.station_id not in stations or value.value is None:
                continue
            if value.value < minimum or (maximum is not None and value.value > maximum):
                continue
            readings[value.station_id] = Reading(
                value=value.value,
                observed_at=batch.timestamp,
                source=value.station_id,
            )
    return [(key, stations[key], reading) for key, reading in readings.items()]


def normalize_weather(
    payloads: Mapping[str, Any],
    *,
    now: datetime,
    observation_max_age: timedelta = OBSERVATION_MAX_AGE,
    psi_max_age: timedelta = PSI_MAX_AGE,
) -> list[AreaWeather]:
    """Map fresh weather readings to NEA forecast area label points.

    Args:
        payloads: Successful NEA envelopes keyed by endpoint name.
        now: Timezone-aware computation time.
        observation_max_age: Maximum age for station observations.
        psi_max_age: Maximum age for the hourly PSI publication.

    Returns:
        Conditions per area, with unavailable or stale inputs set to None.

    Raises:
        ValueError: Clock or freshness windows are invalid.
        DataUnavailableError: No valid forecast area catalogue exists.
    """
    require_aware(now)
    if observation_max_age <= timedelta(0) or psi_max_age <= timedelta(0):
        raise ValueError("Freshness windows must be positive")
    forecast_data = _data(payloads, "two-hr-forecast")
    try:
        metadata = TypeAdapter(list[AreaMetadata]).validate_python(
            forecast_data.get("area_metadata")
        )
    except ValidationError as exc:
        raise DataUnavailableError("Forecast area catalogue is malformed") from exc
    if not metadata or len({area.name for area in metadata}) != len(metadata):
        raise DataUnavailableError(
            "Forecast area catalogue is unavailable or ambiguous"
        )
    forecasts = {}
    for item in sorted(
        _items(ForecastItem, forecast_data.get("items")),
        key=lambda item: item.update_timestamp,
    ):
        if (
            item.update_timestamp <= now
            and item.valid_period.start <= now < item.valid_period.end
        ):
            for entry in item.forecasts:
                forecasts[entry.area] = Forecast(
                    text=entry.forecast,
                    valid_from=item.valid_period.start,
                    valid_to=item.valid_period.end,
                )
    metrics = {}
    for endpoint, field, unit, minimum, maximum in (
        ("rainfall", "rainfall_mm", "mm", 0, None),
        ("relative-humidity", "humidity_pct", "percentage", 0, 100),
        ("air-temperature", "temp_c", "deg C", -273.15, None),
        ("wind-speed", "wind_knots", "knots", 0, None),
    ):
        metrics[field] = _metric_readings(
            _data(payloads, endpoint),
            unit=unit,
            minimum=minimum,
            maximum=maximum,
            now=now,
            max_age=observation_max_age,
        )
    psi = {}
    for item in sorted(
        _items(PsiItem, _data(payloads, "psi").get("items")),
        key=lambda item: item.timestamp,
    ):
        if timedelta(0) <= now - item.timestamp <= psi_max_age:
            for region, value in item.readings.get(
                "psi_twenty_four_hourly", {}
            ).items():
                if value >= 0:
                    psi[region] = Reading(
                        value=value,
                        observed_at=item.timestamp,
                        source=region,
                    )
    areas = []
    for area in metadata:
        region = nearest_area(area.label_location, list(PSI_REGIONS)).area
        conditions = Conditions(forecast=forecasts.get(area.name), psi=psi.get(region))
        for field, readings in metrics.items():
            if readings:
                _, _, reading = min(
                    readings,
                    key=lambda entry: (
                        distance_km(area.label_location, entry[1]),
                        entry[0],
                    ),
                )
                setattr(conditions, field, reading)
        areas.append(
            AreaWeather(
                area=area.name,
                region=region,
                **area.label_location.model_dump(),
                conditions=conditions,
            )
        )
    return areas
