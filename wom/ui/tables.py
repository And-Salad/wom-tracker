"""Table definitions and the Treeview panel that renders them.

A table function takes a ViewContext and returns (columns, rows):
  columns: list of (heading, width, anchor, sort_kind) where sort_kind is
           "text" or "number"
  rows:    list of tuples of already-formatted cell strings
"""

import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import ttk

from .. import theme
from ..util import (
    fmt_ago, fmt_datetime, fmt_float, fmt_int, fmt_signed, pretty_metric,
)
from .base import Registry

TABLES = Registry("tables")

NUM = "number"
TXT = "text"


# -- table definitions ----------------------------------------------------

@TABLES.add("players", "Player overview",
            description="One row per tracked username.")
def players_table(ctx):
    columns = [
        ("Player", 150, tk.W, TXT), ("Type", 90, tk.W, TXT), ("Build", 90, tk.W, TXT),
        ("Combat", 60, tk.E, NUM), ("Total lvl", 70, tk.E, NUM),
        ("Total XP", 110, tk.E, NUM), ("EHP", 70, tk.E, NUM), ("EHB", 70, tk.E, NUM),
        ("Status", 80, tk.W, TXT), ("Last update", 110, tk.W, TXT),
    ]
    rows = []
    for player in ctx.players:
        overall = ctx.db.query_one(
            "SELECT level FROM metrics WHERE player_id=? AND kind='skill' AND metric='overall'"
            " ORDER BY captured_at DESC LIMIT 1", (player["id"],))
        rows.append((
            player["display_name"], pretty_metric(player["type"] or "-"),
            pretty_metric(player["build"] or "-"), fmt_int(player["combat_level"]),
            fmt_int(overall["level"] if overall else None), fmt_int(player["exp"]),
            fmt_float(player["ehp"]), fmt_float(player["ehb"]),
            pretty_metric(player["status"] or "-"), fmt_ago(player["updated_at"]),
        ))
    return columns, rows


@TABLES.add("skills", "Skills", needs_player=True,
            description="Latest levels, experience and weekly gains.")
def skills_table(ctx):
    columns = [
        ("Skill", 130, tk.W, TXT), ("Level", 60, tk.E, NUM),
        ("Experience", 120, tk.E, NUM), ("Rank", 100, tk.E, NUM),
        ("EHP", 70, tk.E, NUM), ("Gained (7d)", 100, tk.E, NUM),
    ]
    gains = _weekly_gains(ctx, "skill")
    rows = []
    for row in ctx.db.latest_snapshot_metrics(ctx.player_id, "skill"):
        rows.append((
            pretty_metric(row["metric"]), fmt_int(row["level"]), fmt_int(row["value"]),
            fmt_int(row["rank"]), fmt_float(row["efficiency"]),
            fmt_signed(gains.get(row["metric"])),
        ))
    return columns, rows


@TABLES.add("bosses", "Boss kill counts", needs_player=True,
            description="Ranked bosses only; unranked kills are hidden by the API.")
def bosses_table(ctx):
    columns = [
        ("Boss", 190, tk.W, TXT), ("Kills", 90, tk.E, NUM), ("Rank", 100, tk.E, NUM),
        ("EHB", 70, tk.E, NUM), ("Gained (7d)", 100, tk.E, NUM),
    ]
    gains = _weekly_gains(ctx, "boss")
    rows = []
    for row in ctx.db.latest_snapshot_metrics(ctx.player_id, "boss"):
        if not row["value"]:
            continue  # unranked bosses come back as -1 and are stored as empty
        rows.append((
            pretty_metric(row["metric"]), fmt_int(row["value"]), fmt_int(row["rank"]),
            fmt_float(row["efficiency"]), fmt_signed(gains.get(row["metric"])),
        ))
    return columns, rows


@TABLES.add("activities", "Activities", needs_player=True,
            description="Clues, minigames and other scored activities.")
def activities_table(ctx):
    columns = [
        ("Activity", 190, tk.W, TXT), ("Score", 90, tk.E, NUM), ("Rank", 100, tk.E, NUM),
        ("Gained (7d)", 100, tk.E, NUM),
    ]
    gains = _weekly_gains(ctx, "activity")
    rows = []
    for row in ctx.db.latest_snapshot_metrics(ctx.player_id, "activity"):
        if not row["value"]:
            continue  # unranked activities come back as -1 and are stored as empty
        rows.append((
            pretty_metric(row["metric"]), fmt_int(row["value"]), fmt_int(row["rank"]),
            fmt_signed(gains.get(row["metric"])),
        ))
    return columns, rows


