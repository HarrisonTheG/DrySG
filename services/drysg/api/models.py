"""HTTP response models for the frontend contract (docs/contracts/lambda-backend.md).

Field names are camelCase on purpose: they are the JSON the frontend reads.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class RecommendationState(StrEnum):
    HANG_NOW = "HANG_NOW"
    WAIT = "WAIT"
    DO_NOT_HANG = "DO_NOT_HANG"


PsiBand = Literal["Good", "Moderate", "Unhealthy", "Very Unhealthy", "Hazardous"]


class WeatherConditions(BaseModel):
    """Current conditions. Rainfall, forecast and PSI are null when that feed was missing."""

    rainfallMm: float | None
    temperatureC: float
    humidityPercent: float
    windSpeedMs: float
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


class ErrorBody(BaseModel):
    code: str
    message: str


class ApiError(BaseModel):
    error: ErrorBody
