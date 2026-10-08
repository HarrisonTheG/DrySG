"""Postal-code resolution using OneMap and the shared area catalogue."""

from datetime import UTC, datetime

from drysg.core.locations import AreaLocation, PostalLocation
from drysg.db import PostalCacheRepository

from .clients import OneMapClient, validate_postal_code
from .errors import DataUnavailableError
from .normalization import nearest_area, require_aware


class LocationService:
    """Resolve a postal code once and cache its nearest forecast-area label."""

    def __init__(self, client: OneMapClient, cache: PostalCacheRepository) -> None:
        self._client = client
        self._cache = cache

    def resolve_postal_code(
        self,
        postal_code: str,
        *,
        areas: list[AreaLocation],
        now: datetime | None = None,
    ) -> PostalLocation:
        """Return a cached or newly resolved postal location.

        Args:
            postal_code: Six ASCII digits, optionally surrounded by whitespace.
            areas: Area label points, normally from the snapshot repository.
            now: Optional timezone-aware clock for deterministic callers/tests.

        Raises:
            ValueError: Postal code or clock is invalid.
            DataUnavailableError: An uncached lookup has no area catalogue.
            PostalCodeNotFoundError: OneMap has no exact match.
            UpstreamError: OneMap lookup failed.
        """
        postal_code = validate_postal_code(postal_code)
        now = now if now is not None else datetime.now(UTC)
        require_aware(now)
        cached = self._cache.get(postal_code)
        if cached is not None:
            return cached
        if not areas:
            raise DataUnavailableError("Forecast area catalogue is unavailable")
        address = self._client.lookup(postal_code)
        area = nearest_area(address, areas)
        location = PostalLocation(
            **address.model_dump(),
            area=area.area,
            cached_at=now,
        )
        self._cache.save(location)
        return location
