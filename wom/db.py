"""SQLite storage for players, snapshots and the flattened metric history.

Snapshots are kept whole (as JSON) so nothing from the API is lost, and are
also flattened into `metrics` so charts and tables can query them with SQL.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .util import parse_api_time

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
    payload     TEXT NOT NULL,                    -- raw snapshot data as JSON
    UNIQUE (player_id, captured_at)
);

CREATE TABLE IF NOT EXISTS metrics (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    player_id   INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    kind        TEXT NOT NULL,                    -- skill | boss | activity | computed
    metric      TEXT NOT NULL,                    -- e.g. overall, zulrah, ehp
    value       REAL,                             -- experience | kills | score | value
    rank        INTEGER,
    level       INTEGER,                          -- skills only
    efficiency  REAL,                             -- ehp for skills, ehb for bosses
    PRIMARY KEY (snapshot_id, kind, metric)
);

CREATE INDEX IF NOT EXISTS idx_metrics_lookup
    ON metrics (player_id, metric, captured_at);

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

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok_count    INTEGER DEFAULT 0,
    fail_count  INTEGER DEFAULT 0,
    trigger     TEXT,                             -- scheduled | manual | startup
    notes       TEXT
);
"""

_VALUE_KEYS = ("experience", "kills", "score", "value")


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
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(players)")}
        with conn:
            if "backfilled_at" not in columns:
                conn.execute("ALTER TABLE players ADD COLUMN backfilled_at TEXT")

        # Summaries used to be one row per period, covering a rolling window
        # with no start or end. There is no honest way to relabel those as
        # calendar windows, and they are cheap to write again, so the old table
        # is dropped rather than converted.
        # The round-ups started naming a winner in a column rather than only
        # in their prose, so the calendar on /summaries can colour by it.
        group_columns = {row["name"]
                         for row in conn.execute("PRAGMA table_info(group_summaries)")}
        if group_columns and "winner" not in group_columns:
            with conn:
                conn.execute("ALTER TABLE group_summaries ADD COLUMN winner TEXT")

        summary_columns = {row["name"]
                           for row in conn.execute("PRAGMA table_info(summaries)")}
        if summary_columns and "window_key" not in summary_columns:
            with conn:
                conn.execute("DROP TABLE summaries")
            conn.executescript(SCHEMA)

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
        """Insert one snapshot and its flattened metrics; ignores duplicates."""
        captured_at = snapshot.get("createdAt") or _utcnow()
        data = snapshot.get("data") or {}
        conn = self.connect()
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO snapshots (player_id, captured_at, fetched_at, payload)"
                " VALUES (?,?,?,?)",
                (player_id, captured_at, _utcnow(), json.dumps(data)),
            )
            if cur.rowcount == 0:
                return None  # already stored
            snapshot_id = cur.lastrowid
            conn.executemany(
                "INSERT OR REPLACE INTO metrics (snapshot_id, player_id, captured_at,"
                " kind, metric, value, rank, level, efficiency) VALUES (?,?,?,?,?,?,?,?,?)",
                list(_flatten(snapshot_id, player_id, captured_at, data)),
            )
        return snapshot_id

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

    def snapshot_count(self, player_id):
        row = self.query_one("SELECT COUNT(*) AS n FROM snapshots WHERE player_id=?",
                             (player_id,))
        return row["n"] if row else 0

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

    def start_run(self, trigger):
        conn = self.connect()
        with conn:
            cur = conn.execute(
                "INSERT INTO runs (started_at, trigger) VALUES (?,?)", (_utcnow(), trigger)
            )
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
        window is left alone; beyond it only each day's last snapshot survives.
        Each day's *last* reading is the one kept - matching what a daily
        chart point shows. It cannot be picked by highest id: history is
        imported newest-first, so within an imported day the largest id is the
        oldest snapshot.

        Metrics cascade from snapshots, so one delete does it. Returns the
        preview dict with the actual count removed.
        """
        summary = self.compaction_preview(keep_days)
        cutoff = summary["cutoff"]
        conn = self.connect()
        with conn:
            cur = conn.execute(
                "DELETE FROM snapshots WHERE captured_at < ?"
                " AND id NOT IN (SELECT id FROM ("
                "     SELECT id, MAX(captured_at) FROM snapshots WHERE captured_at < ?"
                "     GROUP BY player_id, substr(captured_at, 1, 10)))",
                (cutoff, cutoff))
            summary["removed"] = cur.rowcount
        # VACUUM cannot run inside a transaction, and reclaims the file space.
        conn.execute("VACUUM")
        return summary

    def prune_players(self, keep_usernames):
        """Drop players no longer on the tracked list, and everything they own.

        Snapshots, metrics and achievements all cascade from `players`, so the
        one delete is enough. Returns how many players went.
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

    def metric_history(self, player_id, metric, kind="skill", limit=None, since=None,
                       bucket=None, until=None):
        """Time series of one metric for one player, oldest first.

        Unbounded by default: at four snapshots a day a row cap silently drops
        the oldest points off a chart, which reads as history that never
        happened. Callers that want a window pass `since` instead.

        `bucket="day"` returns the last reading of each UTC day. Updates arrive
        at least four times daily and often more, which is more detail than a
        month-wide axis can render; one end-of-day point per day plots the same
        curve from a fraction of the rows.

        With `since`, the snapshot just before the window is included as well,
        so a line drawn over that window starts at its left edge instead of
        wherever the first snapshot inside it happens to fall. `until` closes
        the window at the other end.
        """
        where = " WHERE player_id=? AND metric=? AND kind=?"
        params = [player_id, metric, kind]
        if since:
            where += " AND captured_at>=?"
            params.append(since)
        if until:
            where += " AND captured_at<?"
            params.append(until)
        if bucket == "day":
            # MAX() picks each day's last reading, and SQLite fills the bare
            # columns from that same row.
            sql = ("SELECT MAX(captured_at) AS captured_at, value, rank, level,"
                   " efficiency FROM metrics" + where +
                   " GROUP BY substr(captured_at, 1, 10)")
        else:
            sql = ("SELECT captured_at, value, rank, level, efficiency FROM metrics"
                   + where)
        if limit:
            # Keep the newest rows when capped, but still hand them back oldest first.
            rows = list(reversed(self.query(
                sql + " ORDER BY captured_at DESC LIMIT ?", params + [limit])))
        else:
            rows = self.query(sql + " ORDER BY captured_at ASC", params)
        if since:
            baseline = self.query_one(
                "SELECT captured_at, value, rank, level, efficiency FROM metrics"
                " WHERE player_id=? AND metric=? AND kind=? AND captured_at<?"
                " ORDER BY captured_at DESC LIMIT 1",
                (player_id, metric, kind, since))
            if baseline is not None:
                rows.insert(0, baseline)
        return rows

    def latest_snapshot_metrics(self, player_id, kind=None):
        sql = (
            "SELECT * FROM metrics WHERE snapshot_id = ("
            "  SELECT id FROM snapshots WHERE player_id=? ORDER BY captured_at DESC LIMIT 1)"
        )
        params = [player_id]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        return self.query(sql + " ORDER BY metric", params)

    def snapshot_metrics(self, snapshot_id, kind=None):
        """One named reading's metrics.

        The same rows as latest_snapshot_metrics, for a snapshot already
        chosen. A window that ends in the past has to report where the account
        stood then, not where it stands now.
        """
        sql = "SELECT * FROM metrics WHERE snapshot_id=?"
        params = [snapshot_id]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        return self.query(sql + " ORDER BY metric", params)

    def export_rows(self, player_ids, kinds=None, since=None, until=None,
                    batch=2000):
        """Every stored reading in a range, oldest first, yielded in batches.

        For the export page, which can legitimately ask for a year of every
        metric for everyone - tens of thousands of rows. Yielding keeps the
        whole result off the heap and lets the response stream.
        """
        if not player_ids:
            return
        sql = ("SELECT m.captured_at, p.display_name, p.username, m.kind,"
               "       m.metric, m.value, m.level, m.rank"
               "  FROM metrics m JOIN players p ON p.id = m.player_id"
               " WHERE m.player_id IN ({})".format(",".join("?" * len(player_ids))))
        params = list(player_ids)
        if kinds:
            sql += " AND m.kind IN ({})".format(",".join("?" * len(kinds)))
            params.extend(kinds)
        if since:
            sql += " AND m.captured_at >= ?"
            params.append(since)
        if until:
            sql += " AND m.captured_at < ?"
            params.append(until)
        sql += " ORDER BY m.captured_at, p.display_name, m.kind, m.metric"
        cursor = self.connect().execute(sql, params)
        while True:
            rows = cursor.fetchmany(batch)
            if not rows:
                return
            for row in rows:
                yield row

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
            "SELECT id, captured_at FROM snapshots WHERE player_id=? AND captured_at<=?"
            " ORDER BY captured_at DESC LIMIT 1", (player_id, since))
        after = self.query_one(
            "SELECT id, captured_at FROM snapshots WHERE player_id=? AND captured_at>?"
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
            "SELECT id, captured_at FROM snapshots WHERE player_id=?"
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
            "SELECT id, captured_at FROM snapshots WHERE player_id=? AND captured_at<?"
            " ORDER BY captured_at DESC LIMIT 1", (player_id, when))

    def metric_gains(self, player_id, since, kind="skill", bounds=None, until=None):
        """How much each metric moved between `since` and now, as {metric: gained}.

        Only metrics present in both snapshots are reported, and negative
        differences (rank shuffles, hiscore corrections) are clamped to zero.
        Pass `bounds` from `snapshot_bounds` to reuse one lookup across kinds.
        """
        start, end = bounds if bounds is not None else self.snapshot_bounds(
            player_id, since, until)
        if start is None or end is None or start["id"] == end["id"]:
            return {}
        rows = self.query(
            # LEFT JOIN, not JOIN: a metric the player was unranked on at the
            # baseline has no value there (the API's -1 is stored as NULL), and
            # an inner join silently dropped it from the window entirely - so a
            # boss taken from unranked to 286 kills counted as no kills at all.
            # Unranked means below the hiscore cutoff, so zero is the right
            # thing to measure from; the same goes for a boss that did not
            # exist yet when the baseline was taken.
            "SELECT e.metric AS metric, e.value - COALESCE(s.value, 0) AS gained"
            " FROM metrics e LEFT JOIN metrics s"
            "   ON s.snapshot_id=? AND s.kind=e.kind AND s.metric=e.metric"
            " WHERE e.snapshot_id=? AND e.kind=? AND e.value IS NOT NULL",
            (start["id"], end["id"], kind),
        )
        return {r["metric"]: max(0.0, r["gained"]) for r in rows if r["gained"]}


def _flatten(snapshot_id, player_id, captured_at, data):
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
                snapshot_id, player_id, captured_at, kind, metric,
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


def _days_ago(days):
    when = datetime.now(timezone.utc) - timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
