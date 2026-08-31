"""The Summary tab: one period, several players, a stack of comparison charts.

Charts here live in their own registry and are drawn one under another, so
adding the next one is a single decorated function - no layout work.
"""

import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.offsetbox import AnnotationBbox, OffsetImage

from .. import catalog, periods, theme
from ..catalog import (CHOICE_METRICS, COLLECTION_LOG, LOG_METRICS,
                       TOP_BOSSES, TOTAL_LEVEL)
from ..icons import ICON_CANVAS_PX, SKILL_ORDER, load_icon
from ..util import fmt_int, parse_api_time, pretty_metric, to_local
from .base import Registry
from .charts import NoData, draw_placeholder, format_value_axis
from .tooltip import ChartTooltip, add_hover, hover_text

SUMMARY_CHARTS = Registry("summary")


def _chart(key):
    """Register a drawing function against its entry in wom/catalog.py.

    Titles, descriptions and dropdown choices live there, not here, so the
    desktop tab and the web dashboard cannot drift apart: this module supplies
    only the matplotlib drawing, and charts.js supplies the D3 equivalent.
    """
    spec = catalog.BY_KEY[key]
    return SUMMARY_CHARTS.add(spec.key, spec.title, description=spec.description,
                              height=spec.height, options=spec.options)


# -- chart definitions ----------------------------------------------------

@_chart("skill_gains")
def skill_gains(ax, ctx):
    _stacked_by_metric(
        ax, ctx, kind="skill", metrics=SKILL_ORDER,
        ylabel="Experience gained",
        empty="No experience gained by the included players in the last {}.")


@_chart("boss_gains")
def boss_gains(ax, ctx):
    players = ctx.selected
    if not players:
        raise NoData("Include at least one player using the sidebar swatches.")

    gains = {p["id"]: ctx.gains(p, "boss") for p in players}
    totals = {}
    for per_player in gains.values():
        for metric, value in per_player.items():
            totals[metric] = totals.get(metric, 0.0) + value
    ranked = [m for m, _v in sorted(totals.items(), key=lambda kv: -kv[1])][:TOP_BOSSES]
    if not ranked:
        raise NoData("No boss kills by the included players in the last {}.".format(
            ctx.period.label.lower()))

    _stacked_by_metric(
        ax, ctx, kind="boss", metrics=ranked, ylabel="Kills gained",
        empty="No boss kills by the included players in the last {}.", gains=gains)


@_chart("level_trend")
def level_trend(ax, ctx):
    choice = ctx.choice or TOTAL_LEVEL
    metric = CHOICE_METRICS.get(choice, "overall")

    def describe(when, level, value):
        return "{} - level {} ({} XP)".format(
            when.strftime("%d %b %Y"), fmt_int(level), fmt_int(value))

    _trend_by_metric(
        ax, ctx, kind="skill", metric=metric, field="level", describe=describe,
        ylabel="{} level".format("Total" if metric == "overall"
                                 else pretty_metric(metric)),
        empty="No {} history for the included players in the last {{}}.".format(
            choice.lower()))


@_chart("log_and_clues")
def log_and_clues(ax, ctx):
    choice = ctx.choice or COLLECTION_LOG
    metric = LOG_METRICS.get(choice, "collections_logged")
    unit = "slots" if metric == "collections_logged" else "completed"

    def describe(when, score, _value):
        return "{} - {} {}".format(when.strftime("%d %b %Y"), fmt_int(score), unit)

    _trend_by_metric(
        ax, ctx, kind="activity", metric=metric, field="value", describe=describe,
        ylabel="Collection log slots" if metric == "collections_logged"
               else "{} completed".format(choice),
        empty="No {} history for the included players in the last {{}}.".format(
            choice.lower()))


# -- shared drawing -------------------------------------------------------

