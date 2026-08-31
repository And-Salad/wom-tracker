"""Shared plumbing for the pluggable chart and table views.

Adding a new chart or table means writing one function and decorating it; the
UI pickers, refresh logic and player-selection handling come for free.
"""


class ViewContext:
    """Everything a chart or table function is given to render itself."""

    def __init__(self, database, config, player=None, players=None, selected=None,
                 period=None, choice=None):
        self.db = database
        self.config = config
        self.player = player            # sqlite3.Row for the highlighted player, or None
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

    @property
    def player_id(self):
        return self.player["id"] if self.player is not None else None

    @property
    def player_name(self):
        return self.player["display_name"] if self.player is not None else "no player selected"

    def color_for(self, player):
        """The chart colour for a player row or username - override, else palette."""
        from ..colors import player_color
        username = player if isinstance(player, str) else player["username"]
        index = next((i for i, row in enumerate(self.players)
                      if row["username"] == username), 0)
        return player_color(self.config, username, index)


class ViewSpec:
    def __init__(self, key, title, func, needs_player=False, description="", height=4.2,
                 options=None):
        self.key = key
        self.title = title
        self.func = func
        self.needs_player = needs_player
        self.description = description
        self.height = height        # figure height in inches, for stacked panels
        # Choices for a per-chart dropdown; the selection arrives as ctx.choice.
        self.options = list(options) if options else None


class Registry:
    """An ordered, keyed collection of ViewSpecs."""

    def __init__(self, name):
        self.name = name
        self._specs = {}

    def add(self, key, title, needs_player=False, description="", height=4.2,
            options=None):
        def decorator(func):
            self._specs[key] = ViewSpec(
                key, title, func, needs_player, description, height, options)
            return func
        return decorator

    def get(self, key):
        return self._specs.get(key)

    def specs(self):
        return list(self._specs.values())

    def titles(self):
        return [spec.title for spec in self._specs.values()]

    def by_title(self, title):
        for spec in self._specs.values():
            if spec.title == title:
                return spec
        return None

    def first(self):
        specs = self.specs()
        return specs[0] if specs else None
