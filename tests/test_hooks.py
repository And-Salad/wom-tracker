"""The Dink webhook: the only unauthenticated write in the app.

Everything here is about what an endpoint on the open internet accepts and
what it refuses, because the URL is the whole credential and there is nobody
signed in to check.
"""

import io
import json
from datetime import datetime, timezone

import pytest

from wom.config import Config
from wom.web.limits import Budget


@pytest.fixture(autouse=True)
def _restore_settings():
    """Leave the shared settings file as we found it.

    Tests run against one data directory, so a roster or a token written here
    would still be there for the next file. The database is per-test; this is
    not.
    """
    before = Config()
    roster, tokens = before.get("usernames", []), before.get("dink_tokens") or {}
    yield
    after = Config()
    after["usernames"] = roster
    after["dink_tokens"] = tokens
    after.save()


def body(exp=1000000, world=338, kind="LOGIN", **extra):
    """A Dink metadata push, shaped like the real one."""
    payload = {
        "type": kind,
        "content": "someone logged into World {}".format(world),
        "playerName": "Zezima",
        "accountType": "NORMAL",
        "extra": {
            "world": world,
            "skills": {"totalExperience": exp, "totalLevel": 2000,
                       "levels": {"attack": 99}, "experience": {"attack": exp}},
            "collectionLog": {"completed": 651, "total": 1477},
            "questCount": {"completed": 156, "total": 158},
        },
    }
    payload.update(extra)
    return payload


def issue(signed_in, username="zezima"):
    """Put a player on the roster and give them a URL. Returns the path."""
    config = Config()
    names = list(config.get("usernames", []))
    if username not in [n.lower() for n in names]:
        config["usernames"] = names + [username]
        config.save()
    signed_in.post("/admin/dink", data={"username": username, "action": "issue"})
    token = (Config().get("dink_tokens") or {})[username]
    return "/hook/dink/" + token


def test_an_older_database_keeps_the_logins_it_had(tmp_path):
    """The first cut of this shipped a table that only knew about logins."""
    import sqlite3
    from wom.db import Database

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE logins (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            player_id INTEGER, received_at TEXT NOT NULL, world INTEGER,
            total_exp REAL, total_level INTEGER, collections INTEGER,
            payload TEXT NOT NULL);
        INSERT INTO logins (username, received_at, world, total_exp, payload)
        VALUES ('zezima', '2026-09-03T12:00:00.000Z', 338, 4242.0, '{}');
    """)
    conn.commit()
    conn.close()

    database = Database(path)
    rows = database.session_events("zezima")
    assert len(rows) == 1, "a stored login must survive the rename"
    assert rows[0]["kind"] == "login"
    assert rows[0]["total_exp"] == 4242.0
    assert database.query_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='logins'"
    ) is None, "the old table goes, so nothing writes to it by accident"


# -- who may call ---------------------------------------------------------

def test_an_unknown_token_is_404(client):
    assert client.post("/hook/dink/nope", json=body()).status_code == 404


def test_a_token_that_could_not_have_been_issued_is_a_miss(signed_in, app):
    """Anything outside ASCII, which the constant-time compare refuses to take.

    A token has to exist for this to bite: the comparison it would crash in
    only runs once there is something to compare against.
    """
    issue(signed_in)
    assert signed_in.post(u"/hook/dink/ééé",
                          json=body()).status_code == 404


def test_a_revoked_url_stops_working(signed_in, app):
    url = issue(signed_in)
    assert signed_in.post(url, json=body()).status_code == 204
    signed_in.post("/admin/dink", data={"username": "zezima", "action": "revoke"})
    assert signed_in.post(url, json=body(exp=2000000)).status_code == 404


def test_replacing_a_url_retires_the_old_one(signed_in, app):
    old = issue(signed_in)
    new = issue(signed_in)
    assert old != new, "issuing again must mint a different token"
    assert signed_in.post(old, json=body()).status_code == 404
    assert signed_in.post(new, json=body()).status_code == 204


def test_a_url_is_only_issued_for_a_tracked_player(signed_in):
    signed_in.post("/admin/dink", data={"username": "stranger", "action": "issue"})
    assert "stranger" not in (Config().get("dink_tokens") or {})


def test_the_admin_page_shows_the_url_and_the_last_login(signed_in, app):
    """The page is how a URL is handed out and how a demo login is confirmed."""
    url = issue(signed_in)
    token = url.rsplit("/", 1)[1]
    page = signed_in.get("/admin").get_data(as_text=True)
    assert token in page, "there is no other way to read the URL back out"
    assert "Custom Metadata Handler" in page, "the setting a player has to find"

    signed_in.post(url, json=body(exp=12345678))
    page = signed_in.get("/admin").get_data(as_text=True)
    assert "Session logins" in page
    section = page.split("Session logins")[1]
    assert ">1<" in section, "the event count must move"
    assert "12,345,678" in section, (
        "the experience we captured is how you tell the body was parsed, "
        "not merely that something arrived")


def test_the_url_handed_out_is_https_even_though_we_cannot_see_it(app):
    """The exact condition production runs under, and the bug it caused.

    Waitress strips X-Forwarded-Proto, so a request arrives looking like plain
    HTTP however it was really made - which is what the context below sets up.
    Building the URL from request.url_root therefore handed players an http://
    address: the host redirects it, okhttp downgrades the redirected POST to a
    GET, the body is lost, and the webhook fails where nobody was watching.
    """
    from flask import request
    from wom.web.hooks import public_url
    with app.test_request_context("/admin", base_url="http://wom-tracker.fly.dev"):
        assert not request.is_secure, "the condition this is about"
        assert public_url("abc") == "https://wom-tracker.fly.dev/hook/dink/abc"


def test_a_local_run_is_still_given_a_working_link(app):
    """Forcing https on localhost would only make development painful."""
    from wom.web.hooks import public_url
    with app.test_request_context("/admin", base_url="http://localhost:8000"):
        assert public_url("abc") == "http://localhost:8000/hook/dink/abc"


def test_a_downgraded_get_says_the_scheme_is_wrong(signed_in, app):
    """What an http:// URL turns into, and it must not be a bare 405."""
    url = issue(signed_in)
    response = signed_in.get(url, headers={"User-Agent": "RuneLite (Dink/1.x)"})
    assert response.status_code == 400
    assert b"https" in response.data, "the answer has to name the cause"
    assert app.config["DATABASE"].session_events("zezima") == []