def _trend_by_metric(ax, ctx, kind, metric, field, ylabel, empty, describe):
    """One line per included player: a running total over the chosen period."""
    players = ctx.selected
    if not players:
        raise NoData("Include at least one player using the sidebar swatches.")

    since = ctx.period.start_iso()
    drawn = 0
    for player in players:
        rows = ctx.db.metric_history(player["id"], metric, kind, since=since,
                                     bucket=ctx.period.bucket)
        points = [(to_local(parse_api_time(r["captured_at"])), r[field], r["value"])
                  for r in rows]
        points = [p for p in points if p[0] and p[1] is not None]
        if not points:
            continue
        for segment, run in _plot_with_gaps(ax, points, ctx.period,
                                            label=player["display_name"],
                                            color=ctx.color_for(player)):
            # Each run carries its own slice of the points, so the tooltip
            # names the right snapshot whichever side of a gap the cursor is.
            add_hover(ax, segment,
                      _point_label(player["display_name"], run, describe))
        drawn += 1
    if not drawn:
        raise NoData(empty.format(ctx.period.label.lower()))

    ax.set_ylabel(ylabel)
    # Pin the axis to the chosen window: the baseline snapshot deliberately
    # sits before it, and autoscale otherwise leaves empty months on the right.
    ax.set_xlim(to_local(parse_api_time(since)), datetime.now().astimezone())
    ax.figure.autofmt_xdate(rotation=20, ha="right")
    legend = ax.legend(fontsize=8, ncol=min(drawn, 4), frameon=False,
                       loc="lower left", bbox_to_anchor=(0, 1.01), borderaxespad=0)
    _hover_legend(ax, legend)


def _plot_with_gaps(ax, points, period, label, color):
    """One player's line, dashed wherever it spans missing history.

    Wise Old Man's history has holes - weeks or months with no snapshot at all.
    Joining across one draws a straight line through time nobody measured,
    which reads as steady progress that may never have happened. Those
    stretches are dashed: the two ends are real, the middle is a guess.

    Returns (line, its points) for every solid run, so all of them can be
    hit-tested against the readings they were actually drawn from.
    """
    limit = timedelta(days=max(1.5, period.days * 0.04))
    runs = [[points[0]]]
    guesses = []
    for previous, point in zip(points, points[1:]):
        if point[0] - previous[0] > limit:
            guesses.append((previous, point))
            runs.append([point])
        else:
            runs[-1].append(point)

    for previous, point in guesses:
        ax.plot([previous[0], point[0]], [previous[1], point[1]],
                linewidth=1.1, alpha=0.5, linestyle=(0, (3, 3)), color=color)
    lines = []
    for run in runs:
        drawn, = ax.plot([p[0] for p in run], [p[1] for p in run],
                         marker="o", markersize=2.5, linewidth=1.4,
                         label=label if not lines else None, color=color)
        lines.append((drawn, run))
    return lines


def _point_label(name, points, describe):
    """Tooltip naming the snapshot nearest the cursor on a trend line."""
    def label(details):
        # matplotlib reports hit indices as a numpy array, so check its length.
        indices = details.get("ind") if details else None
        index = int(indices[0]) if indices is not None and len(indices) else -1
        return hover_text(name, describe(*points[index]))
    return label

def _stacked_by_metric(ax, ctx, kind, metrics, ylabel, empty, gains=None, labels=None):
    """One stacked column per metric, one slice per included player."""
    players = ctx.selected
    if not players:
        raise NoData("Include at least one player using the sidebar swatches.")
    if gains is None:
        gains = {p["id"]: ctx.gains(p, kind) for p in players}

    metrics = list(metrics)
    positions = list(range(len(metrics)))
    bottoms = [0.0] * len(metrics)
    unit = ylabel.lower()
    drawn = 0
    for player in players:
        values = [gains[player["id"]].get(metric, 0.0) for metric in metrics]
        if not any(values):
            continue  # keep the legend to players who actually did something
        bars = ax.bar(positions, values, bottom=bottoms, width=0.75,
                      label=player["display_name"], color=ctx.color_for(player),
                      edgecolor=theme.PANEL, linewidth=0.4)
        for index, patch in enumerate(bars.patches):
            if values[index] > 0:
                add_hover(ax, patch, hover_text(
                    player["display_name"], "{}: {} {}".format(
                        pretty_metric(metrics[index]), fmt_int(values[index]), unit)))
        bottoms = [b + v for b, v in zip(bottoms, values)]
        drawn += 1
    if not drawn:
        raise NoData(empty.format(ctx.period.label.lower()))

    ax.set_xticks(positions)
    ax.set_xlim(-0.75, len(metrics) - 0.25)
    ax.set_ylabel(ylabel)
    format_value_axis(ax)
    _label_axis_with_icons(ax, metrics, kind, labels)
    # Anchor the legend's bottom edge above the axes so it never sits on a
    # column - the tallest one is often the first.
    legend = ax.legend(fontsize=8, ncol=min(drawn, 4), frameon=False,
                       loc="lower left", bbox_to_anchor=(0, 1.01), borderaxespad=0)
    _hover_legend(ax, legend)


