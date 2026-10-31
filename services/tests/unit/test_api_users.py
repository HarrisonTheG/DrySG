"""Email verification: POST /users and POST /users/verify against mocked AWS tables."""

import hashlib
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from botocore.exceptions import ClientError

from drysg.alerting.email_template import render_verification
from drysg.api.deps import build_deps
from drysg.db import UserRepository
from drysg.users import UserService
from tests.conftest import SENDER
from tests.unit.test_api_sessions import EMAIL, POSTAL, session_api  # noqa: F401

NEW = "new.user@example.com"
BASE = "https://drysg.example"


@pytest.fixture
def api(session_api):  # noqa: F811
    """The session API fixture plus a UserService that records emails instead of sending."""
    outbox = []
    session_api.outbox = outbox
    session_api.deps.users = UserService(
        UserRepository(session_api.database, "users"),
        lambda to, content: outbox.append((to, content)),
        app_base_url=BASE,
    )
    return session_api


def register(api, email=NEW):
    return api.client.post("/users", json={"email": email})


def verify(api, token, email=NEW):
    return api.client.post("/users/verify", json={"email": email, "token": token})


def token_from(content):
    link = re.search(r"https://\S+", content.text).group(0)
    query = parse_qs(urlparse(link).query)
    assert link.startswith(f"{BASE}/verify?")
    return query["email"][0], query["token"][0]


def test_register_emails_a_link_and_stores_only_the_token_hash(api):
    response = register(api, " New.User@Example.com ")
    assert response.status_code == 202, response.text
    assert response.json() == {"email": NEW, "verified": False}
    [(to, content)] = api.outbox
    assert to == NEW
    email, token = token_from(content)
    assert email == NEW
    row = api.users.get_item(Key={"email": NEW})["Item"]
    assert row["verified"] is False
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(row)
    assert row["token_expires_at"] > row["token_sent_at"]


def test_verified_link_lets_the_user_start_monitoring(api):
    assert api.client.post(
        "/laundry-sessions", json={"email": NEW, "postalCode": POSTAL}
    ).json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
    register(api)
    assert api.client.post(
        "/laundry-sessions", json={"email": NEW, "postalCode": POSTAL}
    ).status_code == 403
    _, token = token_from(api.outbox[0][1])

    response = verify(api, token)

    assert response.status_code == 200, response.text
    assert response.json() == {"email": NEW, "verified": True}
    row = api.users.get_item(Key={"email": NEW})["Item"]
    assert row["verified"] is True and "verified_at" in row
    assert not {"token_hash", "token_expires_at", "token_sent_at"} & set(row)
    started = api.client.post("/laundry-sessions", json={"email": NEW, "postalCode": POSTAL})
    assert started.status_code == 201, started.text
    # A second click on the same link still reports success.
    assert verify(api, token).json() == {"email": NEW, "verified": True}


def test_resend_is_limited_to_once_a_minute_and_replaces_the_old_link(api):
    register(api)
    too_soon = register(api)
    assert too_soon.status_code == 429
    assert too_soon.json()["error"]["code"] == "RESEND_TOO_SOON"
    assert len(api.outbox) == 1

    api.clock["now"] += timedelta(seconds=61)
    assert register(api).status_code == 202
    assert len(api.outbox) == 2
    _, old = token_from(api.outbox[0][1])
    _, new = token_from(api.outbox[1][1])
    assert verify(api, old).json()["error"]["code"] == "INVALID_TOKEN"
    assert verify(api, new).status_code == 200


def test_already_verified_email_gets_no_email_and_keeps_its_session(api):
    started = api.client.post("/laundry-sessions", json={"email": EMAIL, "postalCode": POSTAL})
    assert started.status_code == 201
    for _ in range(2):  # no resend limit applies to an already verified email
        response = register(api, EMAIL)
        assert response.status_code == 202
        assert response.json() == {"email": EMAIL, "verified": True}
    assert api.outbox == []
    row = api.users.get_item(Key={"email": EMAIL})["Item"]
    assert row["active_session_id"] == started.json()["id"]
    assert "token_hash" not in row


@pytest.mark.parametrize("case", ["wrong", "expired", "unknown_email"])
def test_bad_tokens_are_rejected(api, case):
    register(api)
    _, token = token_from(api.outbox[0][1])
    email = NEW
    if case == "wrong":
        token = token[:-1] + ("A" if token[-1] != "A" else "B")
    elif case == "expired":
        api.clock["now"] += timedelta(hours=24, seconds=1)
    else:
        email = "someone.else@example.com"

    response = verify(api, token, email)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_TOKEN"
    assert api.users.get_item(Key={"email": NEW})["Item"]["verified"] is False


def test_failed_send_is_503_and_can_be_retried_at_once(api):
    def fail(to, content):
        raise ClientError(
            {"Error": {"Code": "MessageRejected", "Message": "not verified"}}, "SendEmail"
        )

    api.deps.users.send_email = fail
    response = register(api)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EMAIL_UNAVAILABLE"

    api.deps.users.send_email = lambda to, content: api.outbox.append((to, content))
    assert register(api).status_code == 202
    _, token = token_from(api.outbox[0][1])
    assert verify(api, token).status_code == 200


@pytest.mark.parametrize(
    ("path", "payload", "code"),
    [
        ("/users", {"email": "not-an-email"}, "INVALID_EMAIL"),
        ("/users", {}, "INVALID_EMAIL"),
        ("/users", None, "INVALID_REQUEST"),
        ("/users/verify", {"email": NEW}, "INVALID_TOKEN"),
        ("/users/verify", {"email": NEW, "token": ""}, "INVALID_TOKEN"),
        ("/users/verify", {"email": "bad", "token": "x"}, "INVALID_EMAIL"),
    ],
)
def test_bad_requests_use_the_shared_error_body(api, path, payload, code):
    response = api.client.post(path, json=payload)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == code


def test_unconfigured_email_is_503(api):
    api.deps.users = None
    for response in (register(api), verify(api, "x")):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "EMAIL_UNAVAILABLE"


@pytest.mark.parametrize("sender_verified", [True, False])
def test_deps_send_through_ses(session_api, monkeypatch, sender_verified):  # noqa: F811
    ses = boto3.client("ses")
    if sender_verified:
        ses.verify_email_identity(EmailAddress=SENDER)
    monkeypatch.setenv("SES_SENDER", SENDER)
    monkeypatch.setenv("APP_BASE_URL", BASE)
    build_deps.cache_clear()
    session_api.deps.users = build_deps().users

    response = register(session_api)

    sent = int(ses.get_send_quota()["SentLast24Hours"])
    if sender_verified:
        assert response.status_code == 202
        assert sent == 1
    else:
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "EMAIL_UNAVAILABLE"
        assert sent == 0


@pytest.mark.parametrize("missing", ["SES_SENDER", "APP_BASE_URL"])
def test_deps_leave_email_off_without_its_settings(session_api, monkeypatch, missing):  # noqa: F811
    monkeypatch.setenv("SES_SENDER", SENDER)
    monkeypatch.setenv("APP_BASE_URL", BASE)
    monkeypatch.delenv(missing)
    build_deps.cache_clear()
    assert build_deps().users is None


def test_verification_link_escapes_the_email():
    content = render_verification("a+b@example.com", "tok-_1", BASE + "/", 24)
    assert f"{BASE}/verify?email=a%2Bb%40example.com&token=tok-_1" in content.text
    assert "24 hours" in content.html
