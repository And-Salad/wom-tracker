"""The Summaries tab: a folder tree of written notes, one per closed window."""

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .. import periods, summaries as core, theme
from ..util import fmt_ago

log = logging.getLogger(__name__)

FOLDERS = (("day", "Daily"), ("week", "Weekly"), ("month", "Monthly"))


class SummariesPanel(ttk.Frame):
    """Player -> period -> window on the left, the summary itself on the right."""

    def __init__(self, master, get_context, on_generated=None):
        super().__init__(master)
        self.get_context = get_context
        self.on_generated = on_generated
        self._busy = False
        self._rows = {}        # tree item id -> summary row

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))
        self.generate_button = ttk.Button(bar, text="Write missing summaries",
                                          command=self.generate)
        self.generate_button.pack(side=tk.LEFT)
        ttk.Button(bar, text="Expand all",
                   command=lambda: self._expand(True)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bar, text="Collapse all",
                   command=lambda: self._expand(False)).pack(side=tk.LEFT, padx=4)
        self.status = ttk.Label(bar, text="", foreground=theme.MUTED)
        self.status.pack(side=tk.LEFT, padx=10)

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        left = ttk.Frame(body)
        body.add(left, weight=0)
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse", height=22)
        scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.column("#0", width=260, stretch=True)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        right = ttk.Frame(body)
        body.add(right, weight=1)
        self.text = tk.Text(right, wrap=tk.WORD, relief=tk.FLAT, padx=14, pady=12,
                            background=theme.PANEL, foreground=theme.INK,
                            insertbackground=theme.INK, highlightthickness=0,
                            font=("Segoe UI", 10), spacing1=2, spacing3=8,
                            cursor="arrow")
        text_scroll = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=text_scroll.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.tag_configure("title", font=("Segoe UI", 12, "bold"), spacing3=2)
        self.text.tag_configure("meta", foreground=theme.MUTED,
                                font=("Segoe UI", 8), spacing3=10)
        self.text.tag_configure("empty", foreground=theme.MUTED)

    # -- the tree ---------------------------------------------------------

    def refresh(self):
        ctx = self.get_context()
        opened = {item for item in self._all_items() if self.tree.item(item, "open")}
        chosen = (self.tree.selection() or [None])[0]

        self.tree.delete(*self.tree.get_children())
        self._rows.clear()

        total = 0

        # The group verdict sits above the players: it is about all of them.
        group_folders = [(period, title, ctx.db.group_summaries(period=period))
                         for period, title in FOLDERS]
        if any(rows for _p, _t, rows in group_folders):
            self.tree.tag_configure("group", foreground=theme.ACCENT)
            node = self.tree.insert("", tk.END, iid="g", text="Group",
                                    tags=("group",), open="g" in opened or not opened)
            for period, title, rows in group_folders:
                if not rows:
                    continue
                folder_id = "g:" + period
                self.tree.insert(node, tk.END, iid=folder_id,
                                 text="{}  ({})".format(title, len(rows)),
                                 open=folder_id in opened)
                for row in rows:
                    leaf = "{}:{}".format(folder_id, row["window_key"])
                    self.tree.insert(folder_id, tk.END, iid=leaf, text=row["label"])
                    self._rows[leaf] = row
                    total += 1

        for player in ctx.selected:
            tag = "player_{}".format(player["username"])
            self.tree.tag_configure(tag, foreground=ctx.color_for(player))
            node = self.tree.insert("", tk.END, iid="p:" + player["username"],
                                    text=player["display_name"], tags=(tag,),
                                    open="p:" + player["username"] in opened)
            has_any = False
            for period, title in FOLDERS:
                rows = ctx.db.summaries(player_id=player["id"], period=period)
                if not rows:
                    continue
                has_any = True
                folder_id = "{}:{}".format(node, period)
                self.tree.insert(node, tk.END, iid=folder_id,
                                 text="{}  ({})".format(title, len(rows)),
                                 open=folder_id in opened)
                for row in rows:
                    leaf = "{}:{}".format(folder_id, row["window_key"])
                    self.tree.insert(folder_id, tk.END, iid=leaf, text=row["label"])
                    self._rows[leaf] = row
                    total += 1
            if not has_any:
                self.tree.insert(node, tk.END, text="nothing written yet")

        self.status.config(text="{} summar{}".format(
            total, "y" if total == 1 else "ies"))
        if chosen and self.tree.exists(chosen):
            self.tree.selection_set(chosen)
            self._on_select()
        else:
            self._show_placeholder(bool(ctx.selected), total)

    def _all_items(self, parent=""):
        for item in self.tree.get_children(parent):
            yield item
            for child in self._all_items(item):
                yield child

    def _expand(self, opened):
        for item in self._all_items():
            self.tree.item(item, open=opened)

    def _on_select(self, _event=None):
        selection = self.tree.selection()
        row = self._rows.get(selection[0]) if selection else None
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        if row is None:
            self.text.insert(tk.END, "Pick a summary on the left.", "empty")
        else:
            self.text.insert(tk.END, row["label"] + "\n", "title")
            # Group rows come from their own table and carry no player name.
            who = (row["display_name"] if "display_name" in row.keys()
                   else "Everyone")
            self.text.insert(tk.END, "{} - written {}\n".format(
                who, fmt_ago(row["generated_at"])), "meta")
            self.text.insert(tk.END, row["text"].strip() + "\n")
        self.text.configure(state=tk.DISABLED)

    def _show_placeholder(self, any_players, total):
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        if not any_players and not total:
            # No players ticked and no group round-up either: nothing to show.
            self.text.insert(
                tk.END, "Include at least one player using the sidebar swatches.",
                "empty")
        elif not total:
            self.text.insert(tk.END, (
                "No summaries written yet.\n\n"
                "Press “Write missing summaries” above, or run\n"
                "    py wom_tracker.py --summarize --due\n\n"
                "Each one calls the Claude API and costs about half a cent.\n"
                "Add your key under Options first."), "empty")
        else:
            self.text.insert(tk.END, "Pick a summary on the left.", "empty")
        self.text.configure(state=tk.DISABLED)

    # -- generating -------------------------------------------------------

    def generate(self):
        """Write whichever closed windows have not been summarised yet."""
        if self._busy:
            return
        ctx = self.get_context()
        players = list(ctx.selected)
        if not players:
            messagebox.showinfo("Nothing to write",
                                "Include at least one player first.", parent=self)
            return
        owed = core.due_periods(ctx.db)
        if not owed:
            self.status.config(text="everything is already written")
            return
        windows = [periods.latest_window(key) for key in owed]
        if not messagebox.askyesno(
            "Write summaries?",
            "Write {} for {} player{}?\n\nThat is {} calls to the Claude API, "
            "about half a cent each.".format(
                ", ".join(w.label for w in windows), len(players),
                "" if len(players) == 1 else "s", len(players) * len(windows)),
            parent=self,
        ):
            return

        self._busy = True
        self.generate_button.config(state=tk.DISABLED)
        self.status.config(text="writing...")

        def work():
            results = core.summarise_all(
                ctx.db, ctx.config, players, owed,
                progress=lambda e: self.after(
                    0, lambda: self.status.config(
                        text="{}: {}".format(e["player"], e["note"]))))
            self.after(0, lambda: self._done(results))

        threading.Thread(target=work, name="wom-summaries", daemon=True).start()

    def _done(self, results):
        self._busy = False
        self.generate_button.config(state=tk.NORMAL)
        failures = [r for r in results if r.get("failed")]
        self.refresh()
        if failures:
            self.status.config(text="{} failed".format(len(failures)))
            messagebox.showerror(
                "Summaries failed",
                "\n".join("{}: {}".format(r["player"], r["note"]) for r in failures[:5]),
                parent=self)
        if self.on_generated:
            self.on_generated()