def _hover_legend(ax, legend):
    """Name each legend swatch, so a colour alone is enough to identify."""
    if legend is None:
        return
    handles = getattr(legend, "legend_handles", None) or []
    for handle, label in zip(handles, legend.get_texts()):
        add_hover(ax, handle, label.get_text())
        add_hover(ax, label, label.get_text())


def _label_axis_with_icons(ax, metrics, kind="skill", labels=None):
    """Put the game icons under the x axis, falling back to short names.

    `labels` captions each icon, for charts whose icons are near-identical.
    """
    images = {metric: load_icon(metric, kind) for metric in metrics}
    if not any(image is not None for image in images.values()):
        ax.set_xticklabels([pretty_metric(m) for m in metrics],
                           rotation=45, ha="right", fontsize=7)
        return

    zoom = _icon_zoom(ax, len(metrics))
    ax.set_xticklabels([""] * len(metrics))
    pad = int(ICON_CANVAS_PX * zoom) + 4
    ax.tick_params(axis="x", length=0, pad=pad + (10 if labels else 0))
    for index, metric in enumerate(metrics):
        image = images.get(metric)
        if image is None:
            ax.annotate(pretty_metric(metric)[:8], (index, 0), (0, -12),
                        xycoords=("data", "axes fraction"), textcoords="offset points",
                        ha="center", va="top", fontsize=6, annotation_clip=False)
            continue
        # Nearest-neighbour sampling: these are 25px sprites and smooth
        # resampling turns them to mush.
        box = AnnotationBbox(
            OffsetImage(image, zoom=zoom, interpolation="nearest"),
            (index, 0), xybox=(0, -4), xycoords=("data", "axes fraction"),
            boxcoords="offset points", box_alignment=(0.5, 1.0),
            frameon=False, annotation_clip=False, pad=0)
        ax.add_artist(box)
        # The icons are the only label this axis has, so name them on hover.
        add_hover(ax, box, pretty_metric(metric))
        if labels:
            ax.annotate(labels.get(metric, pretty_metric(metric)), (index, 0),
                        (0, -(pad + 2)), xycoords=("data", "axes fraction"),
                        textcoords="offset points", ha="center", va="top",
                        fontsize=7, annotation_clip=False)


def _icon_zoom(ax, columns):
    """Draw icons 1:1 when the columns are wide enough, else step down.

    Sprites look best at native size, so only shrink when they would otherwise
    collide, and then only by a clean fraction.
    """
    figure = ax.figure
    axes_px = ax.get_position().width * figure.get_size_inches()[0] * figure.dpi
    per_column = axes_px / max(columns, 1)
    for candidate in (1.0, 0.75, 0.5):
        if per_column >= ICON_CANVAS_PX * candidate:
            return candidate
    return 0.5


# -- the panel ------------------------------------------------------------

