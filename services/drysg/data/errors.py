"""Failures exposed to API and scheduled-ingestion callers."""


class UpstreamError(RuntimeError):
    """An external service did not provide a trustworthy response."""


class DataUnavailableError(RuntimeError):
    """Required weather or location data is unavailable."""


class PostalCodeNotFoundError(LookupError):
    """OneMap returned no exact match for a valid postal code."""
