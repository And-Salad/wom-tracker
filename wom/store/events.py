"""What players report while they are playing: sessions and milestones."""

import json

from .core import _seconds_before, _utcnow

# How long two identical reports from one account are treated as one
# event. See record_session_event.
SESSION_DEDUPE_SECONDS = 300


class EventStore:
    """What players report while they are playing: sessions and milestones."""

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

    def feed_events(self, kinds, player_ids=None, since=None, until=None,
                    limit=300):
        """Reported events for the milestones feed, newest first.

        Joined to players the way achievements() is, because the feed needs a
        display name and a colour and both live there. An event stored before
        we knew the account still finds its player by name.
        """
        marks = ",".join("?" * len(kinds))
        sql = ("SELECT g.*, p.username, p.display_name FROM game_events g"
               " LEFT JOIN players p ON p.username = g.username"
               " WHERE g.kind IN ({})".format(marks))
        params = list(kinds)
        if player_ids is not None:
            if not player_ids:
                return []
            sql += " AND p.id IN ({})".format(",".join("?" * len(player_ids)))
            params += list(player_ids)
        if since:
            sql += " AND g.happened_at>=?"
            params.append(since)
        if until:
            sql += " AND g.happened_at<?"
            params.append(until)
        return self.query(sql + " ORDER BY g.happened_at DESC LIMIT ?",
                          params + [limit])

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