# -- what it stores -------------------------------------------------------

def test_a_login_is_recorded(signed_in, app):
    url = issue(signed_in)
    assert signed_in.post(url, json=body(exp=12345678, world=420)).status_code == 204
    rows = app.config["DATABASE"].session_events("zezima")
    assert len(rows) == 1
    assert rows[0]["total_exp"] == 12345678
    assert rows[0]["world"] == 420
    assert rows[0]["total_level"] == 2000
    assert rows[0]["collections"] == 651
    assert rows[0]["received_at"], "a login is worthless without its time"


def test_a_login_links_to_the_player_when_we_know_them(signed_in, app):
    app.config["DATABASE"].save_player_details(
        {"id": 7, "username": "zezima", "displayName": "Zezima", "type": "regular"})
    url = issue(signed_in)
    signed_in.post(url, json=body())
    assert app.config["DATABASE"].session_events("zezima")[0]["player_id"] == 7


def test_a_login_is_kept_for_an_account_we_have_never_seen(signed_in, app):
    """The webhook can arrive before the first update run does."""
    url = issue(signed_in)
    signed_in.post(url, json=body())
    row = app.config["DATABASE"].session_events("zezima")[0]
    assert row["player_id"] is None
    assert row["username"] == "zezima"


def test_a_retry_is_not_a_second_session(signed_in, app):
    """Dink resends what it could not deliver, with a new timestamp."""
    url = issue(signed_in)
    for _ in range(3):
        assert signed_in.post(url, json=body(exp=555)).status_code == 204
    assert len(app.config["DATABASE"].session_events("zezima")) == 1


def test_logging_in_again_after_gaining_xp_is_a_new_session(signed_in, app):
    url = issue(signed_in)
    signed_in.post(url, json=body(exp=555))
    signed_in.post(url, json=body(exp=666))
    assert len(app.config["DATABASE"].session_events("zezima")) == 2


