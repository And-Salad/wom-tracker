"""The Wise Old Man client: throttling, retries, and what it does with a 403.

None of this was covered. It is the layer between the tracker and somebody
else's rate limiter, so getting it wrong means either a ban or a tracker that
goes quiet without saying why.
"""

import pytest

from wom.api import WomClient, WomError


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {"data": []}
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._payload is _BROKEN:
            raise ValueError("not json")
        return self._payload


_BROKEN = object()


class FakeSession:
    """Hands back a queued reply per call and records what it was asked."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def request(self, method, url, params=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params,
                           "headers": headers})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Every retry path sleeps; none of them needs to here.

    The 429 branch waits fifteen seconds and doubles, so an untamed test of
    the retry logic would take most of a minute to prove three lines.
    """
    monkeypatch.setattr("wom.api.time.sleep", lambda _seconds: None)


def _client(replies, **kwargs):
    return WomClient(session=FakeSession(replies), **kwargs)


def test_a_key_is_sent_as_a_header_and_a_contact_in_the_agent():
    client = _client([FakeResponse()], api_key="secret", contact="me@example.com")
    client.get_player("zezima")
    sent = client.session.calls[0]["headers"]
    assert sent["x-api-key"] == "secret"
    assert "me@example.com" in sent["User-Agent"]


def test_no_key_means_no_header_at_all():
    client = _client([FakeResponse()])
    client.get_player("zezima")
    assert "x-api-key" not in client.session.calls[0]["headers"]


def test_a_key_buys_a_faster_rate():
    """Anonymous is 20 a minute and keyed is 100, so the spacing differs."""
    assert _client([], api_key="k")._min_interval < _client([])._min_interval


def test_a_network_error_is_retried_then_reported():
    import requests
    client = _client([requests.RequestException("no route"),
                      requests.RequestException("no route"),
                      requests.RequestException("no route")])
    with pytest.raises(WomError) as raised:
        client.get_player("zezima")
    assert "network error" in str(raised.value)
    assert len(client.session.calls) == 3, "tried three times before giving up"


def test_a_transient_failure_is_retried_and_then_succeeds():
    import requests
    client = _client([requests.RequestException("blip"),
                      FakeResponse(payload={"id": 1})])
    assert client.get_player("zezima") == {"id": 1}
    assert len(client.session.calls) == 2


def test_a_rate_limit_is_retried_and_honours_retry_after(monkeypatch):
    waited = []
    monkeypatch.setattr("wom.api.time.sleep", waited.append)
    client = _client([FakeResponse(429, headers={"Retry-After": "7"}),
                      FakeResponse(payload={"id": 1})])
    assert client.get_player("zezima") == {"id": 1}
    assert 7 in waited, "the server said seven seconds, so wait seven"


def test_a_server_error_is_retried_but_a_client_error_is_not():
    client = _client([FakeResponse(503), FakeResponse(payload={"id": 1})])
    assert client.get_player("zezima") == {"id": 1}

    client = _client([FakeResponse(404, text="no such player")])
    with pytest.raises(WomError) as raised:
        client.get_player("nobody")
    assert raised.value.status == 404
    assert len(client.session.calls) == 1, "a 404 will not become a 200"


def test_a_rejected_key_is_dropped_and_the_call_retried_without_it():
    """A key the API refuses is worse than none: it answers 403 to every
    request while the same request unkeyed is served."""
    client = _client([FakeResponse(403, payload={"message": "Invalid API key"}),
                      FakeResponse(payload={"id": 1})], api_key="stale")
    assert client.get_player("zezima") == {"id": 1}
    assert client.api_key == "", "and it is not tried again"
    assert "x-api-key" not in client.session.calls[1]["headers"]


def test_a_403_that_is_not_about_the_key_is_raised():
    client = _client([FakeResponse(403, payload={"message": "banned"})],
                     api_key="fine")
    with pytest.raises(WomError):
        client.get_player("zezima")
    assert client.api_key == "fine", "an unrelated 403 must not discard it"


def test_a_non_json_reply_is_an_error_not_a_crash():
    client = _client([FakeResponse(payload=_BROKEN)])
    with pytest.raises(WomError) as raised:
        client.get_player("zezima")
    assert "non-JSON" in str(raised.value)