class SummaryPanel(ttk.Frame):
    """Period picker over a scrolling column of charts."""

    def __init__(self, master, get_context, on_period_change=None):
        super().__init__(master)
        self.get_context = get_context
        self.on_period_change = on_period_change
        self._cards = []
        self._tooltips = []
        self._choosers = {}   # chart key -> its own dropdown, where it has one

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))
        ttk.Label(bar, text="Period:").pack(side=tk.LEFT)
        self.period_chooser = ttk.Combobox(bar, state="readonly", width=10,
                                           values=periods.labels())
        self.period_chooser.set(periods.get(periods.DEFAULT_PERIOD).label)
        self.period_chooser.pack(side=tk.LEFT, padx=6)
        self.period_chooser.bind("<<ComboboxSelected>>", self._period_changed)
        self.summary_label = ttk.Label(bar, text="", foreground=theme.MUTED)
        self.summary_label.pack(side=tk.LEFT, padx=10)

        # A canvas is the only scrollable container in tk, so the charts live
        # in a frame inside one.
        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.canvas = tk.Canvas(outer, highlightthickness=0,
                                background=theme.BACKGROUND)
        scroll = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.body = ttk.Frame(self.canvas)
        self._body_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body_resize)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel(True))
        self.canvas.bind("<Leave>", lambda _e: self._bind_wheel(False))

        self._build_cards()

    @property
    def period(self):
        return periods.by_label(self.period_chooser.get())

    # -- layout -----------------------------------------------------------

    def _build_cards(self):
        for spec in SUMMARY_CHARTS.specs():
            card = ttk.LabelFrame(self.body, text=spec.title, padding=(6, 4))
            card.pack(fill=tk.X, expand=True, pady=(0, 10), padx=2)
            header = ttk.Frame(card)
            header.pack(fill=tk.X)
            if spec.description:
                ttk.Label(header, text=spec.description,
                          foreground=theme.MUTED).pack(side=tk.LEFT)
            if spec.options:
                chooser = ttk.Combobox(header, state="readonly", width=16,
                                       values=spec.options)
                chooser.set(spec.options[0])
                chooser.pack(side=tk.RIGHT, padx=(8, 2))
                chooser.bind(
                    "<<ComboboxSelected>>",
                    lambda _e, key=spec.key: self.refresh(only=key))
                ttk.Label(header, text="Show:").pack(side=tk.RIGHT)
                self._choosers[spec.key] = chooser
            figure = Figure(figsize=(9, spec.height), dpi=100)
            figure.set_facecolor(theme.PANEL)
            canvas = FigureCanvasTkAgg(figure, master=card)
            widget = canvas.get_tk_widget()
            widget.pack(fill=tk.BOTH, expand=True)
            widget.bind("<Enter>", lambda _e: self._bind_wheel(True))
            self._tooltips.append(ChartTooltip(canvas))
            self._cards.append((spec, figure, canvas))

    def _on_body_resize(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure(self._body_id, width=event.width)

    def _bind_wheel(self, active):
        if active:
            self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        else:
            self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _period_changed(self, _event=None):
        if self.on_period_change:
            self.on_period_change(self.period)
        self.refresh()

    # -- drawing ----------------------------------------------------------

    def refresh(self, only=None):
        """Redraw the cards. `only` names one chart key - used when that
        chart's own dropdown changes, since the others cannot have moved."""
        ctx = self.get_context()
        for tooltip in self._tooltips:
            tooltip.hide()
        self.summary_label.config(text=self._describe(ctx))
        for spec, figure, canvas in self._cards:
            if only is not None and spec.key != only:
                continue
            figure.clear()
            ax = figure.add_subplot(111)
            chooser = self._choosers.get(spec.key)
            ctx.choice = chooser.get() if chooser is not None else None
            try:
                spec.func(ax, ctx)
                ax.grid(True, axis="y", alpha=0.25, linewidth=0.6)
                ax.set_axisbelow(True)
                for side in ("top", "right"):
                    ax.spines[side].set_visible(False)
            except NoData as exc:
                draw_placeholder(ax, str(exc))
            except Exception as exc:  # one broken chart must not blank the tab
                draw_placeholder(ax, "Chart failed: {}".format(exc))
            figure.tight_layout()
            canvas.draw_idle()

    def _describe(self, ctx):
        count = len(ctx.selected)
        total = len(ctx.players)
        if not total:
            return "No players tracked yet."
        who = "all {} players".format(total) if count == total else \
            "{} of {} players".format(count, total)
        line = "Last {} - {}".format(ctx.period.label.lower(), who)
        short = self._short_history(ctx)
        return "{} ({})".format(line, short) if short else line

    def _short_history(self, ctx):
        """Players whose bars cover less than the period asks for.

        Wise Old Man only has the snapshots it has, so a player it started
        watching three weeks ago still gets a full-height bar on the Year
        chart. Say whose, rather than let it pass for a year of play.
        """
        opened = parse_api_time(ctx.period.start_iso())
        asked = (datetime.now(timezone.utc) - opened).total_seconds()
        names = []
        for player in ctx.selected:
            start = ctx.baseline(player)
            if start is None:
                continue
            # A tenth of the window is slop for the six-hourly cadence.
            measured = parse_api_time(start["captured_at"])
            if (measured - opened).total_seconds() > asked * 0.1:
                names.append(player["display_name"])
        if not names:
            return ""
        return "short history: " + ", ".join(names)

