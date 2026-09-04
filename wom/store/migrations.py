"""Bringing an older database file up to the shape in schema.py.

Each step below is numbered, and the number it has been brought to is kept in
the file itself, in SQLite's own `user_version`. That is the whole point of
this module. The migrations used to be a fixed list run on every open, each
one working out for itself whether it had anything to do by reading
PRAGMA table_info and comparing what it found: "have we done this already?"
answered by sniffing the shape of the result. It worked, but it meant every
step ever written was a probe paid on every startup, for ever, and a step
whose evidence was ambiguous had no way to say so.

A step is still written to be safe to run twice - the structural checks are
all still there - because databases that predate the numbering arrive here
saying version 0 while already being current, and the checks are what make
that harmless. Once a step has run, the number is written down and it is
never asked again.

The version is stamped after each step rather than at the end, so a migration
interrupted half way resumes rather than restarting.
"""

import logging

from ..periods import GROUP_PERIODS
from .schema import FRESH_SECONDS, SCHEMA

log = logging.getLogger(__name__)


def _add_group_summary_board(conn):
    """Give the round-ups a competition to belong to.

    Everything written before there were two was written about the
    leaderboard that existed, so it is Maxing. SQLite cannot add a column
    to a primary key, so the table is rebuilt rather than altered - it
    holds a few hundred rows and this runs once.
    """
    columns = {row["name"] for row in conn.execute(
        "PRAGMA table_info(group_summaries)")}
    if not columns or "board" in columns:
        return
    with conn:
        conn.execute("ALTER TABLE group_summaries RENAME TO group_summaries_old")
    conn.executescript(SCHEMA)
    with conn:
        conn.execute(
            "INSERT INTO group_summaries (board, period, window_key,"
            " period_start, period_end, label, text, digest_hash, model,"
            " input_tokens, output_tokens, generated_at, winner)"
            " SELECT 'maxing', period, window_key, period_start, period_end,"
            " label, text, digest_hash, model, input_tokens, output_tokens,"
            " generated_at, winner FROM group_summaries_old")
        conn.execute("DROP TABLE group_summaries_old")
    log.info("group round-ups now belong to a board")


def _add_event_happened_at(conn):
    """Separate when an event happened from when it reached us.

    Dink retries a delivery it could not make, so arrival time can be
    minutes past the moment - and the moment is what session attribution
    measures. Rows stored before the distinction existed are within a
    second of each other, so they take their arrival time.
    """
    columns = {row["name"] for row in conn.execute(
        "PRAGMA table_info(session_events)")}
    if not columns or "happened_at" in columns:
        return
    with conn:
        conn.execute("ALTER TABLE session_events ADD COLUMN happened_at TEXT")
        conn.execute("UPDATE session_events SET happened_at=received_at"
                     " WHERE happened_at IS NULL")
    log.info("session events gained a happened_at")


