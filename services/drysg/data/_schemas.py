"""Typed parsers for the parts of NEA envelopes used by ingestion."""

from typing import Any

from pydantic import AwareDatetime, BaseModel, Field, FiniteFloat

from drysg.core.locations import Coordinates


class Station(BaseModel):
    """Observation station metadata."""

    id: str
    location: Coordinates


class StationValue(BaseModel):
    """One station observation in a timestamped batch."""

    station_id: str = Field(alias="stationId")
    value: FiniteFloat | None = None


class ObservationBatch(BaseModel):
    """A timestamped batch whose individual readings are parsed separately."""

    timestamp: AwareDatetime
    data: list[Any]


class MetricFeed(BaseModel):
    """Metric envelope; independent stations and batches are parsed separately."""

    stations: list[Any]
    readings: list[Any]
    reading_unit: str = Field(alias="readingUnit")


class AreaMetadata(BaseModel):
    """Area label coordinates in the forecast response."""

    name: str = Field(min_length=1)
    label_location: Coordinates


class ValidPeriod(BaseModel):
    """Forecast validity interval."""

    start: AwareDatetime
    end: AwareDatetime


class AreaForecast(BaseModel):
    """Forecast text keyed by area name."""

    area: str
    forecast: str = Field(min_length=1)


class ForecastItem(BaseModel):
    """One version of the two-hour area forecasts."""

    update_timestamp: AwareDatetime
    valid_period: ValidPeriod
    forecasts: list[AreaForecast]


class PsiItem(BaseModel):
    """Regional pollutant readings at a given time."""

    timestamp: AwareDatetime
    readings: dict[str, dict[str, FiniteFloat]]
