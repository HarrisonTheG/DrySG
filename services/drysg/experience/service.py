"""Send the alert email a replayed scenario would have triggered."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from drysg.alerting.email_template import EmailContent, render_alert
from drysg.core.models import AlertMessage
from drysg.db import UserRepository
from drysg.sessions import EmailNotVerifiedError, normalize_email
from drysg.users import EmailUnavailableError

from .scenarios import ExperienceScenario

logger = logging.getLogger(__name__)

# Minimum gap between Experience emails to one address. Playback can raise two alerts a
# few seconds apart, so this is short; it only stops a script from flooding an inbox.
SEND_INTERVAL = timedelta(seconds=15)


class ScenarioNotFoundError(LookupError):
    """No scenario has this id."""


class NoAlertAtFrameError(ValueError):
    """The scenario raises no alert at this frame."""


class ExperienceRateLimitedError(RuntimeError):
    """An Experience email was sent to this address moments ago."""


@dataclass
class SentAlert:
    email: str
    scenario_id: str
    frame: int


class ExperienceService:
    """Verified emails only: the owner of an address has agreed to get its mail."""

    def __init__(
        self,
        repository: UserRepository,
        send_email: Callable[[str, EmailContent], None],
        scenarios: dict[str, ExperienceScenario],
        *,
        app_base_url: str,
    ) -> None:
        self.repository = repository
        self.send_email = send_email
        self.scenarios = scenarios
        self.app_base_url = app_base_url

    def send_alert(self, email: str, scenario_id: str, frame: int, *, now: datetime) -> SentAlert:
        """Email the alert the scenario raises at `frame`.

        Raises:
            InvalidEmailError: The email is not a usable address.
            ScenarioNotFoundError: Unknown scenario.
            NoAlertAtFrameError: The scenario raises no alert at that frame.
            EmailNotVerifiedError: The address has not been verified.
            ExperienceRateLimitedError: Another Experience email went out moments ago.
            EmailUnavailableError: Sending failed; the user can retry straight away.
        """
        email = normalize_email(email)
        scenario: ExperienceScenario | None = self.scenarios.get(scenario_id)
        if scenario is None:
            raise ScenarioNotFoundError(f"No scenario {scenario_id}")
        alert = scenario.alert_at(frame)
        if alert is None:
            raise NoAlertAtFrameError(f"{scenario_id} raises no alert at frame {frame}")

        claim = self.repository.claim_experience_send(
            email, now=now, cutoff=now - SEND_INTERVAL
        )
        if claim == "unverified":
            raise EmailNotVerifiedError("Confirm your email before turning on live alerts")
        if claim == "too_soon":
            raise ExperienceRateLimitedError("An alert was just sent. Wait a few seconds")

        message = AlertMessage(
            alert_id=f"experience#{scenario_id}#{frame}",
            session_id="experience",
            email=email,
            area=scenario.area,
            prev_score=alert.prevScore,
            prev_band=alert.prevBand,
            new_score=alert.newScore,
            new_band=alert.newBand,
            new_veto=alert.newVeto,
            reason=alert.reason,
            snapshot_ts=alert.snapshotTs,
        )
        try:
            self.send_email(email, render_alert(message, self.app_base_url, experience=True))
        except Exception as exc:
            logger.warning("Experience email to %s failed: %s", email, exc)
            self.repository.release_experience_send(email, now=now)
            raise EmailUnavailableError(
                "The alert email could not be sent. Try again in a moment"
            ) from exc
        return SentAlert(email=email, scenario_id=scenario_id, frame=frame)
