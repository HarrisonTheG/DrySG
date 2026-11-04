"""POST /experience/alert: verified emails only, content fixed by the bundled scenario."""

from datetime import timedelta

import pytest

from drysg.api.deps import build_deps
from drysg.db import UserRepository
from drysg.experience.scenarios import parse_scenarios
from drysg.experience.service import SEND_INTERVAL, ExperienceService
from tests.factories import NOW
from tests.unit.test_api_sessions import EMAIL, session_api  # noqa: F401

BASE = "https://drysg.example"
SCENARIO = "rain_arrives-20241112-0950-bishan"
DOCUMENT = {
    "scenarios": [
        {
            "id": SCENARIO,
            "area": "Bishan",
            "frames": [],
            "alerts": [
                {
                    "frame": 5,
                    "reason": "Rain detected at nearby station (1.2 mm in last 5 min)",
                    "prevScore": 93,
                    "prevBand": "Hang Now",
                    "newScore": 96,
                    "newBand": "Do Not Hang",
                    "newVeto": True,
                    "snapshotTs": "2024-11-12T10:15:00+08:00",
                }
            ],
        }
    ]
}


@pytest.fixture
def api(session_api):  # noqa: F811
    outbox = []
    session_api.outbox = outbox
    session_api.fail = False

    def send(to, content):
        if session_api.fail:
            raise RuntimeError("SES down")
        outbox.append((to, content))

    session_api.deps.experience = ExperienceService(
        UserRepository(session_api.database, "users"),
        send,
        parse_scenarios(DOCUMENT),
        app_base_url=BASE,
    )
    return session_api


def send(api, email=EMAIL, scenario=SCENARIO, frame=5):
    return api.client.post(
        "/experience/alert", json={"email": email, "scenarioId": scenario, "frame": frame}
    )


def test_a_verified_email_gets_the_alert_the_replay_raised(api):
    response = send(api)
    assert response.status_code == 202
    assert response.json() == {"email": EMAIL, "scenarioId": SCENARIO, "frame": 5}
    [(to, content)] = api.outbox
    assert to == EMAIL
    assert content.subject.startswith("[DrySG Experience] rain nearby")
    assert "Bishan" in content.text
    assert "Rain detected at nearby station" in content.text
    assert "Tue 12 Nov 2024, 10:15 am SGT" in content.text  # when it really happened
    assert "Nothing is being monitored" in content.text
    assert f"{BASE}/experience" in content.html
    assert "/stop?session" not in content.html + content.text  # nothing to stop


def test_an_unverified_email_is_refused_and_nothing_is_sent(api):
    response = send(api, email="stranger@example.com")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
    assert api.outbox == []


def test_an_unknown_scenario_is_404(api):
    response = send(api, scenario="nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SCENARIO_NOT_FOUND"


def test_a_frame_without_an_alert_is_400_and_does_not_use_up_the_rate_limit(api):
    response = send(api, frame=2)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "NO_ALERT_AT_FRAME"
    assert send(api).status_code == 202


def test_a_second_email_straight_away_is_rate_limited(api):
    assert send(api).status_code == 202
    response = send(api)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert len(api.outbox) == 1


def test_it_can_send_again_once_the_interval_has_passed(api):
    assert send(api).status_code == 202
    api.deps.clock = lambda: NOW + SEND_INTERVAL + timedelta(seconds=1)
    assert send(api).status_code == 202
    assert len(api.outbox) == 2


def test_a_failed_send_is_503_and_can_be_retried_at_once(api):
    api.fail = True
    response = send(api)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EMAIL_UNAVAILABLE"
    api.fail = False
    assert send(api).status_code == 202


def test_the_email_content_cannot_be_chosen_by_the_caller(api):
    response = api.client.post(
        "/experience/alert",
        json={"email": EMAIL, "scenarioId": SCENARIO, "frame": 5, "reason": "Win a prize"},
    )
    assert response.status_code == 202
    [(_, content)] = api.outbox
    assert "Win a prize" not in content.text + content.html


@pytest.mark.parametrize(
    "body",
    [
        {"email": "not-an-email", "scenarioId": SCENARIO, "frame": 5},
        {"email": EMAIL, "frame": 5},
        {"email": EMAIL, "scenarioId": SCENARIO, "frame": -1},
    ],
)
def test_malformed_requests_are_400(api, body):
    response = api.client.post("/experience/alert", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {"INVALID_EMAIL", "INVALID_REQUEST"}


def test_it_is_503_when_email_is_not_configured(api):
    api.deps.experience = None
    assert send(api).status_code == 503


def test_build_deps_wires_the_service_when_email_is_configured(api, monkeypatch):
    monkeypatch.setenv("SES_SENDER", "alerts@drysg.example")
    monkeypatch.setenv("APP_BASE_URL", BASE)
    build_deps.cache_clear()
    assert build_deps().experience is not None
