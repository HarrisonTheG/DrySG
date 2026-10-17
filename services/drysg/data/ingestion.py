"""Fetch, normalize, score and persist the shared area weather snapshots."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from drysg.core.models import AreaSnapshot
from drysg.core.scoring import InsufficientDataError, compute_score
from drysg.db import AreaSnapshotRepository

from .clients import NEA_ENDPOINTS, NeaClient
from .errors import DataUnavailableError
from .normalization import (
    OBSERVATION_MAX_AGE,
    PSI_MAX_AGE,
    normalize_weather,
    require_aware,
)


@dataclass(frozen=True)
class IngestionResult:
    """Saved snapshots for the alert trigger, plus partial-failure diagnostics."""

    snapshots: tuple[AreaSnapshot, ...]
    skipped_areas: tuple[str, ...]
    missing_feeds: tuple[str, ...]


class IngestionService:
    """Prepare shared snapshots with Isaac's existing scoring implementation."""

    def __init__(
        self,
        client: NeaClient,
        snapshots: AreaSnapshotRepository,
        *,
        observation_max_age: timedelta = OBSERVATION_MAX_AGE,
        psi_max_age: timedelta = PSI_MAX_AGE,
    ) -> None:
        if observation_max_age <= timedelta(0) or psi_max_age <= timedelta(0):
            raise ValueError("Freshness windows must be positive")
        self._client = client
        self._snapshots = snapshots
        self._observation_max_age = observation_max_age
        self._psi_max_age = psi_max_age

    def run(self, *, now: datetime | None = None) -> IngestionResult:
        """Refresh scoreable areas and return persisted snapshots for alert logic.

        Raises:
            ValueError: An explicitly supplied clock is timezone-naive.
            DataUnavailableError: Catalogue or all scoring observations are missing.
            ClientError: AWS persistence failed; some batches may already be saved.
        """
        if now is not None:
            require_aware(now)
        payloads = self._client.fetch_all()
        now = now if now is not None else datetime.now(UTC)
        areas = normalize_weather(
            payloads,
            now=now,
            observation_max_age=self._observation_max_age,
            psi_max_age=self._psi_max_age,
        )
        snapshots, skipped = [], []
        for area in areas:
            try:
                result = compute_score(area.conditions)
            except InsufficientDataError:
                skipped.append(area.area)
                continue
            previous = self._snapshots.get(area.area)
            # A score from another method (e.g. before a table rebuild) is not comparable.
            if previous is not None and previous.result.method != result.method:
                previous = None
            snapshots.append(
                AreaSnapshot(
                    **area.model_dump(),
                    result=result,
                    prev_score=previous.result.score if previous else None,
                    prev_band=previous.result.band if previous else None,
                    updated_at=now,
                )
            )
        if not snapshots:
            raise DataUnavailableError("No fresh scoring inputs are available")
        self._snapshots.save_all(snapshots)
        return IngestionResult(
            snapshots=tuple(snapshots),
            skipped_areas=tuple(skipped),
            missing_feeds=tuple(name for name in NEA_ENDPOINTS if name not in payloads),
        )
