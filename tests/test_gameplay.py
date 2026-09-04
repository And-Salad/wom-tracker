"""What a player reports while playing: kept whole, and read as a metric.

Two jobs per event, tested separately. The row keeps the detail nothing else
has room for - which item, which boss, which skill - and where the payload
happens to be a metric we already track, the value is written at the moment it
happened so the charts stop rounding it to the next poll.
"""

import json

import pytest
from conftest import as_polled, snapshot

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
    gameplay.store(db, player["username"], "collection", WHEN,
                   collection(completed=651))
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
    gameplay.store(db, player["username"], "collection", WHEN,
                   collection(completed=651))
    db.clear_derived_state(player["id"], "2026-09-01")
    standing = {r["metric"]: r["value"]
                for r in db.state_at(player["id"], WHEN, "activity")}
    assert standing.get("collections_logged") == 651


def test_compaction_keeps_a_reported_reading(db, player):
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


# -- the milestones feed ----------------------------------------------------

def quest(name="Dragon Slayer I", done=22):
    return {"type": "QUEST", "extra": {"questName": name, "completedQuests": done,
                                       "totalQuests": 156, "questPoints": 44}}


def diary(area="Varrock", difficulty="HARD", total=15):
    return {"type": "ACHIEVEMENT_DIARY", "extra": {
        "area": area, "difficulty": difficulty, "total": total,
        "areaTasksCompleted": 37, "areaTasksTotal": 42}}


def combat(task="Peach Conjurer", tier="GRANDMASTER"):
    return {"type": "COMBAT_ACHIEVEMENT", "extra": {
        "tier": tier, "task": task, "taskPoints": 6, "totalPoints": 1337}}


def test_a_quest_a_diary_and_a_combat_task_are_kept(db, player):
    for kind, payload in (("quest", quest()), ("diary", diary()),
                          ("combat_task", combat())):
        gameplay.store(db, player["username"], kind, WHEN, payload)
    got = {r["kind"]: r["subject"] for r in db.game_events(player["username"])}
    assert got == {"quest": "Dragon Slayer I", "diary": "Varrock Hard",
                   "combat_task": "Peach Conjurer"}


def test_a_diary_names_its_area_and_difficulty_together(db, player):
    """'Varrock' alone does not say which diary, and 'HARD' does not say where."""
    assert gameplay.extract("diary", diary())[0][0] == "Varrock Hard"
    assert gameplay.extract("diary", {"extra": {"area": "Varrock"}}) == []


def test_the_qualifier_says_how_much_it_meant():
    assert gameplay.detail("combat_task", combat()) == "Grandmaster"
    assert gameplay.detail("diary", diary()) == "15 diaries done"
    assert gameplay.detail("quest", quest()) == "22 of 156"
    assert gameplay.detail("collection", collection(completed=651)) == "651 of 1,443"
    assert gameplay.detail("kill_count", kill()) == "", "not a feed kind"


def test_none_of_the_three_pretend_to_be_a_metric(db, player):
    """We track no quest, diary or combat task metric, so nothing is written
    through - the event is the whole of what we know."""
    for kind, payload in (("quest", quest()), ("diary", diary()),
                          ("combat_task", combat())):
        gameplay.store(db, player["username"], kind, WHEN, payload)
    assert db.query_one("SELECT COUNT(*) AS n FROM snapshots"
                        " WHERE origin='reported'")["n"] == 0


def test_the_feed_merges_both_sources_newest_first(db, player):
    from wom.web import views
    db.save_achievements(player["id"], [{
        "name": "99 Attack", "metric": "attack", "measure": "experience",
        "threshold": 13034431, "createdAt": "2026-09-02T10:00:00.000Z",
        "accuracy": 3600000}])
    gameplay.store(db, player["username"], "quest", "2026-09-03T21:15:00.000000Z",
                   quest())
    gameplay.store(db, player["username"], "collection",
                   "2026-09-01T08:00:00.000000Z", collection())

    feed = views.milestone_feed(db, [dict(player)], {})
    assert [row["category"] for row in feed] == ["quest", "milestone", "collection"]
    assert feed[0]["name"] == "Dragon Slayer I"
    assert feed[0]["detail"] == "22 of 156"


def test_a_milestone_with_no_date_sorts_last(db, player):
    """It is not news, and on top it would push out what happened today."""
    from wom.web import views
    db.save_achievements(player["id"], [{
        "name": "Undated thing", "metric": "attack", "measure": "experience",
        "threshold": 1, "createdAt": None, "accuracy": -1}])
    gameplay.store(db, player["username"], "quest", "2026-09-03T21:15:00.000000Z",
                   quest())
    feed = views.milestone_feed(db, [dict(player)], {})
    assert feed[-1]["name"] == "Undated thing"
    assert feed[-1]["when"] == "unknown"


def test_only_the_selected_players_appear(db, player):
    from wom.web import views
    gameplay.store(db, "someone else", "quest", WHEN, quest())
    gameplay.store(db, player["username"], "quest", WHEN, quest("Cook's Assistant"))
    feed = views.milestone_feed(db, [dict(player)], {})
    assert [row["name"] for row in feed] == ["Cook's Assistant"]


def test_a_count_that_is_not_a_number_is_shown_as_it_arrived():
    """The payload is the plugin's, not ours, so nothing here may explode."""
    assert gameplay.detail("quest", {"extra": {"completedQuests": "many",
                                               "totalQuests": 156}}) == "many of 156"


def test_a_feed_row_with_unreadable_stored_json_still_appears(db, player):
    from wom.web import views
    gameplay.store(db, player["username"], "quest", WHEN, quest())
    conn = db.connect()
    with conn:
        conn.execute("UPDATE game_events SET payload='{not json'")
    feed = views.milestone_feed(db, [dict(player)], {})
    assert len(feed) == 1, "the event happened whatever the payload says now"
    assert feed[0]["detail"] == ""


def test_a_pet_says_what_made_it_worth_mentioning():
    """A duplicate and a milestone are the two things a pet line can add."""
    assert gameplay.detail("pet", {"extra": {"duplicate": True}}) == "duplicate"
    assert gameplay.detail("pet", {"extra": {"milestone": "5,000 killcount"}}) \
        == "5,000 killcount"
    assert gameplay.detail("pet", {"extra": {"petName": "Ikkle hydra"}}) == ""
