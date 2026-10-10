"""External clients must distinguish usable data from upstream failures."""

import json
from pathlib import Path

import httpx
import pytest

from drysg.data import NeaClient, OneMapClient, PostalCodeNotFoundError, UpstreamError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_nea_fetches_six_endpoints_once_with_api_key():
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["x-api-key"] == "test-key"
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200, json=json.loads((FIXTURES / "nea" / f"{name}.json").read_text())
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = NeaClient(http, api_key="test-key").fetch_all()
    assert len(requests) == 6
    assert set(result) == {p.stem for p in (FIXTURES / "nea").glob("*.json")}


def test_nea_partial_failure_retains_other_endpoints():
    def respond(request):
        name = request.url.path.rsplit("/", 1)[-1]
        if name == "rainfall":
            return httpx.Response(503)
        if name == "psi":
            return httpx.Response(200, json={"code": 1, "errorMsg": "down"})
        return httpx.Response(
            200, json=json.loads((FIXTURES / "nea" / f"{name}.json").read_text())
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = NeaClient(http).fetch_all()
    assert len(result) == 4
    assert "rainfall" not in result
    assert "psi" not in result


def test_onemap_exact_postal_match_and_token():
    payload = json.loads((FIXTURES / "onemap/search_018956.json").read_text())
    payload.pop("error")  # Recorded unauthenticated response is not success.
    other = {**payload["results"][0], "POSTAL": "999999"}
    payload["results"].insert(0, other)

    def respond(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.url.params["searchVal"] == "018956"
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = OneMapClient(http, token="test-token").lookup("018956")
    assert result.latitude == pytest.approx(1.282397137717063)
    assert result.longitude == pytest.approx(103.8584053916593)


def test_onemap_warning_is_failure_even_with_results():
    payload = json.loads((FIXTURES / "onemap/search_018956.json").read_text())
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
        ) as http,
        pytest.raises(UpstreamError),
    ):
        OneMapClient(http, token="expired").lookup("018956")


def test_onemap_missing_exact_match_is_not_found():
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"found": 0, "results": []})
            )
        ) as http,
        pytest.raises(PostalCodeNotFoundError),
    ):
        OneMapClient(http, token="test-token").lookup("000000")


def test_onemap_requires_token_before_network():
    with httpx.Client() as http, pytest.raises(UpstreamError, match="token"):
        OneMapClient(http).lookup("018956")


def test_nea_timeouts_and_invalid_json_are_missing_feeds():
    def respond(request):
        if request.url.path.endswith("rainfall"):
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, text="not json")

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        assert NeaClient(http).fetch_all() == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"found": 1},
        {
            "found": 1,
            "results": [
                {
                    "POSTAL": "018956",
                    "LATITUDE": "NaN",
                    "LONGITUDE": "103.8",
                    "ADDRESS": "Address",
                }
            ],
        },
    ],
)
def test_onemap_malformed_response_is_unavailable_not_not_found(payload):
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
        ) as http,
        pytest.raises(UpstreamError),
    ):
        OneMapClient(http, token="test").lookup("018956")