def test_discord_and_clan_are_never_written_down(signed_in, app):
    url = issue(signed_in)
    signed_in.post(url, json=body(
        discordUser={"id": "123", "name": "discord-handle-9f2a"},
        clanName="Clan-Name-9f2a", dinkAccountHash="hash-9f2a"))
    stored = app.config["DATABASE"].session_events("zezima")[0]["payload"]
    for unwanted in ("discordUser", "clanName", "dinkAccountHash",
                     "discord-handle-9f2a", "Clan-Name-9f2a", "hash-9f2a"):
        assert unwanted not in stored, unwanted


def test_other_metadata_is_accepted_and_dropped(signed_in, app):
    """Refusing it would only make the plugin retry it."""
    url = issue(signed_in)
    assert signed_in.post(url, json=body(kind="GROUP_STORAGE")).status_code == 204
    assert app.config["DATABASE"].session_events("zezima") == []


def test_a_login_missing_its_numbers_is_still_a_login(signed_in, app):
    """When the shape changes, the timestamp is the part worth keeping."""
    url = issue(signed_in)
    assert signed_in.post(url, json={"type": "LOGIN"}).status_code == 204
    rows = app.config["DATABASE"].session_events("zezima")
    assert len(rows) == 1
    assert rows[0]["total_exp"] is None


def test_multipart_is_read_too(signed_in, app):
    """The shape Dink uses when it attaches a screenshot."""
    url = issue(signed_in)
    response = signed_in.post(
        url, data={"payload_json": json.dumps(body(exp=999))},
        content_type="multipart/form-data")
    assert response.status_code == 204
    assert app.config["DATABASE"].session_events("zezima")[0]["total_exp"] == 999


def test_a_logout_is_recorded_too(signed_in, app):
    """Dink reports both ends; the logout carries no numbers, only the moment."""
    url = issue(signed_in)
    assert signed_in.post(url, json={"type": "LOGOUT", "playerName": "Zezima"}
                          ).status_code == 204
    rows = app.config["DATABASE"].session_events("zezima")
    assert len(rows) == 1
    assert rows[0]["kind"] == "logout"
    assert rows[0]["total_exp"] is None, "a logout tells us nothing about xp"


def test_a_logout_and_a_login_are_two_events(signed_in, app):
    url = issue(signed_in)
    signed_in.post(url, json=body(exp=500))
    signed_in.post(url, json={"type": "LOGOUT"})
    kinds = [r["kind"] for r in app.config["DATABASE"].session_events("zezima")]
    assert sorted(kinds) == ["login", "logout"]


def test_a_repeated_logout_is_one_logout(signed_in, app):
    """Logouts carry no experience, so they cannot be deduped the way logins are."""
    url = issue(signed_in)
    for _ in range(3):
        assert signed_in.post(url, json={"type": "LOGOUT"}).status_code == 204
    assert len(app.config["DATABASE"].session_events("zezima")) == 1


def test_a_logout_does_not_hide_a_login_with_no_numbers(signed_in, app):
    """Both carry no experience; they must still be told apart."""
    url = issue(signed_in)
    signed_in.post(url, json={"type": "LOGOUT"})
    signed_in.post(url, json={"type": "LOGIN"})
    assert len(app.config["DATABASE"].session_events("zezima")) == 2


# -- what it refuses ------------------------------------------------------

def test_an_unreadable_body_is_refused(signed_in, app):
    url = issue(signed_in)
    response = signed_in.post(url, data="not json at all",
                              content_type="application/json")
    assert response.status_code == 400
    assert app.config["DATABASE"].session_events("zezima") == []


def test_a_json_array_is_refused(signed_in, app):
    url = issue(signed_in)
    assert signed_in.post(url, json=[1, 2, 3]).status_code == 400


def test_an_oversized_body_is_refused(signed_in, app, caplog):
    """And says so. Every way out of this endpoint has to leave a line, or a
    request that was thrown away looks exactly like one that never came."""
    import logging
    url = issue(signed_in)
    with caplog.at_level(logging.INFO):
        response = signed_in.post(url, json=body(padding="x" * 70000))
    assert response.status_code == 413
    assert app.config["DATABASE"].session_events("zezima") == []
    assert any("over" in r.getMessage() and "bytes" in r.getMessage()
               for r in caplog.records), "the reason has to be in the log"


def test_a_multipart_with_no_payload_is_refused(signed_in, app):
    url = issue(signed_in)
    response = signed_in.post(url, data={"file": "just-an-image"},
                              content_type="multipart/form-data")
    assert response.status_code == 400
    assert app.config["DATABASE"].session_events("zezima") == []


