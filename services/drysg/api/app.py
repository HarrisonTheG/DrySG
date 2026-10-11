"""FastAPI app for the DrySG API Lambda (served by Mangum, or uvicorn locally).

Only `GET /recommendation` exists so far. The app takes a dependency factory so the
Lambda can build AWS/HTTP clients lazily (once per container) and tests can inject fakes.
"""

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from drysg.core.locations import POSTAL_CODE_PATTERN, AreaLocation
from drysg.core.models import AreaSnapshot
from drysg.data import (
    DataUnavailableError,
    LocationService,
    PostalCodeNotFoundError,
    UpstreamError,
)
from drysg.db import AreaSnapshotRepository

from .models import ApiError, DryingRecommendation, ErrorBody
from .recommendation import SnapshotIncompleteError, to_recommendation

logger = logging.getLogger(__name__)

# A snapshot older than this is treated as unavailable (6 missed 5-minute ingest runs).
SNAPSHOT_MAX_AGE = timedelta(minutes=30)
CATALOGUE_TTL_S = 3600.0


class ApiProblem(Exception):
    """An error with a stable code that maps to the shared `ApiError` response."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class AreaCatalogue:
    """Area label points, scanned from AreaSnapshot and reused for an hour.

    An empty catalogue is never cached, so a cold start recovers after the first ingest.
    """

    def __init__(self, snapshots: AreaSnapshotRepository) -> None:
        self._snapshots = snapshots
        self._areas: list[AreaLocation] = []
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def get(self) -> list[AreaLocation]:
        with self._lock:
            if self._areas and time.monotonic() - self._loaded_at < CATALOGUE_TTL_S:
                return self._areas
            areas = self._snapshots.list_areas()
            if areas:
                self._areas, self._loaded_at = areas, time.monotonic()
            return areas


class ThrottledRefresh:
    """Wrap a refresh so it runs at most once per interval per container.

    Without this, a postal code whose area never gets a snapshot would make every request
    call NEA six times, which is both slow and a quick way to get rate limited. A skipped
    call returns immediately, and the request then fails with 503 as it would anyway.
    """

    def __init__(
        self,
        refresh: Callable[[], None],
        *,
        min_interval_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._refresh = refresh
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._last: float | None = None
        self._lock = threading.Lock()

    def __call__(self) -> None:
        with self._lock:
            now = self._clock()
            if self._last is not None and now - self._last < self._min_interval_s:
                logger.info("Skipping refresh: last one was %.0fs ago", now - self._last)
                return
            self._last = now  # set first so a failing refresh is not retried in a tight loop
            self._refresh()


@dataclass
class ApiDeps:
    snapshots: AreaSnapshotRepository
    locations: LocationService
    catalogue: AreaCatalogue
    # Cold-start fallback: run one NEA refresh when no snapshot exists. None disables it.
    refresh: Callable[[], None] | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)


def _fresh_snapshot(deps: ApiDeps, area: str) -> AreaSnapshot:
    snapshot = deps.snapshots.get(area)
    if snapshot is None and deps.refresh is not None:
        logger.info("No snapshot for %s; running cold-start refresh", area)
        deps.refresh()
        snapshot = deps.snapshots.get(area)
    if snapshot is None:
        raise ApiProblem(503, "DATA_UNAVAILABLE", "No weather data for this area yet")
    if deps.clock() - snapshot.updated_at > SNAPSHOT_MAX_AGE:
        raise ApiProblem(503, "DATA_UNAVAILABLE", "Weather data is out of date")
    return snapshot


def _resolve_area(deps: ApiDeps, postal_code: str) -> str:
    try:
        try:
            return deps.locations.resolve_postal_code(
                postal_code, areas=deps.catalogue.get(), now=deps.clock()
            ).area
        except DataUnavailableError:
            # Empty area catalogue on a cold start: refresh once, then retry.
            if deps.refresh is None:
                raise
            deps.refresh()
            return deps.locations.resolve_postal_code(
                postal_code, areas=deps.catalogue.get(), now=deps.clock()
            ).area
    except PostalCodeNotFoundError as exc:
        raise ApiProblem(
            404, "POSTAL_CODE_NOT_FOUND", f"No area found for postal code {postal_code}"
        ) from exc
    except (UpstreamError, DataUnavailableError) as exc:
        logger.warning("Postal lookup unavailable: %s", exc)
        raise ApiProblem(503, "DATA_UNAVAILABLE", "Postal code lookup is unavailable") from exc


def create_app(
    get_deps: Callable[[], ApiDeps],
    *,
    cors_origins: list[str] | None = None,
    enable_docs: bool = True,
) -> FastAPI:
    app = FastAPI(
        title="DrySG API",
        version="0.1.0",
        docs_url="/docs" if enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if enable_docs else None,
    )
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )

    def error_response(status: int, code: str, message: str) -> JSONResponse:
        body = ApiError(error=ErrorBody(code=code, message=message))
        return JSONResponse(status_code=status, content=body.model_dump())

    @app.exception_handler(ApiProblem)
    async def handle_problem(_: Request, exc: ApiProblem) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return error_response(500, "INTERNAL_ERROR", "Something went wrong")

    @app.get(
        "/recommendation",
        response_model=DryingRecommendation,
        responses={400: {"model": ApiError}, 404: {"model": ApiError}, 503: {"model": ApiError}},
    )
    def get_recommendation(
        deps: Annotated[ApiDeps, Depends(get_deps)],
        postalCode: Annotated[
            str | None, Query(description="Six-digit Singapore postal code")
        ] = None,
    ) -> DryingRecommendation:
        code = (postalCode or "").strip()
        if not re.fullmatch(POSTAL_CODE_PATTERN, code):
            raise ApiProblem(
                400, "INVALID_POSTAL_CODE", "postalCode must be exactly six digits"
            )
        area = _resolve_area(deps, code)
        try:
            return to_recommendation(_fresh_snapshot(deps, area), code)
        except SnapshotIncompleteError as exc:
            raise ApiProblem(503, "DATA_UNAVAILABLE", "Weather data is being refreshed") from exc

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
