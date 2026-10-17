"""Jagex's hiscores: the client, and turning what it sends into a reading.

The fixtures under tests/fixtures/hiscores were recorded on 2026-10-17: four
real accounts' hiscores, each beside Wise Old Man's reading of the same
moment, with the names replaced.
"""

import json
import os

import pytest

from wom import hiscores

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "hiscores")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def reading(name="player_a"):
    return hiscores.to_snapshot(fixture(name + ".json"),
                                "2026-10-17T18:00:00.000Z")


def test_jagex_names_land_on_the_metrics_we_already_store():
    """Every stored reading uses Wise Old Man's names; a new spelling would
    start each renamed metric's history again from nothing."""
    data = reading()["data"]
    assert "runecrafting" in data["skills"]
    for name in ("last_man_standing", "pvp_arena", "guardians_of_the_rift",
                 "collections_logged", "clue_scrolls_all"):
        assert name in data["activities"], name
    for name in ("tombs_of_amascut_expert", "kreearra", "kril_tsutsaroth",
                 "vetion", "calvarion", "phosanis_nightmare",
                 "chambers_of_xeric_challenge_mode", "tzkal_zuk"):
        assert name in data["bosses"], name


def test_rows_nobody_tracks_are_dropped():
    data = reading()["data"]
    everything = set(data["activities"]) | set(data["bosses"])
    assert not everything & hiscores.IGNORED


def test_a_boss_jagex_adds_is_filed_as_a_boss_under_its_own_name():
    payload = fixture("player_a.json")
    payload["activities"].append(
        {"id": 999, "name": "The Brand-New Boss", "rank": 5, "score": 12})
    data = hiscores.to_snapshot(payload, "x")["data"]
    assert data["bosses"]["the_brand_new_boss"]["kills"] == 12


@pytest.mark.parametrize("name", ["player_a", "player_b", "player_c",
                                  "player_d"])
def test_every_metric_wise_old_man_stored_is_still_read(name):
    """A metric missing here would stop moving on every chart that shows it.
    The values themselves are held to Wise Old Man's in test_efficiency.py,
    where any one of them being off changes the hours it works out."""
    expected = fixture(name + ".wom.json")
    data = reading(name)["data"]
    assert set(expected["skills"]) <= set(data["skills"])
    assert set(expected["bosses"]) <= set(data["bosses"])


def test_an_unranked_skill_keeps_its_experience():
    payload = fixture("player_d.json")
    unranked = [row for row in payload["skills"] if row["rank"] == -1]
    assert unranked, "the fixture has one, or this proves nothing"
    data = hiscores.to_snapshot(payload, "x")["data"]
    name = hiscores.metric_name(unranked[0]["name"])
    assert data["skills"][name]["experience"] == unranked[0]["xp"] > 0


def test_combat_level_follows_the_game():
    assert hiscores.combat_level({}) == 3
    maxed = dict.fromkeys(("attack", "strength", "defence", "hitpoints",
                           "ranged", "magic", "prayer"), 99)
    assert hiscores.combat_level(maxed) == 126


class Reply:
    def __init__(self, code, body=None, text=None, headers=None):
        self.status_code = code
        self._body = body
        self.text = text if text is not None else json.dumps(body)
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


class Session:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.asked = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.asked.append((url, params))
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(hiscores.time, "sleep", lambda _s: None)


def client(*replies):
    return hiscores.HiscoresClient(session=Session(*replies), interval=0)


def test_a_lookup_asks_the_regular_table_by_name():
    reader = client(Reply(200, fixture("player_a.json")))
    assert reader.fetch("Some Name")["name"] == "player_a"
    url, params = reader.session.asked[0]
    assert "m=hiscore_oldschool/index_lite.json" in url
    assert params == {"player": "Some Name"}


def test_an_unknown_name_is_its_own_error():
    """A renamed account, which the caller handles differently from an
    outage: falling back to Wise Old Man would read the old name as current."""
    with pytest.raises(hiscores.HiscoresNotFound):
        client(Reply(404, text="<html>")).fetch("gone")


def test_a_cloudflare_challenge_is_a_refusal_not_data():
    with pytest.raises(hiscores.HiscoresError):
        client(Reply(200, text="<!DOCTYPE html><title>Just a moment</title>")
               ).fetch("someone")


def test_a_busy_answer_is_retried():
    reader = client(Reply(429, text="", headers={"Retry-After": "1"}),
                    Reply(503, text=""),
                    Reply(200, fixture("player_a.json")))
    assert reader.fetch("someone")["name"] == "player_a"
    assert len(reader.session.asked) == 3


def test_a_type_is_believed_only_where_the_table_is_current():
    """A dead hardcore's row stays on the hardcore table, frozen."""
    payload = fixture("player_a.json")
    exp = hiscores.overall(payload)
    frozen = json.loads(json.dumps(payload))
    frozen["skills"][0]["xp"] = exp - 10
    reader = client(Reply(404, text=""), Reply(200, frozen),
                    Reply(200, payload))
    assert reader.account_type("someone", exp) == "ironman"
    assert client(Reply(404, text=""), Reply(404, text=""),
                  Reply(404, text="")).account_type("someone", exp) == "regular"
