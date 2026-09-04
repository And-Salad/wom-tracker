"""The written notes and round-ups, and the runs that produce them."""

from .core import _utcnow


class RecapStore:
    """The written notes and round-ups, and the runs that produce them."""

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
                "SELECT * FROM summaries"
                " WHERE player_id=? AND period=? AND window_key=?",
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
                           winner=None, board="maxing"):
        usage = usage or {}
        conn = self.connect()
        with conn:
            conn.execute(
                "INSERT INTO group_summaries (board, period, window_key,"
                " period_start, period_end, label, text, digest_hash, model,"
                " input_tokens, output_tokens, generated_at, winner)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(board, period, window_key) DO UPDATE SET"
                "   period_start=excluded.period_start, period_end=excluded.period_end,"
                "   label=excluded.label, text=excluded.text,"
                "   digest_hash=excluded.digest_hash, model=excluded.model,"
                "   input_tokens=excluded.input_tokens,"
                "   output_tokens=excluded.output_tokens,"
                "   generated_at=excluded.generated_at,"
                "   winner=excluded.winner",
                (board, window.period, window.key, window.start_iso(),
                 window.end_iso(),
                 window.label, text, digest_hash, usage.get("model"),
                 usage.get("input_tokens"), usage.get("output_tokens"),
                 _utcnow(), winner))

    def group_summary(self, period, window_key, board="maxing"):
        return self.query_one(
            "SELECT * FROM group_summaries"
            " WHERE board=? AND period=? AND window_key=?",
            (board, period, window_key))

    def group_summaries(self, period=None, limit=500, board="maxing"):
        """Round-ups for one board. `board=None` for every board at once."""
        sql = "SELECT * FROM group_summaries WHERE 1=1"
        params = []
        if board:
            sql += " AND board=?"
            params.append(board)
        if period:
            sql += " AND period=?"
            params.append(period)
        sql += " ORDER BY window_key DESC LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    def start_run(self, trigger, roster=None):
        """Open a run. `roster` is how many players it set out to update."""
        conn = self.connect()
        with conn:
            cur = conn.execute(
                "INSERT INTO runs (started_at, trigger, roster) VALUES (?,?,?)",
                (_utcnow(), trigger, roster))
        return cur.lastrowid

    def finish_run(self, run_id, ok_count, fail_count, notes=""):
        conn = self.connect()
        with conn:
            conn.execute(
                "UPDATE runs SET finished_at=?, ok_count=?, fail_count=?, notes=?"
                " WHERE id=?",
                (_utcnow(), ok_count, fail_count, notes, run_id),
            )
