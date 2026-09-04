"""SQLite storage for players, snapshots and the flattened metric history.

Snapshots are kept whole (as JSON) so nothing from the API is lost, and are
also flattened into `metrics` so charts and tables can query them with SQL.
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .util import parse_api_time

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id              INTEGER PRIMARY KEY,          -- Wise Old Man player id
    username        TEXT NOT NULL UNIQUE,         -- lowercase, the API's key
    display_name    TEXT NOT NULL,
    type            TEXT,
    build           TEXT,
    status          TEXT,
    country         TEXT,
    combat_level    INTEGER,
    exp             INTEGER,
    ehp             REAL,
    ehb             REAL,
    ttm             REAL,
    registered_at   TEXT,
    updated_at      TEXT,
    last_changed_at TEXT,
    last_fetched_at TEXT,
    backfilled_at   TEXT                          -- when history was imported
);

CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,                    -- snapshot createdAt, ISO UTC
    fetched_at  TEXT NOT NULL,
    origin      TEXT,                             -- see save_snapshot
    payload     TEXT NOT NULL,                    -- raw snapshot data as JSON
    UNIQUE (player_id, captured_at)
);

-- Only what changed. A reading repeats the previous one for 91 of every 100
-- metrics - a boss sitting at zero was being written again on every update,
-- forever - so a row is stored only when a value actually moves, and every
-- read carries the last one forward. See state_at().
--
-- WITHOUT ROWID with this key makes the table its own index, and the key is
-- ordered for the only question anyone asks of it: what was this metric worth
-- at or before some moment. That folds away both indexes the old shape needed.
CREATE TABLE IF NOT EXISTS metrics (
    player_id   INTEGER NOT NULL,
    kind        TEXT NOT NULL,                    -- skill | boss | activity | computed
    metric      TEXT NOT NULL,                    -- e.g. overall, zulrah, ehp
    captured_at TEXT NOT NULL,
    value       REAL,                             -- experience | kills | score | value
    rank        INTEGER,
    level       INTEGER,                          -- skills only
    efficiency  REAL,                             -- ehp for skills, ehb for bosses
    PRIMARY KEY (player_id, kind, metric, captured_at)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS achievements (
    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,                    -- e.g. "99 Attack"
    metric      TEXT,                             -- attack, zulrah, overall...
    measure     TEXT,                             -- experience | kills | levels | score
    threshold   REAL,
    achieved_at TEXT,                             -- ISO UTC, may be approximate
    accuracy    INTEGER,                          -- +/- milliseconds, -1 if unknown
    first_seen  TEXT NOT NULL,                    -- when this app first stored it
    PRIMARY KEY (player_id, name)
);

CREATE INDEX IF NOT EXISTS idx_achievements_when
    ON achievements (achieved_at DESC);

CREATE TABLE IF NOT EXISTS summaries (
    player_id     INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    period        TEXT NOT NULL,                  -- day | week | month
    window_key    TEXT NOT NULL,                  -- the window's start date
    period_start  TEXT NOT NULL,                  -- ISO UTC, inclusive
    period_end    TEXT NOT NULL,                  -- ISO UTC, exclusive
    label         TEXT NOT NULL,                  -- "Sunday 30 August 2026"
    text          TEXT NOT NULL,
    digest_hash   TEXT NOT NULL,                  -- skip regenerating unchanged data
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    generated_at  TEXT NOT NULL,
    PRIMARY KEY (player_id, period, window_key)
);

-- The group verdict for a window. Its own table because it belongs to no
-- single player, and a nullable half of a primary key is a trap.
CREATE TABLE IF NOT EXISTS group_summaries (
    period        TEXT NOT NULL,
    window_key    TEXT NOT NULL,
    winner        TEXT,                           -- username the round-up named
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    label         TEXT NOT NULL,
    text          TEXT NOT NULL,
    digest_hash   TEXT NOT NULL,
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    generated_at  TEXT NOT NULL,
    PRIMARY KEY (period, window_key)
);

-- Dink's metadata webhook, which reports both ends of a session as they
-- happen: a login six seconds in, carrying that account's own reading of its
-- experience, and a logout, which carries only the fact and the moment.
--
-- Between them this is the only measurement of a session we can get. Wise Old
-- Man infers an ending from the hiscores moving and cannot see a beginning at
-- all, so a three hour session arrives as a single jump and we attribute it to
-- the ten minutes we happened to notice in.
--
-- Keyed by username rather than player id: an event can arrive before the
-- account is tracked, and it stays interesting after one is pruned. player_id
-- is a convenience, filled in when we know it.
CREATE TABLE IF NOT EXISTS session_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL,                    -- lowercase, the token's owner
    player_id   INTEGER,                          -- NULL until the account is known
    kind        TEXT NOT NULL,                    -- login | logout
    received_at TEXT NOT NULL,                    -- when the POST reached us, ISO UTC
    happened_at TEXT NOT NULL,                    -- when the client says it happened
    world       INTEGER,                          -- login only
    total_exp   REAL,                             -- login only: totalExperience, live
    total_level INTEGER,                          -- login only
    collections INTEGER,                          -- login only: collectionLog.completed
    payload     TEXT NOT NULL                     -- what we chose to keep of the body
);

CREATE INDEX IF NOT EXISTS idx_session_events_who
    ON session_events (username, happened_at DESC);

-- What Dink reports while somebody is playing, rather than at the ends of a
-- session: a collection log slot filled, a level gained, a boss count passed.
-- Opt-in per player, so this is sparse and always will be.
--
-- Kept whole as well as flattened, because the interesting part is the detail
-- - which item, from which drop, at which rank - and none of that fits the
-- metrics table. A collection log feed wants the item; the charts want the
-- count. Both are here.
CREATE TABLE IF NOT EXISTS game_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL,                    -- lowercase, the token's owner
    player_id   INTEGER,
    kind        TEXT NOT NULL,                    -- collection | level | kill_count
    happened_at TEXT NOT NULL,                    -- when the client says it happened
    received_at TEXT NOT NULL,
    subject     TEXT,                             -- the item, skill or boss
    quantity    REAL,                             -- slots filled, new level, kills
    payload     TEXT NOT NULL,
    UNIQUE (username, kind, subject, happened_at)
);

CREATE INDEX IF NOT EXISTS idx_game_events_when
    ON game_events (happened_at DESC);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok_count    INTEGER DEFAULT 0,
    fail_count  INTEGER DEFAULT 0,
    roster      INTEGER,                          -- players tracked at the time
    trigger     TEXT,                             -- scheduled | manual | startup
    notes       TEXT
);
"""

