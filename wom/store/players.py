"""Players: the roster, and what Wise Old Man says about each."""

import json

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

    def save_hiscores_reading(self, player_id, snapshot, combat_level=None,
                              exp=None, ehp=None, ehb=None, ttm=None):
        """Store a reading made from Jagex's hiscores. Returns the snapshot id.

        Not save_player_details: that is an upsert of Wise Old Man's whole
        player record, and a reading from the hiscores has none of it - no
        type, build, country or registration date - so going through it would
        blank every one of those on every run. This touches only what the
        hiscores can say, and leaves a figure it could not work out (EHP with
        no rates to hand) as it was rather than wiping it.
        """
        snapshot_id = self.save_snapshot(player_id, snapshot)
        conn = self.connect()
        with conn:
            conn.execute(
                "UPDATE players SET"
                " combat_level=COALESCE(?, combat_level),"
                " exp=COALESCE(?, exp), ehp=COALESCE(?, ehp),"
                " ehb=COALESCE(?, ehb), ttm=COALESCE(?, ttm),"
                " status='active', updated_at=?, last_fetched_at=? WHERE id=?",
                (combat_level, exp, ehp, ehb, ttm, snapshot.get("createdAt"),
                 _utcnow(), player_id))
        return snapshot_id

    def mark_missing(self, player_id):
        """The hiscores no longer know this name - see wom/renames.py."""
        conn = self.connect()
        with conn:
            conn.execute("UPDATE players SET status='missing' WHERE id=?",
                         (player_id,))

    def set_player_type(self, player_id, player_type):
        conn = self.connect()
        with conn:
            conn.execute("UPDATE players SET type=? WHERE id=?",
                         (player_type, player_id))

    def rename_username(self, old, new, display_name=None):
        """Move one account's stored rows from one name to another.

        Everything that files by name rather than by player id: the player
        row itself, the plugin's events and screenshots, which are keyed by
        name because they can arrive before the player is known, and the
        round-ups that named them winner. One transaction, so a rename is
        never half done. Returns False if the old name is not stored or the
        new one already is.
        """
        old, new = old.lower(), new.lower()
        conn = self.connect()
        with conn:
            if conn.execute("SELECT 1 FROM players WHERE username=?",
                            (new,)).fetchone():
                return False
            moved = conn.execute(
                "UPDATE players SET username=?, display_name=? WHERE username=?",
                (new, display_name or new, old)).rowcount
            if not moved:
                return False
            for table in ("session_events", "game_events", "images"):
                conn.execute("UPDATE OR IGNORE {} SET username=? WHERE username=?"
                             .format(table), (new, old))
            conn.execute("UPDATE group_summaries SET winner=? WHERE winner=?",
                         (new, old))
        return True

    def efficiency_rates(self, algorithm_type):
        """(ehp metas, ehb metas, fetched_at) as last stored, or Nones."""
        rows = {row["metric"]: row for row in self.query(
            "SELECT metric, payload, fetched_at FROM efficiency_rates"
            " WHERE type=?", (algorithm_type,))}
        if "ehp" not in rows:
            return None, None, None
        ehb = json.loads(rows["ehb"]["payload"]) if "ehb" in rows else []
        return (json.loads(rows["ehp"]["payload"]), ehb,
                rows["ehp"]["fetched_at"])

    def save_efficiency_rates(self, algorithm_type, metric, metas):
        conn = self.connect()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO efficiency_rates (type, metric,"
                " fetched_at, payload) VALUES (?,?,?,?)",
                (algorithm_type, metric, _utcnow(), json.dumps(metas)))

    def needs_backfill(self, player_id):
        """True until this player's history has been imported once."""
        row = self.query_one("SELECT backfilled_at FROM players WHERE id=?",
                             (player_id,))
        return row is not None and not row["backfilled_at"]

    def backfill_before(self, player_id):
        """Where this player's import resumes: before this moment, or now."""
        row = self.query_one("SELECT backfill_before FROM players WHERE id=?",
                             (player_id,))
        return row["backfill_before"] if row is not None else None

    def set_backfill_before(self, player_id, when):
        conn = self.connect()
        with conn:
            conn.execute("UPDATE players SET backfill_before=? WHERE id=?",
                         (when, player_id))

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

    def ids_for(self, usernames):
        """The stored ids of these accounts; any not stored yet are skipped."""
        names = [n.lower() for n in usernames]
        if not names:
            return []
        return [row["id"] for row in self.query(
            "SELECT id FROM players WHERE username IN ({})".format(
                ",".join("?" * len(names))), names)]

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
