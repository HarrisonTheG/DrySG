"""The bundled Experience scenarios (built by scripts/build_experience_scenarios.py).

The API only needs each scenario's alerts, so a client can ask for the alert at a frame
but can never choose what the email says.
"""

import json
from datetime import datetime
from functools import cache
from pathlib import Path

from pydantic import BaseModel

from drysg.core.models import Band

SCENARIOS_PATH = Path(__file__).with_name("scenarios.json")


class ExperienceAlert(BaseModel):
    """The alert the live rules raised at one frame of the replay."""

    frame: int
    reason: str
    prevScore: int
    prevBand: Band
    newScore: int
    newBand: Band
    newVeto: bool
    snapshotTs: datetime


class ExperienceScenario(BaseModel):
    id: str
    area: str
    alerts: list[ExperienceAlert]

    def alert_at(self, frame: int) -> ExperienceAlert | None:
        return next((alert for alert in self.alerts if alert.frame == frame), None)


def parse_scenarios(document: dict) -> dict[str, ExperienceScenario]:
    scenarios = (ExperienceScenario.model_validate(item) for item in document["scenarios"])
    return {scenario.id: scenario for scenario in scenarios}


@cache
def load_scenarios() -> dict[str, ExperienceScenario]:
    """Empty when the file has not been built yet, so the API still starts."""
    if not SCENARIOS_PATH.exists():
        return {}
    return parse_scenarios(json.loads(SCENARIOS_PATH.read_text(encoding="utf-8")))
