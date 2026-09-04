"""Storage: history, compaction, pruning and the export query."""

import pytest
from conftest import as_polled, before_migration, snapshot

# The two migrations these tests are about, by the number migrations.py gives
# them. Named here so a test says which step it is staging rather than a bare
# integer, and so renumbering one of them breaks loudly.
DROP_UNGROUPED_RECAPS = 6
LABEL_SNAPSHOT_ORIGINS = 8


def test_a_database_can_be_opened_by_a_bare_file_name(tmp_path, monkeypatch):
    """os.makedirs("") raises rather than doing nothing.

    db_path() always names a directory, so this only ever met a caller that
    passed a path of its own - a script run from beside the file, say.
    """
    from wom.db import Database
    monkeypatch.chdir(tmp_path)
    assert Database("bare.db").players() == []


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

    as_polled(db)
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
    as_polled(db)
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

    from wom.db import SCHEMA, Database

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    # The current definition is cut out of SCHEMA rather than quoted, so that
    # adding a column to metrics does not silently turn this test into one
    # that builds the new shape and asserts the migration left it alone.
    opening = SCHEMA.index("CREATE TABLE IF NOT EXISTS metrics (")
    current = SCHEMA[opening:SCHEMA.index(";", opening) + 1]
    conn.executescript(SCHEMA.replace(current, """CREATE TABLE metrics (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    player_id   INTEGER NOT NULL, captured_at TEXT NOT NULL,
    kind TEXT NOT NULL, metric TEXT NOT NULL,
    value REAL, rank INTEGER, level INTEGER, efficiency REAL,
    PRIMARY KEY (snapshot_id, kind, metric));"""))
    assert "snapshot_id" in conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='metrics'").fetchone()[0], \
        "the point of this test is that it starts from the old shape"
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


def test_group_recaps_outside_the_new_schedule_are_dropped(tmp_path):
    """The group recap became the leaderboard's feed, which judges days and
    awards months. The weekly, quarterly and yearly ones already written
    described windows nothing on the page could illustrate."""
    from wom import periods
    from wom.db import Database

    path = str(tmp_path / "recaps.db")
    database = Database(path)
    for key in periods.SUMMARY_PERIODS:
        database.save_group_summary(periods.latest_window(key),
                                    "A {} recap.".format(key), "hash")
    kept = {row["period"] for row in database.group_summaries()}
    assert kept == set(periods.SUMMARY_PERIODS), "all five are there to start"

    # Reopening runs the migration, as a deploy onto an older file would.
    before_migration(database, DROP_UNGROUPED_RECAPS)
    again = Database(path)
    assert {row["period"] for row in again.group_summaries()} == set(
        periods.GROUP_PERIODS)


def test_a_players_own_notes_survive_that_drop(tmp_path):
    """They are about one account's progress, which a quarter still says
    something about even where the leaderboard has no verdict."""
    from wom import periods
    from wom.db import Database

    path = str(tmp_path / "notes.db")
    database = Database(path)
    database.save_player_details({"id": 1, "username": "zezima",
                                  "displayName": "Zezima", "type": "regular"})
    for key in periods.SUMMARY_PERIODS:
        database.save_summary(1, periods.latest_window(key),
                              "A {} note.".format(key), "hash")

    again = Database(path)
    assert {row["period"] for row in again.summaries(player_id=1)} == set(
        periods.SUMMARY_PERIODS)


# -- where a reading came from, and what compaction does about it ---------

def test_a_reading_we_caused_is_marked_as_ours(db, player):
    """Wise Old Man stamped it as we asked for it, so we can ask again."""
    from datetime import datetime, timezone

    from wom.util import api_stamp
    db.save_snapshot(player["id"], snapshot(api_stamp(datetime.now(timezone.utc)),
                                            skills={"attack": (100, 40)}))
    assert db.query_one("SELECT origin FROM snapshots")["origin"] == "poll"


def test_a_reading_that_already_existed_is_marked_archive(db, player):
    """Made without us - which is the only kind that records a moment we
    could never have observed on a ten minute rhythm."""
    db.save_snapshot(player["id"], snapshot("2026-01-05T03:17:42.000Z",
                                            skills={"attack": (100, 40)}))
    assert db.query_one("SELECT origin FROM snapshots")["origin"] == "archive"


def test_compaction_never_thins_an_archive_reading(db, player):
    """A polled reading can be made again tomorrow; this one cannot."""
    for hour in (1, 7, 13, 19):
        db.save_snapshot(player["id"], snapshot(
            "2026-01-05T{:02d}:00:00.000Z".format(hour), bosses={"zulrah": hour}))
    as_polled(db)
    # one of them was Wise Old Man's own, not ours
    conn = db.connect()
    with conn:
        conn.execute("UPDATE snapshots SET origin='archive'"
                     " WHERE captured_at LIKE '2026-01-05T07%'")

    db.compact_snapshots(keep_days=30)
    kept = [r["captured_at"] for r in db.query(
        "SELECT captured_at FROM snapshots WHERE captured_at < '2026-02-01'"
        " ORDER BY captured_at")]
    assert kept == ["2026-01-05T07:00:00.000Z", "2026-01-05T19:00:00.000Z"], (
        "the day's last reading, and the one we could not have taken ourselves")


