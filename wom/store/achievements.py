"""Milestones Wise Old Man dates for us."""

from .core import _utcnow


class AchievementStore:
    """Milestones Wise Old Man dates for us."""

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

    def _milestone_filter(self, player_ids, since, until):
        """The WHERE the feed's rows and the feed's count share.

        Built once and used by both: a total that filtered differently from
        the rows it counts is a number nobody can act on, and the two drift
        the moment they are written out twice.
        """
        sql = ""
        params = []
        if player_ids is not None:
            sql += " AND a.player_id IN ({})".format(",".join("?" * len(player_ids)))
            params.extend(player_ids)
        if since:
            sql += " AND a.achieved_at >= ?"
            params.append(since)
        if until:
            sql += " AND a.achieved_at < ?"
            params.append(until)
        return sql, params

    def achievements(self, player_ids=None, since=None, until=None, limit=500):
        """Milestones for the given players, newest first."""
        if player_ids is not None and not player_ids:
            return []
        where, params = self._milestone_filter(player_ids, since, until)
        # Wise Old Man dates everything it found between two snapshots to the
        # same instant, so a tie here is the common case, not the edge one.
        # Ordering those by name puts "1000 Zulrah kills" above "500 Zulrah
        # kills"; ordering by the threshold reads as the run it was.
        return self.query(
            "SELECT a.*, p.display_name, p.username FROM achievements a"
            " JOIN players p ON p.id = a.player_id WHERE 1=1" + where +
            " ORDER BY a.achieved_at DESC, a.metric, a.measure,"
            " a.threshold, a.name LIMIT ?", params + [limit])

    def count_achievements(self, player_ids=None, since=None, until=None):
        """How many there are, which is not how many a page shows."""
        if player_ids is not None and not player_ids:
            return 0
        where, params = self._milestone_filter(player_ids, since, until)
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM achievements a"
            " JOIN players p ON p.id = a.player_id WHERE 1=1" + where, params)
        return row["n"] if row is not None else 0
