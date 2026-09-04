"""What a player reports while playing: kept whole, and read as a metric.

Two jobs per event, tested separately. The row keeps the detail nothing else
has room for - which item, which boss, which skill - and where the payload
happens to be a metric we already track, the value is written at the moment it
happened so the charts stop rounding it to the next poll.
"""

import json

import pytest

from conftest import snapshot
from wom import gameplay


def collection(item="Zamorak chaps", completed=420):
    return {"type": "COLLECTION", "extra": {
        "itemName": item, "itemId": 10372, "price": 500812,
        "completedEntries": completed, "totalEntries": 1443,
        "currentRank": "IRON", "dropperName": "Clue Scroll (Hard)"}}


def kill(boss="Chambers of Xeric", count=69):
    return {"type": "KILL_COUNT", "extra": {
        "boss": boss, "count": count, "isPersonalBest": True,
        "time": "PT46M34S", "party": ["Zezima"]}}


def levelup(skills=None):
    return {"type": "LEVEL", "extra": {
        "levelledSkills": skills or {"Attack": 70},
        "allSkills": {"Attack": 70, "Magic": 55},
        "combatLevel": {"value": 80, "increased": True}}}


WHEN = "2026-09-03T21:15:00.000000Z"


# -- naming -----------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Chambers of Xeric", "chambers_of_xeric"),
    ("Dagannoth Rex", "dagannoth_rex"),
    ("TzKal-Zuk", "tzkal_zuk"),
    ("Kree'arra", "kreearra"),
    ("Vet’ion", "vetion"),
])
def test_a_display_name_becomes_a_metric_name(name, expected):
    """Apostrophes vanish rather than becoming separators: Wise Old Man writes
    Kree'arra as kreearra, so treating one like a space misses it."""
    assert gameplay.slug(name) == expected


def test_a_boss_we_do_not_track_is_not_invented(db, player):
    """The name arrives from a plugin. A wrong guess would create a metric."""
    db.save_snapshot(player["id"], snapshot("2026-09-03T20:00:00.000Z",
                                            bosses={"zulrah": 10}))
    assert gameplay.boss_metric(db, "Zulrah") == "zulrah"
    assert gameplay.boss_metric(db, "Not A Real Boss") is None
    assert gameplay.boss_metric(db, "") is None


def test_the_article_is_tried_both_ways(db, player):
    """Wise Old Man keeps it for the_whisperer and drops it for nightmare,
    and there is no rule in which it does."""
    db.save_snapshot(player["id"], snapshot("2026-09-03T20:00:00.000Z",
                                            bosses={"nightmare": 3,
                                                    "the_whisperer": 5}))
    assert gameplay.boss_metric(db, "The Nightmare") == "nightmare"
    assert gameplay.boss_metric(db, "The Whisperer") == "the_whisperer"


# -- keeping the detail -----------------------------------------------------

def test_a_collection_log_slot_is_kept_whole(db, player):
    gameplay.store(db, player["username"], "collection", WHEN, collection())
    row = db.game_events(player["username"])[0]
    assert row["kind"] == "collection"
    assert row["subject"] == "Zamorak chaps"
    assert row["quantity"] == 420
    kept = json.loads(row["payload"])
    assert kept["extra"]["dropperName"] == "Clue Scroll (Hard)", (
        "the feed will want where it came from, which no metric has room for")


def test_a_kill_keeps_its_time_and_personal_best(db, player):
    gameplay.store(db, player["username"], "kill_count", WHEN, kill())
    kept = json.loads(db.game_events(player["username"])[0]["payload"])
    assert kept["extra"]["isPersonalBest"] is True
    assert kept["extra"]["time"] == "PT46M34S"


def test_three_skills_levelling_at_once_are_three_events(db, player):
    """One tick, three things that happened."""
    gameplay.store(db, player["username"], "level", WHEN,
                   levelup({"Attack": 70, "Strength": 71, "Magic": 55}))
    rows = db.game_events(player["username"], kind="level")
    assert sorted(r["subject"] for r in rows) == ["Attack", "Magic", "Strength"]
    assert {r["quantity"] for r in rows} == {70.0, 71.0, 55.0}


