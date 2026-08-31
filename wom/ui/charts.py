"""Chart definitions and the matplotlib panel that renders them.

To add a chart, write a function that draws onto a matplotlib Axes and
decorate it with @CHARTS.add(...). It shows up in the picker automatically.
"""

import os
import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import ttk

import matplotlib
# The desktop app draws into Tk; the web server renders PNGs with no display
# attached and sets this to "Agg" before importing anything from here.
matplotlib.use(os.environ.get("WOM_MPL_BACKEND", "TkAgg"))
from matplotlib.backends.backend_tkagg import (  # noqa: E402
    FigureCanvasTkAgg, NavigationToolbar2Tk,
)
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from .. import theme  # noqa: E402
from ..colors import DEFAULT_PALETTE  # noqa: E402
from ..util import (  # noqa: E402
    fmt_float, fmt_int, fmt_short, parse_api_time, pretty_metric, to_local,
)
from .base import Registry  # noqa: E402
from .tooltip import ChartTooltip, add_hover, hover_text  # noqa: E402

theme.apply_matplotlib()

CHARTS = Registry("charts")

# Accent colours for charts that are not split by player; per-player charts use
# ctx.color_for() so a colour picked in the sidebar follows the player around.
PALETTE = list(DEFAULT_PALETTE)


class NoData(Exception):
    """Raised by a chart when it has nothing to draw; the panel shows the message."""


# -- chart definitions ----------------------------------------------------

@CHARTS.add("overall_xp", "Overall XP over time", needs_player=True,
            description="Total experience for the selected player, per snapshot.")
def overall_xp(ax, ctx):
    # Whole-history line: one point per day is plenty and keeps it readable.
    rows = ctx.db.metric_history(ctx.player_id, "overall", "skill", bucket="day")
    points = [(to_local(parse_api_time(r["captured_at"])), r["value"]) for r in rows]
    points = [(t, v) for t, v in points if t and v is not None]
    if len(points) < 2:
        raise NoData("Need at least two updates for {} to draw a trend.".format(ctx.player_name))
    line, = ax.plot([p[0] for p in points], [p[1] for p in points],
                    marker="o", markersize=3, color=ctx.color_for(ctx.player))

    def describe(details, name=ctx.player_name):
        """Name the snapshot nearest the cursor rather than the whole line."""
        # matplotlib reports the hit indices as a numpy array, so test its
        # length rather than its truthiness.
        indices = details.get("ind") if details else None
        index = int(indices[0]) if indices is not None and len(indices) else -1
        when, value = points[index]
        return hover_text(name, "{} - {} XP".format(
            when.strftime("%d %b %Y %H:%M"), fmt_int(value)))

    add_hover(ax, line, describe)
    ax.set_title("Overall XP - {}".format(ctx.player_name))
    ax.set_ylabel("Experience")
    format_value_axis(ax)
    _format_date_axis(ax)


@CHARTS.add("xp_gained_week", "XP gained (last 7 days)",
            description="Experience gained per player across the stored week.")
def xp_gained_week(ax, ctx):
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    names, gains, colors = [], [], []
    for player in ctx.players:
        rows = ctx.db.query(
            "SELECT MIN(value) AS lo, MAX(value) AS hi FROM metrics"
            " WHERE player_id=? AND kind='skill' AND metric='overall' AND captured_at>=?",
            (player["id"], since),
        )
        row = rows[0] if rows else None
        if not row or row["hi"] is None or row["lo"] is None:
            continue
        names.append(player["display_name"])
        gains.append(row["hi"] - row["lo"])
        colors.append(ctx.color_for(player))
    if not names:
        raise NoData("No snapshots from the last 7 days yet.")
    order = sorted(range(len(names)), key=lambda i: gains[i], reverse=True)
    names = [names[i] for i in order]
    gains = [gains[i] for i in order]
    bars = ax.bar(names, gains, color=[colors[i] for i in order])
    for patch, name, value in zip(bars.patches, names, gains):
        add_hover(ax, patch, hover_text(name, "{} XP gained".format(fmt_int(value))))
    ax.set_title("XP gained in the last 7 days")
    ax.set_ylabel("Experience")
    format_value_axis(ax)
    _rotate_labels(ax, names)


@CHARTS.add("skill_levels", "Skill levels", needs_player=True,
            description="Every skill level for the selected player.")
def skill_levels(ax, ctx):
    rows = [r for r in ctx.db.latest_snapshot_metrics(ctx.player_id, "skill")
            if r["metric"] != "overall" and r["level"] is not None]
    if not rows:
        raise NoData("No snapshot stored for {} yet.".format(ctx.player_name))
    rows.sort(key=lambda r: r["level"])
    labels = [pretty_metric(r["metric"]) for r in rows]
    values = [r["level"] for r in rows]
    bars = ax.barh(labels, values, color=ctx.color_for(ctx.player))
    for patch, row in zip(bars.patches, rows):
        add_hover(ax, patch, hover_text(
            pretty_metric(row["metric"]),
            "Level {} - {} XP".format(fmt_int(row["level"]), fmt_int(row["value"]))))
    ax.set_xlim(0, 99)
    ax.set_title("Skill levels - {}".format(ctx.player_name))
    ax.tick_params(axis="y", labelsize=7)


@CHARTS.add("efficiency", "EHP and EHB by player",
            description="Efficient hours played and bossed for the whole list.")