def _label_snapshot_origins(conn):
    """Fill in `origin` for readings stored before it was recorded.

    The two timestamps needed were already there, so this is exact rather
    than a guess - which is the only reason the column can be added after
    the fact at all.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(snapshots)")}
    if "origin" not in columns:
        with conn:
            conn.execute("ALTER TABLE snapshots ADD COLUMN origin TEXT")
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM snapshots WHERE origin IS NULL").fetchone()["n"]
    if not pending:
        return
    with conn:
        conn.execute(
            "UPDATE snapshots SET origin = CASE"
            "  WHEN (julianday(replace(fetched_at,'Z','')) -"
            "        julianday(replace(captured_at,'Z',''))) * 86400.0 <= ?"
            "  THEN 'poll' ELSE 'archive' END"
            " WHERE origin IS NULL", (FRESH_SECONDS,))
    log.info("labelled the origin of %d stored readings", pending)


def _widen_logins_to_sessions(conn):
    """Fold the login-only table into one that holds logouts as well.

    Dink reports both ends; the first cut of this only knew about the
    first. A table called `logins` holding logouts is the kind of drift
    that outlives whoever remembers it, so the table is renamed rather
    than given a column and a footnote.
    """
    names = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "logins" not in names:
        return
    with conn:
        conn.execute(
            "INSERT INTO session_events (username, player_id, kind,"
            " received_at, happened_at, world, total_exp, total_level,"
            " collections, payload)"
            " SELECT username, player_id, 'login', received_at, received_at,"
            " world, total_exp, total_level, collections, payload FROM logins")
        conn.execute("DROP TABLE logins")
    log.info("migrated the logins table into session_events")


def _drop_ungrouped_recaps(conn):
    """Remove group recaps for windows the leaderboard does not judge.

    The group recap became the Maxing Leaderboard's feed, which colours
    days and awards months and has no verdict for anything else. The
    weekly, quarterly and yearly ones that had already been written were
    describing windows nothing on the page could illustrate, so they go
    rather than sitting in the tree as the only entries with no result
    beside them.

    Each player's own notes are untouched: those are about one account's
    progress, and all five windows still say something there.
    """

    marks = ",".join("?" * len(GROUP_PERIODS))
    stale = conn.execute(
        "SELECT COUNT(*) AS n FROM group_summaries"
        " WHERE period NOT IN ({})".format(marks), GROUP_PERIODS).fetchone()
    if not (stale and stale["n"]):
        return
    with conn:
        conn.execute("DELETE FROM group_summaries WHERE period NOT IN ({})"
                     .format(marks), GROUP_PERIODS)
    log.info("dropped %d group recaps outside %s", stale["n"],
             "/".join(GROUP_PERIODS))


def _to_sparse_metrics(conn):
    """Rewrite a full-snapshot metrics table as changes only.

    The old shape wrote every metric of every reading, which for this data
    was 91% repetition, and carried two indexes larger than the table. The
    new one keeps a row only where a value moved.

    Done in one transaction against a copy, so an interrupted migration
    leaves the original in place rather than half a table.
    """
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(metrics)")]
    if not columns or "snapshot_id" not in columns:
        return                                  # already sparse, or brand new
    log.info("compacting the metrics table to changes only; this takes a moment")
    with conn:
        conn.execute("DROP TABLE IF EXISTS metrics_sparse")
        conn.execute("""
            CREATE TABLE metrics_sparse (
                player_id INTEGER NOT NULL, kind TEXT NOT NULL,
                metric TEXT NOT NULL, captured_at TEXT NOT NULL,
                value REAL, rank INTEGER, level INTEGER, efficiency REAL,
                PRIMARY KEY (player_id, kind, metric, captured_at)
            ) WITHOUT ROWID""")
        # A row survives only where it differs from the one before it for
        # the same metric. IS NOT compares NULLs as equal, which matters:
        # an unranked metric stays unranked without a row on every update.
        conn.execute("""
            INSERT INTO metrics_sparse
            SELECT player_id, kind, metric, captured_at, value, rank,
                   level, efficiency
            FROM (
                SELECT m.*,
                       LAG(m.value) OVER w AS pv,
                       LAG(m.level) OVER w AS pl, LAG(m.efficiency) OVER w AS pe,
                       ROW_NUMBER() OVER w AS rn
                FROM metrics m
                WINDOW w AS (PARTITION BY m.player_id, m.kind, m.metric
                             ORDER BY m.captured_at))
            WHERE rn = 1 OR value IS NOT pv
               OR level IS NOT pl OR efficiency IS NOT pe""")
        conn.execute("DROP TABLE metrics")
        conn.execute("ALTER TABLE metrics_sparse RENAME TO metrics")
        # Nothing has ever read the raw payload; only the newest per player
        # is kept, as a sample of exactly what the API hands back.
        conn.execute("""
            UPDATE snapshots SET payload='' WHERE id NOT IN (
                SELECT id FROM snapshots s WHERE captured_at = (
                    SELECT MAX(captured_at) FROM snapshots x
                    WHERE x.player_id = s.player_id))""")
    # VACUUM cannot run inside a transaction, and in WAL mode its result
    # lands in the log rather than the file: without the checkpoint the
    # database still measures its old size, with twenty megabytes of it
    # sitting in a .db-wal beside it.
    conn.execute("VACUUM")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    log.info("metrics table rewritten")

def _add_player_backfilled_at(conn):
    """A column saying whether this player's history has been imported."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(players)")}
    if "backfilled_at" in columns:
        return
    with conn:
        conn.execute("ALTER TABLE players ADD COLUMN backfilled_at TEXT")


