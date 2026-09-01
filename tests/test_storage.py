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


# -- only what changed ----------------------------------------------------

def test_an_unchanged_reading_stores_no_metrics_but_is_still_a_reading(db, player):
    """91 of every 100 rows repeated the reading before them. A reading where
    nothing moved is still evidence somebody looked, which is a different fact
    from the numbers and is kept in `snapshots`."""
    for stamp in ("2026-08-01T00:00:00.000Z", "2026-08-01T06:00:00.000Z"):
        db.save_snapshot(player["id"], snapshot(stamp, skills={"attack": (1000, 40)}))
    assert db.query_one("SELECT COUNT(*) c FROM snapshots")["c"] == 2
    assert db.query_one("SELECT COUNT(*) c FROM metrics")["c"] == 1

    db.save_snapshot(player["id"], snapshot("2026-08-01T12:00:00.000Z",
                                      skills={"attack": (2000, 41)}))
    assert db.query_one("SELECT COUNT(*) c FROM metrics")["c"] == 2


def test_a_reading_is_read_back_whole_however_little_of_it_was_stored(db, player):
    for stamp, xp in (("2026-08-01T00:00:00.000Z", 1000),
                      ("2026-08-02T00:00:00.000Z", 1000),
                      ("2026-08-03T00:00:00.000Z", 5000)):
        db.save_snapshot(player["id"], snapshot(stamp, skills={"attack": (xp, 40)},
                                          bosses={"zulrah": 7}))
    # Zulrah was written once and never again; it still answers for every day.
    for when, expected in (("2026-08-01T12:00:00.000Z", 1000),
                           ("2026-08-02T12:00:00.000Z", 1000),
                           ("2026-08-03T12:00:00.000Z", 5000)):
        state = {r["metric"]: r["value"]
                 for r in db.state_at(player["id"], when)}
        assert state["attack"] == expected
        assert state["zulrah"] == 7, "carried forward from the first reading"


def test_a_flat_line_is_still_a_measured_line(db, player):
    """Drop the readings where nothing moved and a quiet fortnight looks like
    a hole in the data, which is what a dashed stretch is meant to mean."""
    for day in range(1, 6):
        db.save_snapshot(player["id"], snapshot(
            "2026-08-0{}T00:00:00.000Z".format(day), skills={"attack": (1000, 40)}))
    points = db.metric_history(player["id"], "attack", "skill")
    assert len(points) == 5, "one point per reading, not per change"
    assert {p["value"] for p in points} == {1000}


def test_compaction_leaves_every_surviving_reading_saying_what_it_said(db, player):
    """A change deleted while the reading after it survives would leave that
    reading carrying an older value - worse than losing the detail."""
    for hour, xp in (("00", 1000), ("06", 2000), ("12", 3000), ("18", 4000)):
        db.save_snapshot(player["id"], snapshot(
            "2020-01-01T{}:00:00.000Z".format(hour), skills={"attack": (xp, 40)}))
    db.save_snapshot(player["id"], snapshot("2026-08-31T00:00:00.000Z",
                                      skills={"attack": (9000, 50)}))
    db.compact_snapshots(keep_days=30)

    kept = db.observations(player["id"], "2020-01-01", "2020-01-02")
    assert len(kept) == 1, "one reading a day survives"
    state = {r["metric"]: r["value"] for r in db.state_at(player["id"], kept[0])}
    assert state["attack"] == 4000, "and it still says what it said"


def test_only_the_newest_payload_is_kept(db, player):
    for stamp in ("2026-08-01T00:00:00.000Z", "2026-08-02T00:00:00.000Z"):
        db.save_snapshot(player["id"], snapshot(stamp, skills={"attack": (1000, 40)}))
    rows = db.query("SELECT captured_at, payload FROM snapshots ORDER BY captured_at")
    assert rows[0]["payload"] == ""
    assert rows[1]["payload"], "the newest is kept as a sample of the API's shape"


def test_the_old_shape_migrates_without_changing_a_single_answer(tmp_path):
    """The migration is the risky half: it rewrites 66,000 rows in place."""
    import sqlite3
    from wom.db import Database, SCHEMA

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.replace(
        """CREATE TABLE IF NOT EXISTS metrics (
    player_id   INTEGER NOT NULL,
    kind        TEXT NOT NULL,                    -- skill | boss | activity | computed
    metric      TEXT NOT NULL,                    -- e.g. overall, zulrah, ehp
    captured_at TEXT NOT NULL,
    value       REAL,                             -- experience | kills | score | value
    rank        INTEGER,
    level       INTEGER,                          -- skills only
    efficiency  REAL,                             -- ehp for skills, ehb for bosses
    PRIMARY KEY (player_id, kind, metric, captured_at)
) WITHOUT ROWID;""",
        """CREATE TABLE metrics (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    player_id   INTEGER NOT NULL, captured_at TEXT NOT NULL,
    kind TEXT NOT NULL, metric TEXT NOT NULL,
    value REAL, rank INTEGER, level INTEGER, efficiency REAL,
    PRIMARY KEY (snapshot_id, kind, metric));"""))
    conn.execute("INSERT INTO players (id, username, display_name) VALUES (1,'z','Z')")
    # Three readings, the middle one identical to the first.
    for sid, (stamp, xp) in enumerate((("2026-08-01T00:00:00.000Z", 1000),
                                       ("2026-08-01T06:00:00.000Z", 1000),
                                       ("2026-08-02T00:00:00.000Z", 5000)), start=1):
        conn.execute("INSERT INTO snapshots (id, player_id, captured_at, fetched_at,"
                     " payload) VALUES (?,?,?,?,?)", (sid, 1, stamp, stamp, "{}"))
        for kind, metric, value in (("skill", "attack", xp), ("boss", "zulrah", 7)):
            conn.execute("INSERT INTO metrics (snapshot_id, player_id, captured_at,"
                         " kind, metric, value, rank, level, efficiency)"
                         " VALUES (?,?,?,?,?,?,?,?,?)",
                         (sid, 1, stamp, kind, metric, value, 1, 40, None))
    conn.commit()
    conn.close()

    database = Database(path)           # migrates on open
    assert database.query_one("SELECT COUNT(*) c FROM metrics")["c"] == 3, \
        "six rows of which three repeated the one before"
    assert database.query_one("SELECT COUNT(*) c FROM snapshots")["c"] == 3, \
        "every reading is kept, whatever it was worth"
    # And every reading still answers exactly as it did.
    for when, expected in (("2026-08-01T00:00:00.000Z", 1000),
                           ("2026-08-01T06:00:00.000Z", 1000),
                           ("2026-08-02T00:00:00.000Z", 5000)):
        state = {r["metric"]: r["value"] for r in database.state_at(1, when)}
        assert state == {"attack": expected, "zulrah": 7}
