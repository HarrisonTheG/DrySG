"""FastAPI app for the DrySG API Lambda (served by Mangum, or uvicorn locally).

Recommendation and laundry-session routes share a dependency factory so the
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

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
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
from drysg.db import AreaSnapshotRepository, ConcurrentSessionUpdateError
from drysg.experience.service import (
    ExperienceRateLimitedError,
    ExperienceService,
    NoAlertAtFrameError,
    ScenarioNotFoundError,
)
from drysg.sessions import (
    EmailNotVerifiedError,
    InvalidEmailError,
    InvalidSessionIdError,
    SessionNotFoundError,
    SessionService,
)
from drysg.users import (
    EmailUnavailableError,
    InvalidTokenError,
    ResendTooSoonError,
    UserService,
)

from .models import (
    ApiError,
    DryingRecommendation,
    ErrorBody,
    ExperienceAlertSent,
    LaundrySession,
    RegisterUser,
    SendExperienceAlert,
    StartLaundrySession,
    UserStatus,
    VerifyUser,
)
from .recommendation import SnapshotIncompleteError, to_recommendation
from .sessions import to_laundry_session

logger = logging.getLogger(__name__)

# A snapshot older than this is treated as unavailable (6 missed 5-minute ingest runs).
SNAPSHOT_MAX_AGE = timedelta(minutes=30)
CATALOGUE_TTL_S = 3600.0
# Failures of the cold-start refresh that mean "no data right now" (503), not a bug (500).
REFRESH_UNAVAILABLE_ERRORS = (
    UpstreamError,
    DataUnavailableError,
    ClientError,
    BotoCoreError,
)


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
                logger.info(
                    "Skipping refresh: last one was %.0fs ago", now - self._last
                )
                return
            self._last = (
                now  # set first so a failing refresh is not retried in a tight loop
            )
            self._refresh()


@dataclass
class ApiDeps:
    snapshots: AreaSnapshotRepository
    locations: LocationService
    catalogue: AreaCatalogue
    # Cold-start fallback: run one NEA refresh when no snapshot exists. None disables it.
    refresh: Callable[[], None] | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    sessions: SessionService | None = None
    users: UserService | None = None
    experience: ExperienceService | None = None


def _fresh_snapshot(deps: ApiDeps, area: str) -> AreaSnapshot:
    snapshot = deps.snapshots.get(area)
    if snapshot is None and deps.refresh is not None:
        logger.info("No snapshot for %s; running cold-start refresh", area)
        try:
            deps.refresh()
        except REFRESH_UNAVAILABLE_ERRORS as exc:
            logger.warning("Cold-start refresh failed: %s", exc)
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
        raise ApiProblem(
            503, "DATA_UNAVAILABLE", "Postal code lookup is unavailable"
        ) from exc


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

    def error_response(status: int, code: str, message: str) -> JSONResponse:
        body = ApiError(error=ErrorBody(code=code, message=message))
        return JSONResponse(status_code=status, content=body.model_dump())

    @app.exception_handler(ApiProblem)
    async def handle_problem(_: Request, exc: ApiProblem) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message)

    domain_errors = {
        InvalidEmailError: (400, "INVALID_EMAIL"),
        InvalidSessionIdError: (400, "INVALID_SESSION_ID"),
        InvalidTokenError: (400, "INVALID_TOKEN"),
        EmailNotVerifiedError: (403, "EMAIL_NOT_VERIFIED"),
        SessionNotFoundError: (404, "SESSION_NOT_FOUND"),
        ConcurrentSessionUpdateError: (409, "SESSION_CONFLICT"),
        ResendTooSoonError: (429, "RESEND_TOO_SOON"),
        EmailUnavailableError: (503, "EMAIL_UNAVAILABLE"),
        ScenarioNotFoundError: (404, "SCENARIO_NOT_FOUND"),
        NoAlertAtFrameError: (400, "NO_ALERT_AT_FRAME"),
        ExperienceRateLimitedError: (429, "RATE_LIMITED"),
    }

    async def handle_domain_error(_: Request, exc: Exception) -> JSONResponse:
        status, code = domain_errors[type(exc)]
        return error_response(status, code, str(exc))

    for exception in domain_errors:
        app.add_exception_handler(exception, handle_domain_error)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        required = {
            "/laundry-sessions": "A JSON email and postalCode are required",
            "/users": "A JSON email is required",
            "/users/verify": "A JSON email and token are required",
            "/experience/alert": "A JSON email, scenarioId and frame are required",
        }.get(request.url.path)
        if required is None:
            return await request_validation_exception_handler(request, exc)
        fields = {error["loc"][-1] for error in exc.errors()}
        if "email" in fields:
            return error_response(
                400, "INVALID_EMAIL", "email must be a valid email address"
            )
        if "postalCode" in fields:
            return error_response(
                400, "INVALID_POSTAL_CODE", "postalCode must be six digits"
            )
        if "token" in fields:
            return error_response(
                400, "INVALID_TOKEN", "This verification link is invalid or has expired"
            )
        return error_response(400, "INVALID_REQUEST", required)

    # Unexpected errors are caught in a middleware rather than an Exception handler:
    # Starlette runs Exception handlers outside every middleware, so their 500s would
    # skip CORSMiddleware and the browser would hide the error body from the frontend.
    # CORSMiddleware is added after this, which makes it the outer layer.
    @app.middleware("http")
    async def handle_unexpected(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception("Unhandled error")
            return error_response(500, "INTERNAL_ERROR", "Something went wrong")

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )

    @app.get(
        "/recommendation",
        response_model=DryingRecommendation,
        responses={
            400: {"model": ApiError},
            404: {"model": ApiError},
            503: {"model": ApiError},
        },
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
            raise ApiProblem(
                503, "DATA_UNAVAILABLE", "Weather data is being refreshed"
            ) from exc

    def session_service(deps: ApiDeps) -> SessionService:
        if deps.sessions is None:
            raise ApiProblem(503, "DATA_UNAVAILABLE", "Monitoring is not configured")
        return deps.sessions

    @app.post(
        "/laundry-sessions",
        status_code=201,
        response_model=LaundrySession,
        response_model_exclude_none=True,
        responses={status: {"model": ApiError} for status in (400, 403, 404, 409, 503)},
    )
    def start_laundry_session(
        body: StartLaundrySession, deps: Annotated[ApiDeps, Depends(get_deps)]
    ) -> LaundrySession:
        """Start monitoring using the same postal lookup and snapshot as recommendation."""
        now = deps.clock()
        try:
            session = session_service(deps).start(
                body.email,
                body.postalCode,
                now=now,
                snapshot_for_postal=lambda code: _fresh_snapshot(
                    deps, _resolve_area(deps, code)
                ),
            )
        except DataUnavailableError as exc:
            raise ApiProblem(503, "DATA_UNAVAILABLE", str(exc)) from exc
        return to_laundry_session(session, now)

    @app.get(
        "/laundry-sessions/{id}",
        response_model=LaundrySession,
        response_model_exclude_none=True,
        responses={status: {"model": ApiError} for status in (400, 404, 503)},
    )
    def get_laundry_session(
        session_id: Annotated[
            str, Path(alias="id", json_schema_extra={"format": "uuid"})
        ],
        deps: Annotated[ApiDeps, Depends(get_deps)],
    ) -> LaundrySession:
        """Read monitoring details and the last recorded alert state."""
        return to_laundry_session(session_service(deps).get(session_id), deps.clock())

    @app.delete(
        "/laundry-sessions/{id}",
        response_model=LaundrySession,
        response_model_exclude_none=True,
        responses={status: {"model": ApiError} for status in (400, 404, 503)},
    )
    def stop_laundry_session(
        session_id: Annotated[
            str, Path(alias="id", json_schema_extra={"format": "uuid"})
        ],
        deps: Annotated[ApiDeps, Depends(get_deps)],
    ) -> LaundrySession:
        """Stop monitoring; repeated requests return the same inactive state."""
        now = deps.clock()
        return to_laundry_session(session_service(deps).stop(session_id, now=now), now)

    def user_service(deps: ApiDeps) -> UserService:
        if deps.users is None:
            raise ApiProblem(
                503, "EMAIL_UNAVAILABLE", "Email verification is not configured"
            )
        return deps.users

    @app.post(
        "/users",
        status_code=202,
        response_model=UserStatus,
        responses={status: {"model": ApiError} for status in (400, 429, 503)},
    )
    def register_user(
        body: RegisterUser, deps: Annotated[ApiDeps, Depends(get_deps)]
    ) -> UserStatus:
        """Email a verification link. An already verified email gets no email."""
        status = user_service(deps).register(body.email, now=deps.clock())
        return UserStatus(email=status.email, verified=status.verified)

    @app.post(
        "/users/verify",
        response_model=UserStatus,
        responses={status: {"model": ApiError} for status in (400, 503)},
    )
    def verify_user(
        body: VerifyUser, deps: Annotated[ApiDeps, Depends(get_deps)]
    ) -> UserStatus:
        """Confirm the email with the emailed token."""
        status = user_service(deps).verify(body.email, body.token, now=deps.clock())
        return UserStatus(email=status.email, verified=status.verified)

    @app.post(
        "/experience/alert",
        status_code=202,
        response_model=ExperienceAlertSent,
        responses={status: {"model": ApiError} for status in (400, 403, 404, 429, 503)},
    )
    def send_experience_alert(
        body: SendExperienceAlert, deps: Annotated[ApiDeps, Depends(get_deps)]
    ) -> ExperienceAlertSent:
        """Email the alert an Experience replay raises at `frame`. Verified emails only."""
        if deps.experience is None:
            raise ApiProblem(
                503, "EMAIL_UNAVAILABLE", "Email alerts are not configured"
            )
        sent = deps.experience.send_alert(
            body.email, body.scenarioId, body.frame, now=deps.clock()
        )
        return ExperienceAlertSent(
            email=sent.email, scenarioId=sent.scenario_id, frame=sent.frame
        )

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
