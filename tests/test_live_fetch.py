"""A logout prompts a reading; a reported name can follow a rename."""

import json
import os

import pytest

from wom import changes
from wom.config import Config
from wom.hiscores import HiscoresNotFound
from wom.live_fetch import LogoutFetcher
from wom.renames import check_reported_name, rename_player

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "hiscores")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


class Hiscores:
    """Answers for the names it holds and 404s the rest."""

    def __init__(self, **by_name):
        self.by_name = {k.lower(): v for k, v in by_name.items()}
        self.asked = []

    def fetch(self, username, table="hiscore_oldschool"):
        self.asked.append(username)
        payload = self.by_name.get(username.lower())
        if payload is None:
            raise HiscoresNotFound(username + " is not on the hiscores", 404)
        return payload


@pytest.fixture
def zezima(db):
    db.save_player_details({"id": 1, "username": "zezima",
                            "displayName": "Zezima", "type": "regular"})
    config = Config()
    config["usernames"] = ["Friend", "Zezima"]
    config["dink_tokens"] = {"zezima": "secret"}
    config["player_colors"] = {"zezima": "#ff0000"}
    config.save()
    return db


def test_a_logout_reading_is_stored_and_signals_pages(zezima):
    before = changes.token()
    fetcher = LogoutFetcher(zezima, Hiscores(zezima=fixture("player_a.json")))
    again = fetcher.process("zezima")
    assert zezima.snapshot_count(1) == 1
    assert changes.token() != before, "open pages hear about it"
    assert again is False, "it moved, so there is nothing to retry"


def test_a_logout_that_moved_nothing_is_tried_once_more(zezima):
    fetcher = LogoutFetcher(zezima, Hiscores(zezima=fixture("player_a.json")))
    fetcher.process("zezima")
    assert fetcher.process("zezima", attempt=0) is True
    assert fetcher.process("zezima", attempt=1) is False, "once, not forever"


def test_a_player_not_introduced_yet_is_left_for_the_hourly_run(db):
    hiscores = Hiscores(zezima=fixture("player_a.json"))
    assert LogoutFetcher(db, hiscores).process("zezima") is False
    assert hiscores.asked == []


def test_repeated_logouts_collapse_into_one_reading(zezima):
    fetcher = LogoutFetcher(zezima, Hiscores(), delay=60)
    fetcher.enqueue("Zezima")
    fetcher.enqueue("zezima")
    assert list(fetcher._pending) == ["zezima"]


def test_a_logout_through_the_webhook_queues_a_reading(app, signed_in):
    from test_hooks import body, issue

    queued = []

    class Recorder:
        def enqueue(self, username, reported=None):
            queued.append((username, reported))

    app.config["LOGOUTS"] = Recorder()
    url = issue(signed_in)
    signed_in.post(url, json=body(kind="LOGIN"))
    assert queued == [], "a login changes nothing on the hiscores"
    signed_in.post(url, json={"type": "LOGOUT", "playerName": "Zezima",
                              "world": 302})
    assert queued == [("zezima", "Zezima")]


def test_a_rename_moves_every_row_and_setting(zezima):
    zezima.record_session_event("zezima", "logout", {"total_exp": None}, {})
    assert rename_player(zezima, Config(), "zezima", "Zezima Two")
    config = Config()
    assert config["usernames"] == ["Friend", "Zezima Two"], "same place"
    assert config["dink_tokens"] == {"zezima two": "secret"}
    assert config["player_colors"] == {"zezima two": "#ff0000"}
    assert zezima.player_by_username("zezima two")["id"] == 1
    assert zezima.player_by_username("zezima") is None
    assert zezima.session_events("zezima two")


def test_a_reported_name_is_followed_when_the_old_one_is_gone(zezima):
    hiscores = Hiscores(**{"zezima two": fixture("player_a.json")})
    now = check_reported_name(zezima, Config(), hiscores, "zezima", "Zezima Two")
    assert now == "zezima two"
    assert Config()["usernames"] == ["Friend", "Zezima Two"]


def test_an_alt_on_the_same_token_is_flagged_not_followed(zezima):
    """Both names on the hiscores: that is two accounts, not a rename."""
    hiscores = Hiscores(zezima=fixture("player_a.json"),
                        alt=fixture("player_b.json"))
    now = check_reported_name(zezima, Config(), hiscores, "zezima", "Alt")
    assert now == "zezima"
    assert Config()["usernames"] == ["Friend", "Zezima"]
    assert Config()["name_flags"] == {"zezima": "Alt"}


def test_the_same_name_in_other_capitals_is_not_a_rename(zezima):
    hiscores = Hiscores()
    assert check_reported_name(zezima, Config(), hiscores, "zezima",
                               "ZEZIMA") == "zezima"
    assert hiscores.asked == []


def test_typing_the_new_name_over_the_old_keeps_the_history(
        zezima, app, signed_in, monkeypatch):
    """Without this, a hand-fixed rename read as one player leaving and a
    stranger arriving, and the next prune deleted the old history."""
    from wom import hiscores
    monkeypatch.setattr(hiscores, "shared", lambda: Hiscores(
        **{"zezima two": fixture("player_a.json")}))
    app.config["DATABASE"] = zezima
    signed_in.post("/admin/settings",
                   data={"usernames": "Friend\nZezima Two"})
    assert zezima.player_by_username("zezima two")["id"] == 1
    assert Config()["dink_tokens"] == {"zezima two": "secret"}


def test_swapping_one_friend_for_another_is_not_a_rename(
        zezima, app, signed_in, monkeypatch):
    from wom import hiscores
    monkeypatch.setattr(hiscores, "shared", lambda: Hiscores(
        zezima=fixture("player_a.json"), newcomer=fixture("player_b.json")))
    app.config["DATABASE"] = zezima
    signed_in.post("/admin/settings", data={"usernames": "Friend\nNewcomer"})
    assert zezima.player_by_username("zezima") is not None
    assert zezima.player_by_username("newcomer") is None