_VALUE_KEYS = ("experience", "kills", "score", "value")

# A reading Wise Old Man made this recently was made because we asked. Beyond
# it, the reading already existed and we are only now collecting it - which is
# a moment we could never have observed ourselves. See save_snapshot.
FRESH_SECONDS = 60

# How long two identical reports from one account are treated as one event.
# See record_session_event.
SESSION_DEDUPE_SECONDS = 300


class Database:
    """Small wrapper around a SQLite file; safe to use from several threads."""

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._local = threading.local()
        with self.connect() as conn:
            conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """Bring an older database file up to the current schema."""
        conn = self.connect()
        self._to_sparse_metrics(conn)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(players)")}
        with conn:
            if "backfilled_at" not in columns:
                conn.execute("ALTER TABLE players ADD COLUMN backfilled_at TEXT")

        # A run used to record only how many players it managed. Whether it
        # looked at everyone was then answered against today's roster, so
        # adding a seventh account made every past run fall short and blanked
        # the history behind it. Old rows have no answer and say so with NULL.
        run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        if "roster" not in run_columns:
            with conn:
                conn.execute("ALTER TABLE runs ADD COLUMN roster INTEGER")

        # The round-ups started naming a winner in a column rather than only
        # in their prose, so the calendar on /summaries can colour by it.
        group_columns = {row["name"]
                         for row in conn.execute("PRAGMA table_info(group_summaries)")}
        if group_columns and "winner" not in group_columns:
            with conn:
                conn.execute("ALTER TABLE group_summaries ADD COLUMN winner TEXT")

        # Summaries used to be one row per period, covering a rolling window
        # with no start or end. There is no honest way to relabel those as
        # calendar windows, and they are cheap to write again, so the old
        # table is dropped rather than converted.
        summary_columns = {row["name"]
                           for row in conn.execute("PRAGMA table_info(summaries)")}
        if summary_columns and "window_key" not in summary_columns:
            with conn:
                conn.execute("DROP TABLE summaries")
            conn.executescript(SCHEMA)

        self._drop_ungrouped_recaps(conn)
        self._widen_logins_to_sessions(conn)
        self._label_snapshot_origins(conn)
        self._add_event_happened_at(conn)

    def _add_event_happened_at(self, conn):
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

    def _label_snapshot_origins(self, conn):
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

    def _widen_logins_to_sessions(self, conn):
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

    def _drop_ungrouped_recaps(self, conn):
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
        from .periods import GROUP_PERIODS

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

    def _to_sparse_metrics(self, conn):
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
                SELECT player_id, kind, metric, captured_at, value, rank, level, efficiency
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

    def connect(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def query(self, sql, params=()):
        return self.connect().execute(sql, params).fetchall()

    def query_one(self, sql, params=()):
        return self.connect().execute(sql, params).fetchone()

    # -- writes -----------------------------------------------------------

    def save_player_details(self, details):
        """Store a PlayerDetails payload and its latest snapshot. Returns player id."""
        conn = self.connect()
        now = _utcnow()
        pid = details["id"]
        with conn:
            conn.execute(
                """
                INSERT INTO players (id, username, display_name, type, build, status,
                                     country, combat_level, exp, ehp, ehb, ttm,
                                     registered_at, updated_at, last_changed_at, last_fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    username=excluded.username,
                    display_name=excluded.display_name,
                    type=excluded.type,
                    build=excluded.build,
                    status=excluded.status,
                    country=excluded.country,
                    combat_level=excluded.combat_level,
                    exp=excluded.exp,
                    ehp=excluded.ehp,
                    ehb=excluded.ehb,
                    ttm=excluded.ttm,
                    registered_at=excluded.registered_at,
                    updated_at=excluded.updated_at,
                    last_changed_at=excluded.last_changed_at,
                    last_fetched_at=excluded.last_fetched_at
                """,
                (
                    pid,
                    (details.get("username") or details.get("displayName", "")).lower(),
                    details.get("displayName") or details.get("username", ""),
                    details.get("type"), details.get("build"), details.get("status"),
                    details.get("country"), details.get("combatLevel"), details.get("exp"),
                    details.get("ehp"), details.get("ehb"), details.get("ttm"),
                    details.get("registeredAt"), details.get("updatedAt"),
                    details.get("lastChangedAt"), now,
                ),
            )

        snapshot = details.get("latestSnapshot")
        if snapshot:
            self.save_snapshot(pid, snapshot)
        return pid

    def save_snapshot(self, player_id, snapshot):
        """Insert one snapshot and its flattened metrics; ignores duplicates.

        Records where the reading came from, which is not something that can
        be worked out later. Our update pass asks Wise Old Man to read the
        hiscores, so a reading stamped a moment before we stored it is one we
        caused - `poll`. A reading stamped well before that already existed
        when we asked: Wise Old Man made it for somebody else, most often a
        player's own client pushing on logout, and it marks a moment we could
        never have observed on our ten minute rhythm - `archive`.

        Compaction keeps the second kind and thins the first, because one is
        reproducible by asking again tomorrow and the other is gone for good.
        """
        captured_at = snapshot.get("createdAt") or _utcnow()
        data = snapshot.get("data") or {}
        fetched_at = _utcnow()
        conn = self.connect()
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO snapshots (player_id, captured_at, fetched_at,"
                " origin, payload) VALUES (?,?,?,?,?)",
                (player_id, captured_at, fetched_at,
                 _origin(captured_at, fetched_at), json.dumps(data)),
            )
            if cur.rowcount == 0:
                return None  # already stored
            snapshot_id = cur.lastrowid
            # One payload per player: a sample of exactly what the API returns,
            # for the day a field we do not flatten turns out to matter. Every
            # payload was six megabytes of JSON nothing has ever read.
            conn.execute(
                "UPDATE snapshots SET payload='' WHERE player_id=? AND id<>?",
                (player_id, snapshot_id))
            # Only what moved, and rank moving is not the player moving: a
            # hiscore position drifts because strangers played, and 83% of
            # every row ever written was that drift. Rank is still stored on
            # the rows that are written, so it reads as the rank they held
            # when the metric last actually changed.
            before = self._state_before(conn, player_id, captured_at)
            changed = [row for row in _flatten(player_id, captured_at, data)
                       if before.get((row[1], row[2])) != (row[4], row[6], row[7])]
            conn.executemany(
                "INSERT OR REPLACE INTO metrics (player_id, kind, metric,"
                " captured_at, value, rank, level, efficiency)"
                " VALUES (?,?,?,?,?,?,?,?)", changed)
        return snapshot_id

    @staticmethod
    def _state_before(conn, player_id, when):
        """{(kind, metric): (value, rank, level, efficiency)} at or before `when`.

        A snapshot can arrive out of order - Wise Old Man's history is imported
        oldest first, and a backfill can land beside readings already stored -
        so this asks what was true just before this reading rather than
        assuming the newest row is the one to compare against.
        """
        rows = conn.execute(
            "SELECT kind, metric, value, level, efficiency FROM metrics m"
            " WHERE player_id=? AND captured_at<? AND captured_at = ("
            "   SELECT MAX(captured_at) FROM metrics x WHERE x.player_id=m.player_id"
            "     AND x.kind=m.kind AND x.metric=m.metric AND x.captured_at<?)",
            (player_id, when, when)).fetchall()
        return {(r["kind"], r["metric"]):
                (r["value"], r["level"], r["efficiency"]) for r in rows}

    def save_snapshots(self, player_id, snapshots):
        """Store many snapshots, skipping any already held. Returns how many were new."""
        return sum(1 for s in snapshots if self.save_snapshot(player_id, s) is not None)

    def needs_backfill(self, player_id):
        """True until this player's history has been imported once."""
        row = self.query_one("SELECT backfilled_at FROM players WHERE id=?", (player_id,))
        return row is not None and not row["backfilled_at"]

    def mark_backfilled(self, player_id, when=None):
        conn = self.connect()
        with conn:
            conn.execute("UPDATE players SET backfilled_at=? WHERE id=?",
                         (when or _utcnow(), player_id))

    def overall_at(self, player_id, when=None):
        """Total level and total experience as at a moment, or now.

        The same query lived in five places - twice in the digest builders,
        once in the landmark line, once in the Players table and once in the
        standings - and three of the five had no time bound at all, which is
        how a digest about August came to open with "Total level now".
        """
        sql = ("SELECT level, value FROM metrics WHERE player_id=?"
               " AND kind='skill' AND metric='overall'")
        params = [player_id]
        if when:
            sql += " AND captured_at<=?"
            params.append(when)
        return self.query_one(sql + " ORDER BY captured_at DESC LIMIT 1", params)

    def last_change(self, player_id):
        """When this player's numbers last actually moved.

        A row is only written when something changed, so the newest reading a
        player holds is the last time they played. `players.updated_at` is a
        different question - it is when Wise Old Man last refreshed them,
        which we cause every ten minutes, so it reads "9m ago" forever
        whether or not the account has been logged into all week.
        """
        row = self.query_one(
            "SELECT MAX(captured_at) AS at FROM metrics WHERE player_id=?",
            (player_id,))
        return row["at"] if row else None

    def snapshot_count(self, player_id):
        row = self.query_one("SELECT COUNT(*) AS n FROM snapshots WHERE player_id=?",
                             (player_id,))
        return row["n"] if row else 0

    def record_derived_state(self, player_id, when, rows, origin="derived"):
        """Write an interpolated reading at `when`. Returns rows written.

        `rows` is [(kind, metric, value)] - what the account had earned by
        that moment, which is not what the hiscores said. The hiscores do not
        move until logout, so during a session they under-report, and this is
        the correction: experience credited to the time it was earned rather
        than to the minute we found out about it.

        Written as metric rows at a moment that usually already has a
        snapshot. That is deliberate. Nothing is overwritten, because a
        session leaves no metric rows behind it at all - every metric was
        unchanged as far as the hiscores were concerned - so these fill a gap
        rather than contradict a reading.

        A snapshot is created only if the moment has none, and marked so
        compaction keeps it and nothing mistakes it for something Wise Old Man
        said: `derived` when we worked the value out ourselves, `reported`
        when a plugin told us outright. The difference matters because
        recomputing attribution clears the first and must not touch the
        second - a reported value is evidence, not arithmetic.
        """
        conn = self.connect()
        written = 0
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO snapshots (player_id, captured_at,"
                " fetched_at, origin, payload) VALUES (?,?,?,?,'{}')",
                (player_id, when, _utcnow(), origin))
            for kind, metric, value in rows:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO metrics (player_id, kind, metric,"
                    " captured_at, value) VALUES (?,?,?,?,?)",
                    (player_id, kind, metric, when, value))
                written += cur.rowcount
        return written

    def clear_derived_state(self, player_id, since=None):
        """Remove interpolated readings, so they can be worked out again.

        The rule that produces them will change, and a correction nobody can
        withdraw is worse than no correction.
        """
        conn = self.connect()
        where = "player_id=?" + (" AND captured_at>=?" if since else "")
        params = [player_id] + ([since] if since else [])
        with conn:
            conn.execute(
                "DELETE FROM metrics WHERE " + where + " AND captured_at IN ("
                "  SELECT captured_at FROM snapshots WHERE " + where +
                "    AND origin='derived')", params + params)
            cur = conn.execute(
                "DELETE FROM snapshots WHERE " + where + " AND origin='derived'",
                params)
        return cur.rowcount

    def record_game_event(self, username, kind, happened_at, payload,
                          subject=None, quantity=None, when=None):
        """Store one thing that happened mid-session. Returns its id, or None.

        Keyed so the same event cannot land twice however many times the
        plugin retries it: one account, one kind, one subject, one moment.
        """
        row = self.player_by_username(username)
        conn = self.connect()
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO game_events (username, player_id, kind,"
                " happened_at, received_at, subject, quantity, payload)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (username, row["id"] if row is not None else None, kind,
                 happened_at, when or _utcnow(), subject, quantity,
                 json.dumps(payload)))
        return cur.lastrowid if cur.rowcount else None

    def game_events(self, username=None, kind=None, since=None, limit=200):
        """What players reported while playing, newest first."""
        sql = "SELECT * FROM game_events"
        clauses, params = [], []
        for column, value in (("username", username), ("kind", kind)):
            if value:
                clauses.append(column + "=?")
                params.append(value)
        if since:
            clauses.append("happened_at>=?")
            params.append(since)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.query(sql + " ORDER BY happened_at DESC LIMIT ?",
                          params + [limit])

    def game_event_count(self, username=None):
        sql = "SELECT COUNT(*) AS n FROM game_events"
        params = ()
        if username:
            sql += " WHERE username=?"
            params = (username,)
        row = self.query_one(sql, params)
        return row["n"] if row is not None else 0

    def knows_metric(self, kind, metric):
        """True if we already track this metric, so a name can be checked."""
        return self.query_one(
            "SELECT 1 AS ok FROM metrics WHERE kind=? AND metric=? LIMIT 1",
            (kind, metric)) is not None

    def record_session_event(self, username, kind, reading, payload, when=None,
                             happened_at=None,
                             dedupe_seconds=SESSION_DEDUPE_SECONDS):
        """Store one login or logout. Returns its row id, or None if a repeat.

        Dink retries a webhook it could not deliver, so the same event can
        arrive more than once with a different timestamp each time. There is
        no id in the payload to key on, so a repeat is recognised the only way
        left: the same account reporting the same thing again within a few
        minutes - the same total experience for a login, and for a logout,
        which carries no numbers at all, simply another logout.

        That also swallows a genuine second event in the same window, which
        costs one session boundary. It is the right way round: a phantom
        session would be attributed real gains.
        """
        stamp = when or _utcnow()
        happened = happened_at or stamp
        conn = self.connect()
        exp = reading.get("total_exp")
        cutoff = _seconds_before(stamp, dedupe_seconds)
        if exp is None:
            sql = ("SELECT id FROM session_events WHERE username=? AND kind=?"
                   " AND total_exp IS NULL AND received_at>=? LIMIT 1")
            params = (username, kind, cutoff)
        else:
            sql = ("SELECT id FROM session_events WHERE username=? AND kind=?"
                   " AND total_exp=? AND received_at>=? LIMIT 1")
            params = (username, kind, exp, cutoff)
        if conn.execute(sql, params).fetchone() is not None:
            return None
        row = self.player_by_username(username)
        with conn:
            cur = conn.execute(
                "INSERT INTO session_events (username, player_id, kind,"
                " received_at, happened_at, world, total_exp, total_level,"
                " collections, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (username, row["id"] if row is not None else None, kind, stamp,
                 happened, reading.get("world"), exp, reading.get("total_level"),
                 reading.get("collections"), json.dumps(payload)))
        return cur.lastrowid

    def session_events(self, username=None, kind=None, since=None, until=None,
                       limit=200):
        """Recorded logins and logouts, newest first."""
        sql = "SELECT * FROM session_events"
        clauses, params = [], []
        if username:
            clauses.append("username=?")
            params.append(username)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if since:
            clauses.append("happened_at>=?")
            params.append(since)
        if until:
            clauses.append("happened_at<=?")
            params.append(until)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.query(sql + " ORDER BY happened_at DESC LIMIT ?",
                          params + [limit])

    def last_session_event(self, username):
        return self.query_one(
            "SELECT * FROM session_events WHERE username=?"
            " ORDER BY happened_at DESC LIMIT 1", (username,))

    def session_event_count(self, username):
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM session_events WHERE username=?", (username,))
        return row["n"] if row is not None else 0

    def save_achievements(self, player_id, achievements):
        """Store a player's milestones. Returns how many were new to us."""
        conn = self.connect()
        now = _utcnow()
        added = 0
        with conn:
            for entry in achievements or []:
                name = entry.get("name")
                if not name:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO achievements (player_id, name, metric,"
                    " measure, threshold, achieved_at, accuracy, first_seen)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (player_id, name, entry.get("metric"), entry.get("measure"),
                     entry.get("threshold"), entry.get("createdAt"),
                     entry.get("accuracy"), now),
                )
                added += cur.rowcount
        return added

    def achievements(self, player_ids=None, since=None, until=None, limit=500):
        """Milestones for the given players, newest first."""
        sql = ("SELECT a.*, p.display_name, p.username FROM achievements a"
               " JOIN players p ON p.id = a.player_id WHERE 1=1")
        params = []
        if player_ids is not None:
            if not player_ids:
                return []
            sql += " AND a.player_id IN ({})".format(",".join("?" * len(player_ids)))
            params.extend(player_ids)
        if since:
            sql += " AND a.achieved_at >= ?"
            params.append(since)
        if until:
            sql += " AND a.achieved_at < ?"
            params.append(until)
        sql += " ORDER BY a.achieved_at DESC, a.name LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    def save_summary(self, player_id, window, text, digest_hash, usage=None):
        usage = usage or {}
        conn = self.connect()
        with conn:
            conn.execute(
                "INSERT INTO summaries (player_id, period, window_key, period_start,"
                " period_end, label, text, digest_hash, model, input_tokens,"
                " output_tokens, generated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(player_id, period, window_key) DO UPDATE SET"
                "   period_start=excluded.period_start, period_end=excluded.period_end,"
                "   label=excluded.label, text=excluded.text,"
                "   digest_hash=excluded.digest_hash, model=excluded.model,"
                "   input_tokens=excluded.input_tokens,"
                "   output_tokens=excluded.output_tokens,"
                "   generated_at=excluded.generated_at",
                (player_id, window.period, window.key, window.start_iso(),
                 window.end_iso(), window.label, text, digest_hash,
                 usage.get("model"), usage.get("input_tokens"),
                 usage.get("output_tokens"), _utcnow()))

    def summary(self, player_id, period, window_key=None):
        """One stored summary - a specific window, or the most recent."""
        if window_key:
            return self.query_one(
                "SELECT * FROM summaries WHERE player_id=? AND period=? AND window_key=?",
                (player_id, period, window_key))
        return self.query_one(
            "SELECT * FROM summaries WHERE player_id=? AND period=?"
            " ORDER BY window_key DESC LIMIT 1", (player_id, period))

    def summaries(self, player_id=None, period=None, limit=500):
        """Stored summaries, newest window first, with the player's name."""
        sql = ("SELECT s.*, p.display_name, p.username FROM summaries s"
               " JOIN players p ON p.id = s.player_id WHERE 1=1")
        params = []
        if player_id is not None:
            sql += " AND s.player_id=?"
            params.append(player_id)
        if period:
            sql += " AND s.period=?"
            params.append(period)
        sql += " ORDER BY s.window_key DESC, p.display_name LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    def save_group_summary(self, window, text, digest_hash, usage=None,
                           winner=None):
        usage = usage or {}
        conn = self.connect()
        with conn:
            conn.execute(
                "INSERT INTO group_summaries (period, window_key, period_start,"
                " period_end, label, text, digest_hash, model, input_tokens,"
                " output_tokens, generated_at, winner)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(period, window_key) DO UPDATE SET"
                "   period_start=excluded.period_start, period_end=excluded.period_end,"
                "   label=excluded.label, text=excluded.text,"
                "   digest_hash=excluded.digest_hash, model=excluded.model,"
                "   input_tokens=excluded.input_tokens,"
                "   output_tokens=excluded.output_tokens,"
                "   generated_at=excluded.generated_at,"
                "   winner=excluded.winner",
                (window.period, window.key, window.start_iso(), window.end_iso(),
                 window.label, text, digest_hash, usage.get("model"),
                 usage.get("input_tokens"), usage.get("output_tokens"),
                 _utcnow(), winner))

    def group_summary(self, period, window_key):
        return self.query_one(
            "SELECT * FROM group_summaries WHERE period=? AND window_key=?",
            (period, window_key))

    def group_summaries(self, period=None, limit=500):
        sql = "SELECT * FROM group_summaries WHERE 1=1"
        params = []
        if period:
            sql += " AND period=?"
            params.append(period)
        sql += " ORDER BY window_key DESC LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    def start_run(self, trigger, roster=None):
        """Open a run. `roster` is how many players it set out to update."""
        conn = self.connect()
        with conn:
            cur = conn.execute(
                "INSERT INTO runs (started_at, trigger, roster) VALUES (?,?,?)",
                (_utcnow(), trigger, roster))
        return cur.lastrowid

    def finish_run(self, run_id, ok_count, fail_count, notes=""):
        conn = self.connect()
        with conn:
            conn.execute(
                "UPDATE runs SET finished_at=?, ok_count=?, fail_count=?, notes=? WHERE id=?",
                (_utcnow(), ok_count, fail_count, notes, run_id),
            )

    def compaction_preview(self, keep_days=30):
        """How many snapshots a compaction would drop, without touching anything."""
        cutoff = _days_ago(keep_days)
        total = self.query_one("SELECT COUNT(*) AS n FROM snapshots")["n"]
        doomed = self.query_one(
            "SELECT COUNT(*) AS n FROM snapshots WHERE captured_at < ?"
            " AND COALESCE(origin,'poll') = 'poll'"
            " AND id NOT IN (SELECT id FROM ("
            "     SELECT id, MAX(captured_at) FROM snapshots WHERE captured_at < ?"
            "     GROUP BY player_id, substr(captured_at, 1, 10)))",
            (cutoff, cutoff))["n"]
        return {"total": total, "removable": doomed, "cutoff": cutoff,
                "keep_days": keep_days}

    def compact_snapshots(self, keep_days=30):
        """Thin old history to one snapshot per player per day.

        Four-plus readings a day is the right resolution for recent gains, and
        far more than a month-wide chart can draw. Everything inside the recent
        window is left alone; beyond it each day's last snapshot survives - and
        so does every reading marked `archive`, whatever day it falls on.

        That exception is the point of the origin column. A reading we made by
        polling can be made again by polling tomorrow, so thinning it costs a
        detail. An archive reading is a moment Wise Old Man recorded without
        us - a player's client pushing on logout, most often - and it is the
        only evidence of when a session ended. Thin it and the timestamp is
        gone for good. They are also rare enough to be nearly free: 287 of
        2,470 readings on the live database, and they carry 280 of the 425
        experience changes in it.
        Each day's *last* reading is the one kept - matching what a daily
        chart point shows. It cannot be picked by highest id: history is
        imported newest-first, so within an imported day the largest id is the
        oldest snapshot.

        Metrics are thinned with them, to the last change of each metric on
        each day. That has to happen together: a change deleted while the
        reading after it survives would leave the reading carrying an older
        value, which is worse than losing the detail. Keeping each day's last
        change and each day's last reading is exact at every surviving moment.

        Returns the preview dict with the actual count removed.
        """
        summary = self.compaction_preview(keep_days)
        cutoff = summary["cutoff"]
        conn = self.connect()
        with conn:
            cur = conn.execute(
                "DELETE FROM snapshots WHERE captured_at < ?"
                " AND COALESCE(origin,'poll') = 'poll'"
                " AND id NOT IN (SELECT id FROM ("
                "     SELECT id, MAX(captured_at) FROM snapshots WHERE captured_at < ?"
                "     GROUP BY player_id, substr(captured_at, 1, 10)))",
                (cutoff, cutoff))
            summary["removed"] = cur.rowcount
            conn.execute(
                "DELETE FROM metrics WHERE captured_at < ?"
                " AND captured_at NOT IN ("
                "   SELECT MAX(x.captured_at) FROM metrics x"
                "    WHERE x.player_id=metrics.player_id AND x.kind=metrics.kind"
                "      AND x.metric=metrics.metric AND x.captured_at < ?"
                "    GROUP BY substr(x.captured_at, 1, 10))"
                " AND NOT EXISTS (SELECT 1 FROM snapshots s"
                "   WHERE s.player_id=metrics.player_id"
                "     AND s.captured_at=metrics.captured_at)",
                (cutoff, cutoff))
        # VACUUM cannot run inside a transaction, and in WAL mode its result
        # has to be checkpointed or the file never actually shrinks.
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return summary

    def prune_players(self, keep_usernames):
        """Drop players no longer on the tracked list, and everything they own.

        Snapshots and achievements cascade from `players`. Metrics do not:
        they carry no foreign key any more, because the key that made them
        cheap is the one they are read by. So they are deleted by hand, and
        before the players row goes - after it, there is nothing to name them.
        Returns how many players went.
        """
        keep = [n.lower() for n in keep_usernames]
        # `x NOT IN (NULL)` is NULL, not true, so an empty keep list has to be
        # spelled out or clearing the username list would prune nothing.
        if keep:
            where = "username NOT IN ({})".format(",".join("?" * len(keep)))
        else:
            where = "1=1"
        conn = self.connect()
        with conn:
            conn.execute(
                "DELETE FROM metrics WHERE player_id IN ("
                "  SELECT id FROM players WHERE " + where + ")", keep)
            removed = conn.execute(
                "DELETE FROM players WHERE " + where, keep).rowcount
            # Group round-ups belong to no player, so nothing cascades them.
            # They stay meaningful while anyone is still tracked; once the
            # roster is empty they describe nobody.
            if not conn.execute("SELECT 1 FROM players LIMIT 1").fetchone():
                conn.execute("DELETE FROM group_summaries")
        return max(0, removed)

    # -- reads used by the UI ---------------------------------------------

    def players(self):
        return self.query("SELECT * FROM players ORDER BY display_name COLLATE NOCASE")

    def player_by_username(self, username):
        return self.query_one("SELECT * FROM players WHERE username=?", (username.lower(),))

    def observations(self, player_id, since=None, until=None):
        """When this account was read, oldest first.

        A snapshot row is the record that somebody looked, whether or not
        anything had changed. That is a different fact from the metrics beside
        it and the only one that can answer "were we watching".
        """
        sql = "SELECT captured_at FROM snapshots WHERE player_id=?"
        params = [player_id]
        if since:
            sql += " AND captured_at>=?"
            params.append(since)
        if until:
            sql += " AND captured_at<?"
            params.append(until)
        return [row["captured_at"]
                for row in self.query(sql + " ORDER BY captured_at", params)]

    def metric_history(self, player_id, metric, kind="skill", limit=None, since=None,
                       bucket=None, until=None):
        """Time series of one metric for one player, oldest first.

        One point per reading, not per change. Only changes are stored, but a
        reading where nothing moved is what tells a chart the line was flat
        rather than unmeasured - drop those and a quiet fortnight looks like a
        gap in the data, which is what the dashed stretches are meant to mean.

        `bucket="day"` returns the last reading of each UTC day. Updates arrive
        every ten minutes and often more, which is more detail than a
        month-wide axis can render; one end-of-day point per day plots the same
        curve from a fraction of the rows.

        With `since`, the reading just before the window opens it, so a line
        drawn over that window starts at its left edge rather than wherever
        the first reading inside it happens to fall.
        """
        changes = self.query(
            "SELECT captured_at, value, rank, level, efficiency FROM metrics"
            " WHERE player_id=? AND metric=? AND kind=?"
            + (" AND captured_at<?" if until else "") + " ORDER BY captured_at",
            [player_id, metric, kind] + ([until] if until else []))
        stamps = self.observations(player_id, since, until)
        if since:
            earlier = self.query_one(
                "SELECT captured_at FROM snapshots WHERE player_id=? AND captured_at<?"
                " ORDER BY captured_at DESC LIMIT 1", (player_id, since))
            if earlier is not None:
                stamps.insert(0, earlier["captured_at"])

        rows = []
        at = 0
        held = None
        for stamp in stamps:
            while at < len(changes) and changes[at]["captured_at"] <= stamp:
                held = changes[at]
                at += 1
            if held is None:
                continue          # the metric was not on file this early
            rows.append({"captured_at": stamp, "value": held["value"],
                         "rank": held["rank"], "level": held["level"],
                         "efficiency": held["efficiency"]})
        if bucket == "day":
            by_day = {}
            for row in rows:
                by_day[row["captured_at"][:10]] = row
            rows = [by_day[day] for day in sorted(by_day)]
        if limit and len(rows) > limit:
            rows = rows[-limit:]
        return rows

    def state_at(self, player_id, when=None, kind=None):
        """Where an account stood at a moment: one row per metric.

        Rows are stored only where a value moved, so the answer is the newest
        row at or before `when` for each metric rather than the rows sharing
        one timestamp. `when` of None means now.
        """
        edge = when or "9999"
        sql = ("SELECT kind, metric, captured_at, value, rank, level, efficiency"
               " FROM metrics m WHERE player_id=? AND captured_at<=?")
        params = [player_id, edge]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        sql += (" AND captured_at = (SELECT MAX(captured_at) FROM metrics x"
                "   WHERE x.player_id=m.player_id AND x.kind=m.kind"
                "     AND x.metric=m.metric AND x.captured_at<=?)")
        params.append(edge)
        return self.query(sql + " ORDER BY metric", params)

    def latest_snapshot_metrics(self, player_id, kind=None):
        return self.state_at(player_id, None, kind)

    def snapshot_metrics(self, snapshot, kind=None):
        """Where an account stood at one named reading.

        Takes the snapshot row rather than its id: with only changes stored, a
        reading is a moment in time, not a set of rows carrying its number.
        """
        if snapshot is None:
            return []
        when = snapshot["captured_at"] if not isinstance(snapshot, str) else snapshot
        return self.state_at(snapshot["player_id"] if not isinstance(snapshot, str)
                             else None, when, kind)

    def export_rows(self, player_ids, kinds=None, since=None, until=None,
                    batch=2000):
        """Every stored reading in a range, oldest first, one row per metric.

        Storage keeps only what changed, but the file has always meant "one
        row per metric per reading" and a spreadsheet asking what someone had
        on a given date should not have to carry values forward itself. So the
        readings are rebuilt here: a running state per player, emitted whole at
        each moment that player was read.

        One player at a time, so what is held in memory is one account's
        metrics rather than the whole export.
        """
        if not player_ids:
            return
        wanted = list(kinds) if kinds else None
        for player_id in player_ids:
            who = self.query_one(
                "SELECT display_name, username FROM players WHERE id=?", (player_id,))
            if who is None:
                continue
            sql = ("SELECT captured_at, kind, metric, value, level, rank FROM metrics"
                   " WHERE player_id=?")
            params = [player_id]
            if wanted:
                sql += " AND kind IN ({})".format(",".join("?" * len(wanted)))
                params.extend(wanted)
            if until:
                sql += " AND captured_at<?"
                params.append(until)
            changes = self.query(sql + " ORDER BY captured_at", params)

            held = {}
            at = 0
            for stamp in self.observations(player_id, since, until):
                while at < len(changes) and changes[at]["captured_at"] <= stamp:
                    row = changes[at]
                    held[(row["kind"], row["metric"])] = row
                    at += 1
                for (kind, metric), row in sorted(held.items()):
                    yield {"captured_at": stamp,
                           "display_name": who["display_name"],
                           "username": who["username"], "kind": kind,
                           "metric": metric, "value": row["value"],
                           "level": row["level"], "rank": row["rank"]}

    # -- gains over a window ----------------------------------------------

    def baseline_snapshot(self, player_id, since, until=None):
        """The snapshot a window's gains are measured from.

        Normally the last snapshot before the window opened, which brackets it
        exactly. Wise Old Man's history is sparse for players it has not been
        watching long, though, and that snapshot can predate the window by
        years - measuring from it would report four years of kills as "this
        month". So take whichever bracketing snapshot sits closer to the window
        edge: an earlier one overstates by what happened before the window, a
        later one understates by what happened at the start of it, and the
        nearer of the two is wrong by less.
        """
        before = self.query_one(
            "SELECT id, player_id, captured_at FROM snapshots WHERE player_id=? AND captured_at<=?"
            " ORDER BY captured_at DESC LIMIT 1", (player_id, since))
        after = self.query_one(
            "SELECT id, player_id, captured_at FROM snapshots WHERE player_id=? AND captured_at>?"
            + (" AND captured_at<?" if until else "") +
            " ORDER BY captured_at ASC LIMIT 1",
            (player_id, since, until) if until else (player_id, since))
        if before is None:
            return after
        if after is None:
            return before
        edge = parse_api_time(since)
        gap_before = abs((edge - parse_api_time(before["captured_at"])).total_seconds())
        gap_after = abs((parse_api_time(after["captured_at"]) - edge).total_seconds())
        return before if gap_before <= gap_after else after

    def earliest_reading(self, player_ids):
        """The first reading held for any of these players, for "All time".

        An unbounded window is not the same as no window: the gains baseline
        and a chart's axis both need a real start.
        """
        if not player_ids:
            return None
        row = self.query_one(
            "SELECT MIN(captured_at) AS first FROM snapshots WHERE player_id IN ({})"
            .format(",".join("?" * len(player_ids))), list(player_ids))
        return row["first"] if row else None

    def latest_snapshot(self, player_id):
        return self.query_one(
            "SELECT id, player_id, captured_at FROM snapshots WHERE player_id=?"
            " ORDER BY captured_at DESC LIMIT 1", (player_id,))

    def snapshot_bounds(self, player_id, since, until=None):
        """The snapshots bracketing a window, or (None, None).

        `until` closes the window: gains then stop at the last snapshot inside
        it rather than running to whatever is newest, which is what a summary
        of "last Tuesday" needs.
        """
        end = (self.latest_snapshot(player_id) if until is None
               else self.snapshot_at_or_before(player_id, until))
        if end is None:
            return None, None
        return self.baseline_snapshot(player_id, since, end["captured_at"]), end

    def snapshot_at_or_before(self, player_id, when):
        return self.query_one(
            "SELECT id, player_id, captured_at FROM snapshots WHERE player_id=? AND captured_at<?"
            " ORDER BY captured_at DESC LIMIT 1", (player_id, when))

    def metric_gains(self, player_id, since, kind="skill", bounds=None, until=None):
        """How much each metric moved between `since` and now, as {metric: gained}.

        A metric missing from the opening state counts from zero rather than
        being dropped - see the comment below, which is the bug this docstring
        used to describe as the behaviour. Negative differences (rank
        shuffles, hiscore corrections) are clamped to zero. Pass `bounds` from
        `snapshot_bounds` to reuse one lookup across kinds.
        """
        start, end = bounds if bounds is not None else self.snapshot_bounds(
            player_id, since, until)
        if start is None or end is None or start["id"] == end["id"]:
            return {}
        # A metric missing from the opening state counts from zero rather than
        # being dropped: unranked means below the hiscore cutoff, and a boss
        # taken from unranked to 286 kills is 286 kills, not none. The same
        # goes for a boss that did not exist yet when the window opened.
        opened = {row["metric"]: row["value"]
                  for row in self.state_at(player_id, start["captured_at"], kind)}
        gains = {}
        for row in self.state_at(player_id, end["captured_at"], kind):
            if row["value"] is None:
                continue
            moved = row["value"] - (opened.get(row["metric"]) or 0.0)
            if moved:
                gains[row["metric"]] = max(0.0, moved)
        return gains