def _add_run_roster(conn):
    """Record how many accounts a run was asked to cover.

    A run used to record only how many players it managed. Whether it looked
    at everyone was then answered against today's roster, so adding a seventh
    account made every past run fall short and blanked the history behind it.
    Old rows have no answer and say so with NULL.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
    if "roster" in columns:
        return
    with conn:
        conn.execute("ALTER TABLE runs ADD COLUMN roster INTEGER")


def _add_group_summary_winner(conn):
    """Name the winner in a column, not only in the prose.

    The round-ups started naming one so the calendar on /summaries can colour
    by it.
    """
    columns = {row["name"]
               for row in conn.execute("PRAGMA table_info(group_summaries)")}
    if not columns or "winner" in columns:
        return
    with conn:
        conn.execute("ALTER TABLE group_summaries ADD COLUMN winner TEXT")


def _summaries_by_window(conn):
    """Re-key the per-player notes by the window they cover.

    Summaries used to be one row per period, covering a rolling window with no
    start or end. There is no honest way to relabel those as calendar windows,
    and they are cheap to write again, so the old table is dropped rather than
    converted.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(summaries)")}
    if not columns or "window_key" in columns:
        return
    with conn:
        conn.execute("DROP TABLE summaries")
    conn.executescript(SCHEMA)


def _label_metric_origins(conn):
    """Let a metric row say for itself where its value came from.

    Until now the snapshot beside it carried that. Everything we worked out
    was written at a moment Wise Old Man had never read, so a `derived`
    snapshot was enough to find those rows again. Ramping a session across the
    readings it ran through breaks that: those moments are real readings,
    whose `poll` snapshots have to survive, so the mark moves onto the row it
    actually describes.

    Rows already on file all predate the ramp, so they take their snapshot's
    word - exact rather than a guess, which is the only reason this column can
    be added after the fact.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(metrics)")}
    if not columns or "origin" in columns:
        return
    with conn:
        conn.execute("ALTER TABLE metrics ADD COLUMN origin TEXT")
        conn.execute(
            "UPDATE metrics SET origin = (SELECT s.origin FROM snapshots s"
            "  WHERE s.player_id=metrics.player_id"
            "    AND s.captured_at=metrics.captured_at)")
        # `poll` and `archive` are both Wise Old Man reading the hiscores, and
        # a row with no snapshot at all is older than the distinction. Only
        # the two words meaning "we did not read this" are kept.
        conn.execute("UPDATE metrics SET origin=NULL"
                     " WHERE origin IS NOT NULL"
                     "   AND origin NOT IN ('derived','reported')")
    log.info("metric rows can now say where they came from")


# In the order they have to run, and numbered for ever. Append; never
# renumber, and never remove one - a database that has not seen a step still
# needs it, however old it is.
STEPS = (
    (1, _to_sparse_metrics),
    (2, _add_player_backfilled_at),
    (3, _add_run_roster),
    (4, _add_group_summary_winner),
    (5, _summaries_by_window),
    (6, _drop_ungrouped_recaps),
    (7, _widen_logins_to_sessions),
    (8, _label_snapshot_origins),
    (9, _add_event_happened_at),
    (10, _add_group_summary_board),
    (11, _label_metric_origins),
)

LATEST = max(number for number, _step in STEPS)


def version(conn):
    """Which step this file has been brought up to."""
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _stamp(conn, number):
    # PRAGMA takes no parameters, so the number is formatted in. It comes from
    # the tuple above and is an int, which is what makes that safe.
    conn.execute("PRAGMA user_version = {:d}".format(number))


def apply(conn, fresh=False):
    """Run whatever this database has not had, and record that it has.

    `fresh` says the file had no tables before schema.py built it, which makes
    it current by construction: it is stamped and no step runs. Every other
    database is asked what it has seen. One that predates the numbering says
    zero, so it walks the whole list - and every step finds its work already
    done, which is what the structural checks inside them are for.
    """
    at = version(conn)
    if fresh:
        _stamp(conn, LATEST)
        return LATEST
    if at >= LATEST:
        return at
    if at == 0:
        log.info("checking a database written before the migrations were numbered")
    for number, step in STEPS:
        if number <= at:
            continue
        step(conn)
        _stamp(conn, number)
    return LATEST
