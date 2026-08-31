"""The Milestones tab: a feed of the achievements Wise Old Man has recorded.

Newest first, one row per milestone, coloured by player and labelled with the
same icons the charts use.
"""

import logging
import tkinter as tk
from tkinter import ttk

from .. import periods
from ..icons import icon_kind_for, icon_path
from .. import theme
from ..util import fmt_ago, fmt_datetime, parse_api_time

log = logging.getLogger(__name__)

# Row icons are drawn at this height; the feed is a list, not a chart.
ROW_ICON_PX = 20

# Milestones dated to worse than this are shown with a "~" - Wise Old Man
# reports how precisely it knows each date, and imported ones can be vague.
APPROXIMATE_MS = 24 * 60 * 60 * 1000

ALL_TIME = "All time"

# Wise Old Man dates a milestone it cannot place to the epoch, so anything this
# old is "we know it happened, not when".
UNDATED_BEFORE = "1990"

MAX_ROWS = 500


class MilestonesPanel(ttk.Frame):
    """Scrollable feed of achievements for the players included in the sidebar."""

    def __init__(self, master, get_context):
        super().__init__(master)
        self.get_context = get_context
        self._icons = {}   # metric -> PhotoImage, kept alive for Tk

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))
        ttk.Label(bar, text="Since:").pack(side=tk.LEFT)
        self.period_chooser = ttk.Combobox(
            bar, state="readonly", width=10, values=[ALL_TIME] + periods.labels())
        self.period_chooser.set(ALL_TIME)
        self.period_chooser.pack(side=tk.LEFT, padx=6)
        self.period_chooser.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        self.summary_label = ttk.Label(bar, text="", foreground=theme.MUTED)
        self.summary_label.pack(side=tk.LEFT, padx=10)

        holder = ttk.Frame(self)
        holder.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.tree = ttk.Treeview(
            holder, columns=("when", "ago", "player", "milestone"),
            show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="")
        self.tree.heading("when", text="Achieved")
        self.tree.heading("ago", text="")
        self.tree.heading("player", text="Player")
        self.tree.heading("milestone", text="Milestone")
        self.tree.column("#0", width=44, minwidth=44, stretch=False, anchor=tk.CENTER)
        self.tree.column("when", width=120, stretch=False, anchor=tk.W)
        self.tree.column("ago", width=80, stretch=False, anchor=tk.E)
        self.tree.column("player", width=140, stretch=False, anchor=tk.W)
        self.tree.column("milestone", width=320, anchor=tk.W)
        # Rows carry an icon, so give them room to breathe.
        ttk.Style(self).configure("Milestones.Treeview", rowheight=ROW_ICON_PX + 6)
        self.tree.configure(style="Milestones.Treeview")

        vsb = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self.status = ttk.Label(self, text="", foreground=theme.MUTED)
        self.status.pack(anchor=tk.W, padx=10, pady=(0, 6))

    # -- data -------------------------------------------------------------

    @property
    def period(self):
        """The chosen window, or None for all time."""
        label = self.period_chooser.get()
        return None if label == ALL_TIME else periods.by_label(label)

    def refresh(self):
        ctx = self.get_context()
        self.tree.delete(*self.tree.get_children())

        included = ctx.selected
        if not included:
            self._message("Include at least one player using the sidebar swatches.")
            return

        period = self.period
        rows = ctx.db.achievements(
            player_ids=[p["id"] for p in included],
            since=period.start_iso() if period else None,
            limit=MAX_ROWS)
        if not rows:
            self._message("No milestones recorded {}.".format(
                "yet" if period is None else "in the last " + period.label.lower()))
            return

        for index, row in enumerate(rows):
            tags = [self._color_tag(ctx, row["username"])]
            if index % 2:
                tags.append("odd")
            dated = _is_dated(row["achieved_at"])
            self.tree.insert(
                "", tk.END, image=self._icon(row["metric"]), tags=tuple(tags),
                values=(self._when(row), fmt_ago(row["achieved_at"]) if dated else "",
                        row["display_name"], row["name"]))

        self.tree.tag_configure("odd", background=theme.RAISED)
        self._describe(ctx, rows, period)

    def _describe(self, ctx, rows, period):
        window = "all time" if period is None else "the last " + period.label.lower()
        who = ("all {} players".format(len(ctx.players))
               if len(ctx.selected) == len(ctx.players)
               else "{} of {} players".format(len(ctx.selected), len(ctx.players)))
        self.summary_label.config(text="{} - {}".format(window.capitalize(), who))
        capped = " (newest {} shown)".format(MAX_ROWS) if len(rows) >= MAX_ROWS else ""
        self.status.config(text="{} milestone{}{}".format(
            len(rows), "" if len(rows) == 1 else "s", capped))

    def _message(self, text):
        self.tree.insert("", tk.END, values=("", "", "", text))
        self.summary_label.config(text="")
        self.status.config(text="")

    # -- row bits ---------------------------------------------------------

    def _when(self, row):
        """Formatted date, marked approximate when the API says it is fuzzy."""
        if not _is_dated(row["achieved_at"]):
            return "unknown"
        accuracy = row["accuracy"]
        vague = accuracy is None or accuracy < 0 or accuracy > APPROXIMATE_MS
        return ("~" if vague else "") + fmt_datetime(row["achieved_at"], "%d %b %Y")

    def _color_tag(self, ctx, username):
        tag = "player_{}".format(username)
        self.tree.tag_configure(tag, foreground=ctx.color_for(username))
        return tag

    def _icon(self, metric):
        """A Tk image for the metric, scaled to row height. Cached and kept alive."""
        if metric in self._icons:
            return self._icons[metric]
        image = ""
        kind = icon_kind_for(metric) if metric else None
        if kind:
            try:
                from PIL import Image, ImageTk
                with Image.open(icon_path(metric, kind)) as handle:
                    icon = handle.convert("RGBA")
                if icon.height > ROW_ICON_PX:
                    scale = ROW_ICON_PX / float(icon.height)
                    icon = icon.resize(
                        (max(1, int(icon.width * scale)), ROW_ICON_PX), Image.NEAREST)
                image = ImageTk.PhotoImage(icon)
            except Exception:
                log.warning("could not load milestone icon for %s", metric, exc_info=True)
                image = ""
        self._icons[metric] = image
        return image


def _is_dated(achieved_at):
    """False for milestones Wise Old Man could not put a date on."""
    if not achieved_at or achieved_at < UNDATED_BEFORE:
        return False
    return parse_api_time(achieved_at) is not None
