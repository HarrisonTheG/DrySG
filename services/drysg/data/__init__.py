"""External data sources: NEA real-time APIs, OneMap, geo matching (Stream A)."""

from .clients import NeaClient, OneMapClient
from .errors import DataUnavailableError, PostalCodeNotFoundError, UpstreamError
from .ingestion import IngestionResult, IngestionService
from .location import LocationService
from .normalization import AreaWeather, normalize_weather

__all__ = [
    "AreaWeather",
    "DataUnavailableError",
    "NeaClient",
    "OneMapClient",
    "PostalCodeNotFoundError",
    "UpstreamError",
    "normalize_weather",
    "IngestionResult",
    "IngestionService",
    "LocationService",
]