def test_the_same_event_twice_is_stored_once(db, player):
    """The plugin retries what it could not deliver."""
    for _ in range(3):
        gameplay.store(db, player["username"], "collection", WHEN, collection())
    assert len(db.game_events(player["username"])) == 1


def test_an_event_for_an_account_we_have_never_seen_is_still_kept(db):
    """The webhook can arrive before the first update run does."""
    gameplay.store(db, "stranger", "collection", WHEN, collection())
    assert db.game_events("stranger")[0]["player_id"] is None


# -- reading it as a metric -------------------------------------------------

def test_a_collection_slot_becomes_a_reading_at_that_moment(db, player):
    gameplay.store(db, player["username"], "collection", WHEN, collection(completed=651))
    standing = {r["metric"]: r["value"]
                for r in db.state_at(player["id"], WHEN, "activity")}
    assert standing["collections_logged"] == 651


def test_a_kill_count_becomes_a_reading_at_that_moment(db, player):
    db.save_snapshot(player["id"], snapshot("2026-09-03T20:00:00.000Z",
                                            bosses={"chambers_of_xeric": 60}))
    gameplay.store(db, player["username"], "kill_count", WHEN, kill(count=69))
    standing = {r["metric"]: r["value"]
                for r in db.state_at(player["id"], WHEN, "boss")}
    assert standing["chambers_of_xeric"] == 69


def test_a_levelup_is_kept_but_not_written_through(db, player):
    """Our level total shares a row with overall experience. Writing one
    without the other would read as authoritative while carrying half."""
    gameplay.store(db, player["username"], "level", WHEN, levelup())
    assert db.game_events(player["username"], kind="level")
    assert db.state_at(player["id"], WHEN, "skill") == []


def test_an_untracked_boss_keeps_the_event_and_writes_no_reading(db, player):
    gameplay.store(db, player["username"], "kill_count", WHEN,
                   kill(boss="Some Future Boss"))
    assert db.game_events(player["username"])[0]["subject"] == "Some Future Boss"
    assert db.state_at(player["id"], WHEN, "boss") == []


def test_a_reported_reading_is_not_cleared_by_recomputing_attribution(db, player):
    """Attribution clears what it worked out and must not touch what it was
    told - one is arithmetic, the other is evidence."""
    gameplay.store(db, player["username"], "collection", WHEN, collection(completed=651))
    db.clear_derived_state(player["id"], "2026-09-01")
    standing = {r["metric"]: r["value"]
                for r in db.state_at(player["id"], WHEN, "activity")}
    assert standing.get("collections_logged") == 651


def test_compaction_keeps_a_reported_reading(db, player):
    from conftest import as_polled
    gameplay.store(db, player["username"], "collection", "2026-01-05T05:00:00.000Z",
                   collection(completed=100))
    db.save_snapshot(player["id"], snapshot("2026-01-05T23:00:00.000Z",
                                            skills={"attack": (900, 40)}))
    as_polled(db)
    conn = db.connect()
    with conn:
        conn.execute("UPDATE snapshots SET origin='reported'"
                     " WHERE captured_at LIKE '2026-01-05T05%'")
    db.compact_snapshots(keep_days=30)
    kept = [r["captured_at"] for r in db.query(
        "SELECT captured_at FROM snapshots WHERE captured_at < '2026-02-01'")]
    assert "2026-01-05T05:00:00.000Z" in kept


def test_a_malformed_payload_is_kept_rather_than_dropped(db, player):
    """A shape we did not expect is still a thing that happened."""
    gameplay.store(db, player["username"], "collection", WHEN,
                   {"type": "COLLECTION", "extra": "not an object"})
    assert len(db.game_events(player["username"])) == 1
    assert db.game_events(player["username"])[0]["quantity"] is None


def test_a_levelup_with_no_skills_named_is_not_an_event(db, player):
    """A shape we cannot read is nothing that happened, not a blank thing."""
    gameplay.store(db, player["username"], "level", WHEN,
                   {"type": "LEVEL", "extra": {"levelledSkills": "broken"}})
    assert db.game_events(player["username"]) == []


def test_a_kind_we_do_not_handle_yields_nothing(db, player):
    assert gameplay.extract("loot", {"extra": {"items": []}}) == []
