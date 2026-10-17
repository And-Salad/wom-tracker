"""EHP, EHB and milestones, worked out here - held to Wise Old Man's answers.

Ported rather than reinterpreted, so the only acceptable test is agreement:
each fixture is a real account's hiscores beside Wise Old Man's figures for
the same moment, and ours have to match them.
"""

import json
import os

import pytest

from wom import efficiency, hiscores, milestones

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "hiscores")
ACCOUNTS = ["player_a", "player_b", "player_c", "player_d"]


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def ironman():
    return efficiency.Algorithm(fixture("rates_ironman_ehp.json"),
                                fixture("rates_ironman_ehb.json"))


def reading(name):
    return hiscores.to_snapshot(fixture(name + ".json"),
                                "2026-10-17T18:00:00.000Z")


@pytest.mark.parametrize("name", ACCOUNTS)
def test_totals_match_wise_old_man(name, ironman):
    expected = fixture(name + ".wom.json")
    ehp, ehb, ttm = efficiency.annotate(reading(name), ironman)
    assert ehp == pytest.approx(expected["ehp"], abs=1e-3)
    assert ehb == pytest.approx(expected["ehb"], abs=1e-3)
    assert ttm == pytest.approx(expected["ttm"], abs=1e-3)


@pytest.mark.parametrize("name", ACCOUNTS)
def test_each_skill_and_boss_matches_too(name, ironman):
    """The per-metric figures are stored on every metric row, and the store
    compares them to decide whether anything changed - so a near miss here
    would rewrite rows that did not move."""
    expected = fixture(name + ".wom.json")
    snap = reading(name)
    efficiency.annotate(snap, ironman)
    for skill, hours in expected["skills"].items():
        assert snap["data"]["skills"][skill]["ehp"] == pytest.approx(
            hours or 0, abs=1e-4), skill
    for boss, hours in expected["bosses"].items():
        assert snap["data"]["bosses"][boss]["ehb"] == pytest.approx(
            hours or 0, abs=1e-4), boss


def test_no_rates_means_no_figures_rather_than_wrong_ones():
    snap = reading("player_a")
    assert efficiency.annotate(snap, None) == (None, None, None)
    assert "ehp" not in snap["data"]["skills"]["attack"]


def test_account_types_choose_wise_old_mans_tables():
    assert efficiency.algorithm_type("hardcore") == "ironman"
    assert efficiency.algorithm_type("ultimate") == "ultimate"
    assert efficiency.algorithm_type("regular") == "main"
    assert efficiency.algorithm_type(None) == "main"


@pytest.mark.parametrize("name", ACCOUNTS)
def test_milestones_met_are_exactly_wise_old_mans(name):
    """Same names, metrics, measures and thresholds - the names are the key
    every stored milestone is filed under."""
    expected = {tuple(a) for a in fixture(name + ".wom.json")["achievements"]}
    met = {(d.name, d.metric, d.measure, d.threshold)
           for d in milestones.met(milestones.values_of(reading(name)))}
    assert met == expected


def test_names_are_written_the_way_wise_old_man_writes_them():
    names = {d.name for d in milestones.DEFINITIONS}
    for name in ("99 Attack", "50m Attack", "200m Sailing",
                 "1k Mad Angel kills", "10k Zulrah kills",
                 "4.60b Overall Exp.", "Base 60 Stats", "Maxed Overall",
                 "500 Chambers Of Xeric (CM) kills"):
        assert name in names, name


def test_a_crossing_is_dated_to_the_reading_that_saw_it():
    rows = milestones.crossed({"zulrah": 499}, {"zulrah": 500},
                              "2026-10-17T17:00:00.000Z",
                              "2026-10-17T18:00:00.000Z")
    assert [r["name"] for r in rows] == ["500 Zulrah kills"]
    assert rows[0]["createdAt"] == "2026-10-17T18:00:00.000Z"
    assert rows[0]["accuracy"] == 3_600_000


def test_a_metric_seen_for_the_first_time_crosses_nothing():
    """Wise Old Man's rule: a boss with no count before cannot be dated."""
    assert milestones.crossed({"attack": 1}, {"zulrah": 600}, "a", "b") == []
    assert milestones.crossed(None, {"zulrah": 600}, "a", "b") == []
