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
