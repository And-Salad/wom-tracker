"""The screenshots behind the gallery - their rows, not their bytes."""

from .core import _utcnow


class ImageStore:
    """The screenshots behind the gallery - their rows, not their bytes."""

    def record_image(self, digest, username, kind, fmt, size, happened_at,
                     event_id=None, caption=None, when=None):
        """Note one stored screenshot. Returns True if it was new."""
        row = self.player_by_username(username)
        conn = self.connect()
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO images (digest, username, player_id,"
                " kind, format, bytes, happened_at, received_at, event_id,"
                " caption) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (digest, username, row["id"] if row is not None else None, kind,
                 fmt, size, happened_at, when or _utcnow(), event_id, caption))
        return bool(cur.rowcount)

    def image(self, digest):
        return self.query_one("SELECT * FROM images WHERE digest=?", (digest,))

    def images(self, kind=None, player_ids=None, limit=10):
        """Stored screenshots, newest first."""
        sql = ("SELECT i.*, p.display_name FROM images i"
               " LEFT JOIN players p ON p.id = i.player_id")
        clauses, params = [], []
        if kind:
            clauses.append("i.kind=?")
            params.append(kind)
        if player_ids is not None:
            if not player_ids:
                return []
            clauses.append("i.player_id IN ({})".format(
                ",".join("?" * len(player_ids))))
            params += list(player_ids)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.query(sql + " ORDER BY i.happened_at DESC LIMIT ?",
                          params + [limit])

    def surplus_images(self, kind, keep):
        """The rows past the newest `keep` of a kind, oldest first."""
        return self.query(
            "SELECT * FROM images WHERE kind=? AND digest NOT IN ("
            "  SELECT digest FROM images WHERE kind=?"
            "   ORDER BY happened_at DESC LIMIT ?) ORDER BY happened_at",
            (kind, kind, keep))

    def oldest_images(self, limit=50):
        return self.query(
            "SELECT * FROM images ORDER BY happened_at LIMIT ?", (limit,))

    def image_bytes_stored(self):
        row = self.query_one("SELECT COALESCE(SUM(bytes), 0) AS n FROM images")
        return row["n"] if row is not None else 0

    def forget_image(self, digest):
        conn = self.connect()
        with conn:
            conn.execute("DELETE FROM images WHERE digest=?", (digest,))