def efficiency(ax, ctx):
    players = [p for p in ctx.players if p["ehp"] is not None or p["ehb"] is not None]
    if not players:
        raise NoData("No players stored yet - add some in Options, then update.")
    names = [p["display_name"] for p in players]
    ehp = [p["ehp"] or 0 for p in players]
    ehb = [p["ehb"] or 0 for p in players]
    positions = range(len(names))
    width = 0.4
    ehp_bars = ax.bar([i - width / 2 for i in positions], ehp, width,
                      label="EHP", color=PALETTE[0])
    ehb_bars = ax.bar([i + width / 2 for i in positions], ehb, width,
                      label="EHB", color=PALETTE[3])
    for bars, unit, hours in ((ehp_bars, "EHP", ehp), (ehb_bars, "EHB", ehb)):
        for patch, name, value in zip(bars.patches, names, hours):
            add_hover(ax, patch, hover_text(
                name, "{}: {} hours".format(unit, fmt_float(value))))
    ax.set_xticks(list(positions))
    ax.set_xticklabels(names)
    ax.set_title("Efficient hours")
    ax.set_ylabel("Hours")
    ax.legend(fontsize=8)
    format_value_axis(ax)
    _rotate_labels(ax, names)


@CHARTS.add("total_level", "Total level by player",
            description="Overall level across the tracked list.")
def total_level(ax, ctx):
    names, levels, colors = [], [], []
    for player in ctx.players:
        row = ctx.db.query_one(
            "SELECT level FROM metrics WHERE player_id=? AND kind='skill' AND metric='overall'"
            " ORDER BY captured_at DESC LIMIT 1", (player["id"],))
        if row and row["level"] is not None:
            names.append(player["display_name"])
            levels.append(row["level"])
            colors.append(ctx.color_for(player))
    if not names:
        raise NoData("No snapshots stored yet.")
    order = sorted(range(len(names)), key=lambda i: levels[i], reverse=True)
    names = [names[i] for i in order]
    levels = [levels[i] for i in order]
    bars = ax.bar(names, levels, color=[colors[i] for i in order])
    for patch, name, value in zip(bars.patches, names, levels):
        add_hover(ax, patch, hover_text(name, "Total level {}".format(fmt_int(value))))
    ax.set_title("Total level")
    ax.set_ylabel("Level")
    _rotate_labels(ax, names)


@CHARTS.add("top_bosses", "Top boss kill counts", needs_player=True,
            description="The selected player's fifteen most-killed bosses.")
def top_bosses(ax, ctx):
    rows = [r for r in ctx.db.latest_snapshot_metrics(ctx.player_id, "boss")
            if r["value"]]
    if not rows:
        raise NoData("No ranked boss kills for {} yet.".format(ctx.player_name))
    rows.sort(key=lambda r: r["value"], reverse=True)
    rows = rows[:15][::-1]
    bars = ax.barh([pretty_metric(r["metric"]) for r in rows], [r["value"] for r in rows],
                   color=ctx.color_for(ctx.player))
    for patch, row in zip(bars.patches, rows):
        add_hover(ax, patch, hover_text(
            pretty_metric(row["metric"]), "{} kills".format(fmt_int(row["value"]))))
    ax.set_title("Top bosses - {}".format(ctx.player_name))
    ax.set_xlabel("Kills")
    ax.tick_params(axis="y", labelsize=7)


# -- the panel ------------------------------------------------------------

class ChartPanel(ttk.Frame):
    """A chart picker plus the matplotlib canvas it draws into."""

    def __init__(self, master, get_context):
        super().__init__(master)
        self.get_context = get_context

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=8, pady=(8, 0))
        ttk.Label(bar, text="Chart:").pack(side=tk.LEFT)
        self.chooser = ttk.Combobox(bar, state="readonly", width=32,
                                    values=CHARTS.titles())
        self.chooser.pack(side=tk.LEFT, padx=6)
        self.chooser.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        first = CHARTS.first()
        if first:
            self.chooser.set(first.title)
        self.hint = ttk.Label(bar, text="", foreground=theme.MUTED)
        self.hint.pack(side=tk.LEFT, padx=10)

        self.figure = Figure(figsize=(7, 4.2), dpi=100)
        self.figure.set_facecolor(theme.PANEL)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        toolbar_holder = ttk.Frame(self)
        toolbar_holder.pack(fill=tk.X, padx=8)
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_holder, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side=tk.LEFT)
        self.tooltip = ChartTooltip(self.canvas)

    def refresh(self):
        spec = CHARTS.by_title(self.chooser.get()) or CHARTS.first()
        self.tooltip.hide()
        self.figure.clear()
        if spec is None:
            self.canvas.draw_idle()
            return
        self.hint.config(text=spec.description)
        ax = self.figure.add_subplot(111)
        ctx = self.get_context()
        try:
            if spec.needs_player and ctx.player is None:
                raise NoData("Select a player on the left to draw this chart.")
            spec.func(ax, ctx)
            ax.grid(True, axis="both", alpha=0.25, linewidth=0.6)
            ax.set_axisbelow(True)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
        except NoData as exc:
            draw_placeholder(ax, str(exc))
        except Exception as exc:  # a broken chart must not take the window down
            draw_placeholder(ax, "Chart failed: {}".format(exc))
        self.figure.tight_layout()
        self.canvas.draw_idle()


def draw_placeholder(ax, message):
    ax.clear()
    ax.text(0.5, 0.5, message, ha="center", va="center", wrap=True,
            fontsize=10, color=theme.MUTED, transform=ax.transAxes)
    ax.set_axis_off()


def format_value_axis(ax, axis="y"):
    formatter = FuncFormatter(lambda v, _pos: fmt_short(v))
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(formatter)


def _format_date_axis(ax):
    ax.figure.autofmt_xdate(rotation=25, ha="right")


def _rotate_labels(ax, names):
    if len(names) > 4 or max((len(n) for n in names), default=0) > 8:
        ax.tick_params(axis="x", labelrotation=25, labelsize=8)
        for label in ax.get_xticklabels():
            label.set_horizontalalignment("right")
