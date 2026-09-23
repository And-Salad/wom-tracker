"""Thinning old history so a year of readings stays a small file."""

from .core import _days_ago


def _players_clause(column, ids):
    """` AND column IN (?,?)` and its parameters, or nothing for no ids."""
    ids = list(ids)
    if not ids:
        return "", []
    return " AND {} IN ({})".format(column, ",".join("?" * len(ids))), ids


class MaintenanceStore:
    """Thinning old history so a year of readings stays a small file."""

    def _doomed_snapshots(self, cutoff, thin=(), only=()):
        """The WHERE clause, and its parameters, for snapshots a pass removes.

        Readings we polled are thinned, and so is everything held for a
        player in `thin` whatever its origin. `only` confines the pass to
        those players.
        """
        thinned, thin_args = _players_clause("player_id", thin)
        scoped, only_args = _players_clause("player_id", only)
        origin = "COALESCE(origin,'poll') = 'poll'"
        if thinned:
            origin = "({} OR {})".format(origin, thinned[len(" AND "):])
        where = (
            "captured_at < ? AND " + origin + scoped +
            " AND id NOT IN (SELECT id FROM ("
            "     SELECT id, MAX(captured_at) FROM snapshots WHERE captured_at < ?"
            + scoped +
            "     GROUP BY player_id, substr(captured_at, 1, 10)))")
        return where, [cutoff] + thin_args + only_args + [cutoff] + only_args

    def compaction_preview(self, keep_days=30, thin=(), only=()):
        """How many snapshots a compaction would drop, without touching anything."""
        cutoff = _days_ago(keep_days)
        total = self.query_one("SELECT COUNT(*) AS n FROM snapshots")["n"]
        where, args = self._doomed_snapshots(cutoff, thin, only)
        doomed = self.query_one(
            "SELECT COUNT(*) AS n FROM snapshots WHERE " + where, args)["n"]
        return {"total": total, "removable": doomed, "cutoff": cutoff,
                "keep_days": keep_days}

    def compact_snapshots(self, keep_days=30, thin=(), only=(), vacuum=True):
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

        Except for the players in `thin`, which are the celebrities. Anybody
        on Wise Old Man can update a famous account, so theirs arrive as
        archive readings a hundred a day - thirty-odd thousand a year for one
        account, kept for ever under the rule above. And the rule is there for
        session ends and midnight boundaries, which only the recaps and the
        leaderboards care about, and celebrities are in neither. So theirs are
        thinned whatever their origin.

        `only` confines a pass to some players - an import thins what it has
        just stored - and `vacuum=False` skips rewriting the file, which is
        the whole database's worth of work for one account's pages.

        Metrics are thinned with them, to the last change of each metric on
        each day. That has to happen together: a change deleted while the
        reading after it survives would leave the reading carrying an older
        value, which is worse than losing the detail. Keeping each day's last
        change and each day's last reading is exact at every surviving moment.

        Returns the preview dict with the actual count removed.
        """
        summary = self.compaction_preview(keep_days, thin, only)
        cutoff = summary["cutoff"]
        where, args = self._doomed_snapshots(cutoff, thin, only)
        scoped, only_args = _players_clause("player_id", only)
        conn = self.connect()
        with conn:
            cur = conn.execute("DELETE FROM snapshots WHERE " + where, args)
            summary["removed"] = cur.rowcount
            # Each metric's rows ranked within their day in one pass. This
            # was a correlated subquery - for every row, the MAX of that
            # metric's day, found by reading the metric's whole history again
            # - which is quadratic in how much of it there is: 80 seconds at a
            # celebrity's year and a half locally, and the write lock held for
            # all of it. The rows kept are the same ones: rank 1 is the MAX.
            conn.execute(
                "DELETE FROM metrics WHERE captured_at < ?" + scoped +
                " AND (player_id, kind, metric, captured_at) IN ("
                "   SELECT player_id, kind, metric, captured_at FROM ("
                "     SELECT player_id, kind, metric, captured_at,"
                "       ROW_NUMBER() OVER (PARTITION BY player_id, kind, metric,"
                "         substr(captured_at, 1, 10) ORDER BY captured_at DESC) AS n"
                "     FROM metrics WHERE captured_at < ?" + scoped + ")"
                "   WHERE n > 1)"
                " AND NOT EXISTS (SELECT 1 FROM snapshots s"
                "   WHERE s.player_id=metrics.player_id"
                "     AND s.captured_at=metrics.captured_at)",
                [cutoff] + only_args + [cutoff] + only_args)
        if vacuum:
            # VACUUM cannot run inside a transaction, and in WAL mode its
            # result has to be checkpointed or the file never actually shrinks.
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return summary