def test_a_multipart_carrying_junk_is_refused(signed_in, app):
    url = issue(signed_in)
    response = signed_in.post(url, data={"payload_json": "{not json"},
                              content_type="multipart/form-data")
    assert response.status_code == 400
    assert app.config["DATABASE"].session_events("zezima") == []


def test_a_body_that_lost_its_length_says_so_rather_than_blaming_size(signed_in, app):
    """What an http:// URL produces after the redirect to https.

    The body does not survive that redirect, so there is nothing to accept.
    It must not answer 413: that sends whoever configured it hunting for a
    size problem when the real one is the scheme.
    """
    url = issue(signed_in)
    response = signed_in.post(url, data=b"", content_type="application/json",
                              content_length=None)
    assert response.status_code == 400
    assert b"large" not in response.data.lower()
    assert app.config["DATABASE"].session_events("zezima") == []


def _no_length(app, body, content_type, terminated=False):
    """A request context whose environ genuinely declares no length.

    The test client fills CONTENT_LENGTH in for you, which is the opposite of
    what these two cases are about - so the environ is built and the header
    removed by hand.
    """
    from werkzeug.test import EnvironBuilder
    environ = EnvironBuilder(path="/hook/dink/x", method="POST",
                             content_type=content_type).get_environ()
    environ.pop("CONTENT_LENGTH", None)
    environ["wsgi.input"] = io.BytesIO(body)
    environ["wsgi.input_terminated"] = terminated
    return app.request_context(environ)


def test_a_multipart_with_no_length_is_refused_rather_than_parsed(app):
    """Werkzeug cannot parse a form it has no length for."""
    from flask import request
    from wom.web.hooks import _body
    with _no_length(app, b"whatever", "multipart/form-data"):
        assert request.content_length is None, "the point of the fixture"
        assert _body() == (None, False)


def test_an_undeclared_body_is_still_bounded(app):
    """A chunked request declares no length; the read must still stop."""
    from flask import request
    from wom.web.hooks import _body, MAX_BODY
    with _no_length(app, b"x" * (MAX_BODY + 500), "application/json",
                    terminated=True):
        assert request.content_length is None, "the point of the fixture"
        assert _body() == (None, True), "an unbounded body must be refused"


def test_a_burst_from_one_token_is_refused(signed_in, app):
    url = issue(signed_in)
    app.config["LIMITS"].dink_per_token = Budget(2, 300)
    assert signed_in.post(url, json=body(exp=1)).status_code == 204
    assert signed_in.post(url, json=body(exp=2)).status_code == 204
    assert signed_in.post(url, json=body(exp=3)).status_code == 429
    assert len(app.config["DATABASE"].session_events("zezima")) == 2


def test_a_tripped_wire_does_not_stop_us_collecting(signed_in, app):
    """The tripwire stops us serving data. Losing a login loses it forever.

    A session boundary cannot be fetched again later, unlike everything the
    tripwire protects - so a burst of dashboard traffic must not quietly cost
    us the thing this endpoint exists to collect.
    """
    url = issue(signed_in)
    tripwire = app.config["LIMITS"].api_tripwire
    tripwire.tripped_at = datetime.now(timezone.utc)
    tripwire.tripped_by = "1.2.3.4"
    assert tripwire.tripped
    assert signed_in.post(url, json=body()).status_code == 204
    assert len(app.config["DATABASE"].session_events("zezima")) == 1


def test_every_attempt_at_the_hook_is_logged(client, caplog):
    """A call that never routes must still leave a trace, and never the token."""
    import logging
    with caplog.at_level(logging.INFO):
        client.get("/hook/dink/sixteen-chars-xy")
        client.post("/hook/nonsense")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("hook:")]
    assert len(lines) == 2, "both the wrong method and the wrong path"
    assert "GET" in lines[0] and "16 characters" in lines[0]
    assert "sixteen-chars-xy" not in " ".join(lines), "the secret is never written"


def test_an_unknown_token_is_a_miss_whatever_the_method(client):
    """The scheme hint is for people we know; a stranger learns nothing."""
    assert client.get("/hook/dink/whatever").status_code == 404
    assert client.put("/hook/dink/whatever").status_code == 405