@TABLES.add("runs", "Update history",
            description="Every update pass this program has made.")
def runs_table(ctx):
    columns = [
        ("Started", 140, tk.W, TXT), ("Trigger", 90, tk.W, TXT),
        ("Updated", 70, tk.E, NUM), ("Failed", 60, tk.E, NUM),
        ("Notes", 380, tk.W, TXT),
    ]
    rows = [
        (fmt_datetime(run["started_at"]), run["trigger"] or "-",
         fmt_int(run["ok_count"]), fmt_int(run["fail_count"]), run["notes"] or "")
        for run in ctx.db.recent_runs(50)
    ]
    return columns, rows


def _weekly_gains(ctx, kind):
    """Per-metric change over the last seven days of stored snapshots."""
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ctx.db.metric_gains(ctx.player_id, since, kind)


# -- the panel ------------------------------------------------------------

class TablePanel(ttk.Frame):
    """A table picker plus a sortable Treeview."""

    def __init__(self, master, get_context):
        super().__init__(master)
        self.get_context = get_context
        self._sort_state = {}

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=8, pady=(8, 0))
        ttk.Label(bar, text="Table:").pack(side=tk.LEFT)
        self.chooser = ttk.Combobox(bar, state="readonly", width=32, values=TABLES.titles())
        self.chooser.pack(side=tk.LEFT, padx=6)
        self.chooser.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        first = TABLES.first()
        if first:
            self.chooser.set(first.title)
        self.hint = ttk.Label(bar, text="", foreground=theme.MUTED)
        self.hint.pack(side=tk.LEFT, padx=10)

        holder = ttk.Frame(self)
        holder.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.tree = ttk.Treeview(holder, show="headings", selectmode="browse")
        vsb = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(holder, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.tree.tag_configure("odd", background=theme.RAISED)

        self.status = ttk.Label(self, text="", foreground=theme.MUTED)
        self.status.pack(anchor=tk.W, padx=10, pady=(0, 6))

    def refresh(self):
        spec = TABLES.by_title(self.chooser.get()) or TABLES.first()
        if spec is None:
            return
        self.hint.config(text=spec.description)
        ctx = self.get_context()
        try:
            if spec.needs_player and ctx.player is None:
                self._show_message("Select a player on the left to fill this table.")
                return
            columns, rows = spec.func(ctx)
        except Exception as exc:
            self._show_message("Table failed: {}".format(exc))
            return
        self._render(columns, rows)
        self.status.config(text="{} row{}".format(len(rows), "" if len(rows) == 1 else "s"))

    # -- rendering --------------------------------------------------------

    def _show_message(self, message):
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = ("message",)
        self.tree.heading("message", text="")
        self.tree.column("message", width=600, anchor=tk.W)
        self.tree.insert("", tk.END, values=(message,))
        self.status.config(text="")

    def _render(self, columns, rows):
        self._columns = columns
        self._rows = rows
        self.tree.delete(*self.tree.get_children())
        keys = [str(index) for index in range(len(columns))]
        self.tree["columns"] = keys
        for key, (heading, width, anchor, kind) in zip(keys, columns):
            self.tree.heading(
                key, text=heading,
                command=lambda k=key, idx=int(key), knd=kind: self._sort(idx, knd))
            self.tree.column(key, width=width, anchor=anchor, stretch=False)
        self._fill(rows)

    def _fill(self, rows):
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(rows):
            self.tree.insert("", tk.END, values=row, tags=("odd",) if index % 2 else ())

    def _sort(self, index, kind):
        ascending = not self._sort_state.get(index, False)
        self._sort_state = {index: ascending}

        def key(row):
            cell = row[index]
            if kind == NUM:
                cleaned = str(cell).replace(",", "").replace("+", "").strip()
                try:
                    return (0, float(cleaned))
                except ValueError:
                    return (1, 0.0)  # dashes and blanks sort last
            return (0, str(cell).lower())

        self._rows = sorted(self._rows, key=key, reverse=not ascending)
        self._fill(self._rows)
