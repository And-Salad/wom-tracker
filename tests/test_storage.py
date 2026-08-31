"""Storage: history, compaction, pruning and the export query."""

from conftest import snapshot


def test_compaction_keeps_each_days_last_reading(db, player):
    """Ids run backwards within a day because backfill inserts newest first.

    Keeping MAX(id) therefore kept the day's *first* reading, quietly losing
    the day's progress. The captured_at is what decides.
    """
    for hour, kills in ((1, 10), (7, 20), (13, 30), (19, 44)):
        db.save_snapshot(player["id"], snapshot(
            "2026-01-05T{:02d}:00:00.000Z".format(hour), bosses={"zulrah": kills}))
    db.save_snapshot(player["id"], snapshot("2026-08-30T00:00:00.000Z",
                                            bosses={"zulrah": 90}))

    db.compact_snapshots(keep_days=30)
    kept = db.query(
        "SELECT captured_at FROM snapshots WHERE player_id=? AND captured_at < ?"
        " ORDER BY captured_at", (player["id"], "2026-02-01"))
    assert len(kept) == 1
    assert kept[0]["captured_at"].startswith("2026-01-05T19"), (
        "the last reading of the day is the one worth keeping")


def test_compaction_leaves_the_recent_window_alone(db, player):
    """The day and week views need every reading, so recent history is raw."""
    for hour in (1, 7, 13, 19):
        db.save_snapshot(player["id"], snapshot(
            "2026-08-30T{:02d}:00:00.000Z".format(hour), bosses={"zulrah": hour}))
    before = db.snapshot_count(player["id"])
    db.compact_snapshots(keep_days=3650)
    assert db.snapshot_count(player["id"]) == before


def test_pruning_with_an_empty_keep_list_removes_everyone(db):
    """`x NOT IN (NULL)` is NULL, not true, so this once pruned nothing."""
    for n, name in enumerate(("a", "b", "c"), start=1):
        db.save_player_details({"id": n, "username": name, "displayName": name})
    assert db.prune_players(["a"]) == 2
    assert db.prune_players([]) == 1
    assert db.players() == []


def test_pruning_cascades_to_everything_the_player_owned(db, player):
    db.save_snapshot(player["id"], snapshot("2026-08-30T00:00:00.000Z",
                                            bosses={"zulrah": 5}))
    db.prune_players([])
    assert db.query_one("SELECT COUNT(*) n FROM snapshots")["n"] == 0
    assert db.query_one("SELECT COUNT(*) n FROM metrics")["n"] == 0


def test_history_is_not_silently_truncated(db, player):
    """A row cap once cut long histories off at 500, losing the oldest points."""
    for day in range(1, 29):
        db.save_snapshot(player["id"], snapshot(
            "2026-06-{:02d}T00:00:00.000Z".format(day),
            skills={"overall": (day * 1000, day)}))
    rows = db.metric_history(player["id"], "overall", "skill")
    assert len(rows) == 28


def test_export_rows_filters_and_streams(db, player):
    db.save_snapshot(player["id"], snapshot(
        "2026-08-29T00:00:00.000Z", skills={"attack": (100, 1)},
        bosses={"zulrah": 5}))
    db.save_snapshot(player["id"], snapshot(
        "2026-08-31T00:00:00.000Z", skills={"attack": (200, 2)},
        bosses={"zulrah": 9}))

    everything = list(db.export_rows([player["id"]]))
    assert {r["kind"] for r in everything} == {"skill", "boss"}

    skills_only = list(db.export_rows([player["id"]], kinds=["skill"]))
    assert {r["kind"] for r in skills_only} == {"skill"}

    windowed = list(db.export_rows([player["id"]],
                                   since="2026-08-30T00:00:00.000Z"))
    assert all(r["captured_at"] >= "2026-08-30" for r in windowed)
    assert windowed, "the filter should not empty the result"

    assert list(db.export_rows([])) == [], "no players means no rows, not all rows"


def test_unranked_is_stored_as_missing_not_as_minus_one(db, player):
    db.save_snapshot(player["id"], snapshot("2026-08-30T00:00:00.000Z",
                                            bosses={"zulrah": -1}))
    row = db.query_one("SELECT value FROM metrics WHERE metric='zulrah'")
    assert row["value"] is None, "-1 means unranked, and must not read as a score"


def test_a_repeated_snapshot_is_stored_once(db, player):
    payload = snapshot("2026-08-30T00:00:00.000Z", bosses={"zulrah": 5})
    assert db.save_snapshot(player["id"], payload) is not None
    assert db.save_snapshot(player["id"], payload) is None
    assert db.snapshot_count(player["id"]) == 1
