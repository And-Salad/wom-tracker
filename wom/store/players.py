"""Players: the roster, and what Wise Old Man says about each."""

from .core import _utcnow


class PlayerStore:
    """Players: the roster, and what Wise Old Man says about each."""

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
                                     registered_at, updated_at, last_changed_at,
                                     last_fetched_at)
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
                    details.get("country"), details.get("combatLevel"),
                    details.get("exp"),
                    details.get("ehp"), details.get("ehb"), details.get("ttm"),
                    details.get("registeredAt"), details.get("updatedAt"),
                    details.get("lastChangedAt"), now,
                ),
            )

        snapshot = details.get("latestSnapshot")
        if snapshot:
            self.save_snapshot(pid, snapshot)
        return pid

    def needs_backfill(self, player_id):
        """True until this player's history has been imported once."""
        row = self.query_one("SELECT backfilled_at FROM players WHERE id=?",
                             (player_id,))
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

    def players(self):
        return self.query("SELECT * FROM players ORDER BY display_name COLLATE NOCASE")

    def player_by_username(self, username):
        return self.query_one("SELECT * FROM players WHERE username=?",
                              (username.lower(),))

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
