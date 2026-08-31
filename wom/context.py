"""What a chart is handed to build itself.

It is its own module rather than part of wom/web/data.py because the chart
builders and the summary digests both lean on it and neither owns it.
"""

from .colors import player_color


class ViewContext:
    """Everything a chart or table function is given to render itself."""

    def __init__(self, database, config, players=None, selected=None,
                 period=None, choice=None):
        self.db = database
        self.config = config
        self.players = players or []    # every tracked player, display order
        # The players ticked in the sidebar - what the Summary tab compares.
        self.selected = list(selected) if selected is not None else list(self.players)
        self.period = period            # a wom.periods.Period, on the Summary tab
        # The value from a chart's own dropdown, for charts that declare one.
        self.choice = choice
        # Per-refresh memo. A context is built fresh for every redraw, so it
        # can cache freely without any risk of going stale.
        self._bounds = {}
        self._gains = {}

    def gains(self, player, kind="skill"):
        """{metric: gained} for this player over the period, computed once.

        The Summary tab asks for the same player's skills and bosses from
        different charts; without this each ask repeats the two snapshot
        lookups that bracket the window.
        """
        player_id = player if isinstance(player, int) else player["id"]
        key = (player_id, kind)
        if key not in self._gains:
            since = self.period.start_iso()
            if player_id not in self._bounds:
                self._bounds[player_id] = self.db.snapshot_bounds(player_id, since)
            self._gains[key] = self.db.metric_gains(
                player_id, since, kind, bounds=self._bounds[player_id])
        return self._gains[key]

    def baseline(self, player):
        """The snapshot this player's gains are actually measured from.

        Wise Old Man's history is sparse for players it has not watched long,
        so this can sit well inside the window - the caller decides whether
        that is worth telling the viewer about.
        """
        player_id = player if isinstance(player, int) else player["id"]
        if player_id not in self._bounds:
            self._bounds[player_id] = self.db.snapshot_bounds(
                player_id, self.period.start_iso())
        return self._bounds[player_id][0]

    def color_for(self, player):
        """The chart colour for a player row or username - override, else palette."""
        username = player if isinstance(player, str) else player["username"]
        index = next((i for i, row in enumerate(self.players)
                      if row["username"] == username), 0)
        return player_color(self.config, username, index)