def _flatten(player_id, captured_at, data):
    for kind, key in (("skill", "skills"), ("boss", "bosses"),
                      ("activity", "activities"), ("computed", "computed")):
        section = data.get(key) or {}
        for metric, entry in section.items():
            if not isinstance(entry, dict):
                continue
            value = None
            for vk in _VALUE_KEYS:
                if entry.get(vk) is not None:
                    value = entry[vk]
                    break
            efficiency = entry.get("ehp")
            if efficiency is None:
                efficiency = entry.get("ehb")
            yield (
                player_id, kind, metric, captured_at,
                _num(value), _num(entry.get("rank")), _num(entry.get("level")),
                _num(efficiency),
            )


def _num(value):
    """Treat the API's -1 'unranked' sentinel as missing."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if value < 0 else value


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _origin(captured_at, fetched_at):
    """`poll` if our own request produced this reading, else `archive`."""
    made, got = parse_api_time(captured_at), parse_api_time(fetched_at)
    if made is None or got is None:
        return None
    return "poll" if (got - made).total_seconds() <= FRESH_SECONDS else "archive"


def _seconds_before(stamp, seconds):
    """`stamp` moved back by `seconds`, in the same ISO form."""
    when = parse_api_time(stamp) or datetime.now(timezone.utc)
    return (when - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _days_ago(days):
    when = datetime.now(timezone.utc) - timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
