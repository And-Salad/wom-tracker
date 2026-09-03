"""The Dink webhook: the only unauthenticated write in the app.

Everything here is about what an endpoint on the open internet accepts and
what it refuses, because the URL is the whole credential and there is nobody
signed in to check.
"""

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
    assert ">1<" in section, "the login count must move"
    assert "12,345,678" in section, (
        "the experience we captured is how you tell the body was parsed, "
        "not merely that something arrived")


# -- what it stores -------------------------------------------------------

def test_a_login_is_recorded(signed_in, app):
    url = issue(signed_in)
    assert signed_in.post(url, json=body(exp=12345678, world=420)).status_code == 204
    rows = app.config["DATABASE"].logins("zezima")
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
    assert app.config["DATABASE"].logins("zezima")[0]["player_id"] == 7


def test_a_login_is_kept_for_an_account_we_have_never_seen(signed_in, app):
    """The webhook can arrive before the first update run does."""
    url = issue(signed_in)
    signed_in.post(url, json=body())
    row = app.config["DATABASE"].logins("zezima")[0]
    assert row["player_id"] is None
    assert row["username"] == "zezima"


def test_a_retry_is_not_a_second_session(signed_in, app):
    """Dink resends what it could not deliver, with a new timestamp."""
    url = issue(signed_in)
    for _ in range(3):
        assert signed_in.post(url, json=body(exp=555)).status_code == 204
    assert len(app.config["DATABASE"].logins("zezima")) == 1


def test_logging_in_again_after_gaining_xp_is_a_new_session(signed_in, app):
    url = issue(signed_in)
    signed_in.post(url, json=body(exp=555))
    signed_in.post(url, json=body(exp=666))
    assert len(app.config["DATABASE"].logins("zezima")) == 2


def test_discord_and_clan_are_never_written_down(signed_in, app):
    url = issue(signed_in)
    signed_in.post(url, json=body(
        discordUser={"id": "123", "name": "discord-handle-9f2a"},
        clanName="Clan-Name-9f2a", dinkAccountHash="hash-9f2a"))
    stored = app.config["DATABASE"].logins("zezima")[0]["payload"]
    for unwanted in ("discordUser", "clanName", "dinkAccountHash",
                     "discord-handle-9f2a", "Clan-Name-9f2a", "hash-9f2a"):
        assert unwanted not in stored, unwanted


def test_other_metadata_is_accepted_and_dropped(signed_in, app):
    """Refusing it would only make the plugin retry it."""
    url = issue(signed_in)
    assert signed_in.post(url, json=body(kind="GROUP_STORAGE")).status_code == 204
    assert app.config["DATABASE"].logins("zezima") == []


def test_a_login_missing_its_numbers_is_still_a_login(signed_in, app):
    """When the shape changes, the timestamp is the part worth keeping."""
    url = issue(signed_in)
    assert signed_in.post(url, json={"type": "LOGIN"}).status_code == 204
    rows = app.config["DATABASE"].logins("zezima")
    assert len(rows) == 1
    assert rows[0]["total_exp"] is None


def test_multipart_is_read_too(signed_in, app):
    """The shape Dink uses when it attaches a screenshot."""
    url = issue(signed_in)
    response = signed_in.post(
        url, data={"payload_json": json.dumps(body(exp=999))},
        content_type="multipart/form-data")
    assert response.status_code == 204
    assert app.config["DATABASE"].logins("zezima")[0]["total_exp"] == 999


# -- what it refuses ------------------------------------------------------

def test_an_unreadable_body_is_refused(signed_in, app):
    url = issue(signed_in)
    response = signed_in.post(url, data="not json at all",
                              content_type="application/json")
    assert response.status_code == 400
    assert app.config["DATABASE"].logins("zezima") == []


def test_a_json_array_is_refused(signed_in, app):
    url = issue(signed_in)
    assert signed_in.post(url, json=[1, 2, 3]).status_code == 400


def test_an_oversized_body_is_refused(signed_in, app):
    url = issue(signed_in)
    response = signed_in.post(url, json=body(padding="x" * 70000))
    assert response.status_code == 413
    assert app.config["DATABASE"].logins("zezima") == []


def test_a_multipart_with_no_payload_is_refused(signed_in, app):
    url = issue(signed_in)
    response = signed_in.post(url, data={"file": "just-an-image"},
                              content_type="multipart/form-data")
    assert response.status_code == 400
    assert app.config["DATABASE"].logins("zezima") == []


def test_a_multipart_carrying_junk_is_refused(signed_in, app):
    url = issue(signed_in)
    response = signed_in.post(url, data={"payload_json": "{not json"},
                              content_type="multipart/form-data")
    assert response.status_code == 400
    assert app.config["DATABASE"].logins("zezima") == []


def test_a_burst_from_one_token_is_refused(signed_in, app):
    url = issue(signed_in)
    app.config["LIMITS"].dink_per_token = Budget(2, 300)
    assert signed_in.post(url, json=body(exp=1)).status_code == 204
    assert signed_in.post(url, json=body(exp=2)).status_code == 204
    assert signed_in.post(url, json=body(exp=3)).status_code == 429
    assert len(app.config["DATABASE"].logins("zezima")) == 2


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
    assert len(app.config["DATABASE"].logins("zezima")) == 1


def test_the_endpoint_only_takes_posts(client):
    assert client.get("/hook/dink/whatever").status_code in (404, 405)
