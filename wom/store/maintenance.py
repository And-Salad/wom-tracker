"""Thinning old history so a year of readings stays a small file."""

from .core import _days_ago


class MaintenanceStore:
    """Thinning old history so a year of readings stays a small file."""

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
