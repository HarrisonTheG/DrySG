"""HTTP response models for the frontend contract (docs/contracts/lambda-backend.md).

Field names are camelCase on purpose: they are the JSON the frontend reads.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from drysg.core.locations import POSTAL_CODE_PATTERN
from drysg.data.clients import validate_postal_code
from drysg.sessions import normalize_email


class RecommendationState(StrEnum):
    HANG_NOW = "HANG_NOW"
    WAIT = "WAIT"
    DO_NOT_HANG = "DO_NOT_HANG"


PsiBand = Literal["Good", "Moderate", "Unhealthy", "Very Unhealthy", "Hazardous"]


class WeatherConditions(BaseModel):
    """Current conditions. Any value is null when its feed was missing.

    A missing temperature, humidity or wind reading is scored with its historical median
    (see `DryingRecommendation.degraded`), but is still shown as null here.
    """

    rainfallMm: float | None
    temperatureC: float | None
    humidityPercent: float | None
    windSpeedMs: float | None
    forecast: str | None
    psi: float | None
    psiBand: PsiBand | None


class StationSources(BaseModel):
    """NEA station (or PSI region) that supplied each observation; null if not available."""

    rainfall: str | None
    humidity: str | None
    temperature: str | None
    wind: str | None


class DryingRecommendation(BaseModel):
    postalCode: str
    areaName: str
    score: int
    recommendation: RecommendationState
    summary: str
    percentileDescription: str | None = None
    updatedAt: str
    conditions: WeatherConditions
    rainVeto: bool
    airQualityWarning: bool
    stationSources: StationSources | None = None
    degraded: bool = False


class ErrorBody(BaseModel):
    code: str
    message: str


class ApiError(BaseModel):
    error: ErrorBody


class StartLaundrySession(BaseModel):
    """Start monitoring for a verified email and a six-digit postal code."""

    email: str = Field(json_schema_extra={"format": "email"})
    postalCode: str = Field(pattern=POSTAL_CODE_PATTERN)

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, value: str) -> str:
        """Use the same normalized email key as the session service."""
        return normalize_email(value)

    @field_validator("postalCode", mode="before")
    @classmethod
    def validate_postal(cls, value: str) -> str:
        """Preserve leading zeroes and reject non-string postal codes."""
        return validate_postal_code(value)


class LaundrySession(BaseModel):
    """Public session state; never includes the email or alert baseline."""

    id: str = Field(json_schema_extra={"format": "uuid"})
    postalCode: str
    startedAt: str = Field(json_schema_extra={"format": "date-time"})
    expiresAt: str = Field(json_schema_extra={"format": "date-time"})
    active: bool
