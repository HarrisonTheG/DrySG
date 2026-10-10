"""Bounded HTTP clients for NEA v2 and authenticated OneMap search."""

import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from drysg.core.locations import POSTAL_CODE_PATTERN, PostalAddress

from .errors import PostalCodeNotFoundError, UpstreamError

logger = logging.getLogger(__name__)
NEA_ENDPOINTS = (
    "rainfall",
    "relative-humidity",
    "air-temperature",
    "wind-speed",
    "two-hr-forecast",
    "psi",
)


def validate_postal_code(postal_code: str) -> str:
    """Normalize a postal code or raise ValueError for invalid input."""
    if not isinstance(postal_code, str):
        raise ValueError("Postal code must contain six ASCII digits")
    postal_code = postal_code.strip()
    if re.fullmatch(POSTAL_CODE_PATTERN, postal_code) is None:
        raise ValueError("Postal code must contain six ASCII digits")
    return postal_code


class NeaClient:
    """Fetch each weather feed once, retaining successful partial responses."""

    def __init__(self, http: httpx.Client, *, api_key: str | None = None) -> None:
        self._http = http
        self._headers = {"x-api-key": api_key} if api_key else {}

    def fetch_all(self) -> dict[str, Any]:
        """Return successful envelopes keyed by NEA endpoint name."""
        payloads = {}
        for endpoint in NEA_ENDPOINTS:
            try:
                response = self._http.get(
                    f"https://api-open.data.gov.sg/v2/real-time/api/{endpoint}",
                    headers=self._headers,
                    timeout=5.0,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("code") != 0:
                    raise ValueError("Unsuccessful NEA response")
                if not isinstance(payload.get("data"), dict):
                    raise ValueError("Missing NEA data")
                payloads[endpoint] = payload
            except (httpx.HTTPError, ValueError):
                logger.warning("NEA feed unavailable: %s", endpoint)
        return payloads


class _SearchResponse(BaseModel):
    error: str | None = None
    results: list[dict[str, Any]]
    found: int = Field(ge=0)


class OneMapClient:
    """Look up an exact postal code using a caller-owned HTTP client."""

    def __init__(self, http: httpx.Client, *, token: str | None = None) -> None:
        self._http = http
        self._token = token

    def lookup(self, postal_code: str) -> PostalAddress:
        """Return an exact address match.

        Raises:
            ValueError: The postal code is invalid.
            PostalCodeNotFoundError: No exact match exists.
            UpstreamError: Authentication, transport or response validation failed.
        """
        postal_code = validate_postal_code(postal_code)
        if not self._token or not self._token.strip():
            raise UpstreamError("OneMap token is required")
        try:
            response = self._http.get(
                "https://www.onemap.gov.sg/api/common/elastic/search",
                params={
                    "searchVal": postal_code,
                    "returnGeom": "Y",
                    "getAddrDetails": "Y",
                    "pageNum": 1,
                },
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=5.0,
            )
            response.raise_for_status()
            result = _SearchResponse.model_validate(response.json())
            if result.error:
                raise UpstreamError("OneMap rejected the search request")
            for item in result.results:
                if item.get("POSTAL") == postal_code:
                    return PostalAddress(
                        postal_code=postal_code,
                        latitude=item["LATITUDE"],
                        longitude=item["LONGITUDE"],
                        address=item["ADDRESS"],
                    )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise UpstreamError("OneMap search unavailable or malformed") from exc
        raise PostalCodeNotFoundError(postal_code)