def test_an_archive_reading_still_says_what_it_said(db, player):
    """Keeping the reading and dropping its metrics would be worse than
    dropping both: it would carry an older value and look authoritative."""
    for hour, kills in ((1, 10), (7, 25), (13, 30), (19, 44)):
        db.save_snapshot(player["id"], snapshot(
            "2026-01-05T{:02d}:00:00.000Z".format(hour), bosses={"zulrah": kills}))
    as_polled(db)
    conn = db.connect()
    with conn:
        conn.execute("UPDATE snapshots SET origin='archive'"
                     " WHERE captured_at LIKE '2026-01-05T07%'")
    def reading():
        return {r["metric"]: r["value"] for r in
                db.state_at(player["id"], "2026-01-05T07:00:00.000Z", kind="boss")}
    before = reading()
    assert before["zulrah"] == 25, "what that moment said before we thinned"

    db.compact_snapshots(keep_days=30)
    assert reading() == before, "the surviving moment must read exactly as it did"


def test_older_readings_are_labelled_from_what_was_already_stored(db, player):
    """The column was added after the fact; the two timestamps it needs
    were already there, so the answer is exact rather than a guess."""
    db.save_snapshot(player["id"], snapshot("2026-01-05T03:00:00.000Z",
                                            skills={"attack": (100, 40)}))
    conn = db.connect()
    with conn:
        conn.execute("UPDATE snapshots SET origin=NULL")
    before_migration(db, LABEL_SNAPSHOT_ORIGINS)
    from wom.db import Database
    relabelled = Database(db.path)
    assert relabelled.query_one("SELECT origin FROM snapshots")["origin"] == "archive"


# -- the version the file records -----------------------------------------

def test_a_new_database_is_current_without_running_a_single_step(tmp_path,
                                                                 monkeypatch):
    """Built from today's schema, it cannot be an older shape.

    The steps all used to run on every open, each one reading PRAGMA
    table_info to discover it had nothing to do - a probe paid at every
    startup for ever, on a file that was seconds old.
    """
    from wom.db import Database
    from wom.store import migrations

    ran = []
    monkeypatch.setattr(migrations, "STEPS", tuple(
        (n, lambda conn, n=n: ran.append(n)) for n, _step in migrations.STEPS))

    database = Database(str(tmp_path / "new.db"))
    assert ran == [], "a new file needs none of them"
    assert migrations.version(database.connect()) == migrations.LATEST


def test_a_database_from_before_the_numbering_is_walked_through_all_of_them():
    """It says version zero because it has never said anything else, so every
    step is offered it - and every step finds its work already done, which is
    what the checks inside them are still there for."""
    import sqlite3

    from wom.store import migrations

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(migrations.SCHEMA)
    assert migrations.version(conn) == 0, "nothing has stamped it"

    assert migrations.apply(conn) == migrations.LATEST
    assert migrations.version(conn) == migrations.LATEST


def test_a_step_that_has_run_is_never_offered_again(tmp_path, monkeypatch):
    """Which is the point of writing the number down."""
    from wom.db import Database
    from wom.store import migrations

    path = str(tmp_path / "twice.db")
    Database(path)                                  # stamped current

    ran = []
    monkeypatch.setattr(migrations, "STEPS", tuple(
        (n, lambda conn, n=n: ran.append(n)) for n, _step in migrations.STEPS))
    Database(path)
    assert ran == []


def test_an_interrupted_migration_resumes_rather_than_restarting(tmp_path,
                                                                monkeypatch):
    """The version is stamped after each step, not after the last one."""
    import sqlite3

    from wom.store import migrations

    conn = sqlite3.connect(str(tmp_path / "half.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(migrations.SCHEMA)

    ran = []

    def explode(_conn):
        raise RuntimeError("the power went out")

    steps = [(n, lambda c, n=n: ran.append(n)) for n, _s in migrations.STEPS]
    stumbles = steps[2][0]
    steps[2] = (stumbles, explode)
    monkeypatch.setattr(migrations, "STEPS", tuple(steps))

    with pytest.raises(RuntimeError):
        migrations.apply(conn)
    assert ran == [steps[0][0], steps[1][0]]
    assert migrations.version(conn) == steps[1][0], "as far as it got"

    # And a second attempt picks up at the one that failed.
    steps[2] = (stumbles, lambda c: ran.append(stumbles))
    monkeypatch.setattr(migrations, "STEPS", tuple(steps))
    migrations.apply(conn)
    assert ran[2] == stumbles
    assert migrations.version(conn) == migrations.LATEST


def test_every_step_is_numbered_once_and_in_order():
    """Numbers are permanent: a renumbered step is one an older file either
    runs twice or never sees."""
    from wom.store import migrations

    numbers = [n for n, _step in migrations.STEPS]
    assert numbers == sorted(numbers)
    assert len(set(numbers)) == len(numbers)
    assert numbers[0] == 1 and numbers[-1] == migrations.LATEST
