"""Readings, and the sparse metric history flattened out of them."""

import json

from ..util import parse_api_time
from .core import _flatten, _origin, _utcnow


class SnapshotStore:
    """Readings, and the sparse metric history flattened out of them."""

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
        """Store many snapshots, skipping any held. Returns how many were new."""
        return sum(1 for s in snapshots if self.save_snapshot(player_id, s) is not None)

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

    def knows_metric(self, kind, metric):
        """True if we already track this metric, so a name can be checked."""
        return self.query_one(
            "SELECT 1 AS ok FROM metrics WHERE kind=? AND metric=? LIMIT 1",
            (kind, metric)) is not None

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
            "SELECT id, player_id, captured_at FROM snapshots"
            " WHERE player_id=? AND captured_at<=?"
            " ORDER BY captured_at DESC LIMIT 1", (player_id, since))
        after = self.query_one(
            "SELECT id, player_id, captured_at FROM snapshots"
            " WHERE player_id=? AND captured_at>?"
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
            "SELECT id, player_id, captured_at FROM snapshots"
            " WHERE player_id=? AND captured_at<?"
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
