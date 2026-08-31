"""The main window: player list on the left, charts and tables on the right."""

import logging
import queue
import tkinter as tk
from datetime import datetime, timezone
from tkinter import messagebox, ttk

from .. import theme
from ..api import WomClient
from ..colors import default_color, player_color, set_player_color
from ..config import Config, DB_PATH
from ..db import Database
from ..scheduler import SlotScheduler, parse_last_run
from ..summaries import maybe_write_summaries
from ..updater import update_all
from ..util import fmt_ago, fmt_int
from .base import ViewContext
from .charts import ChartPanel
from .colorpicker import ask_color
from .milestones import MilestonesPanel
from .options import OptionsDialog
from .sharing import SharingPanel
from .summaries import SummariesPanel
from .summary import SummaryPanel
from .tables import TablePanel
from .tray import TrayIcon

log = logging.getLogger(__name__)

# Sidebar swatch colour for a player left out of the Summary comparison.
EXCLUDED_COLOR = theme.EXCLUDED


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WOM Tracker")
        self.geometry("1120x720")
        self.minsize(900, 560)

        self.config_obj = Config()
        self.db = Database(DB_PATH)
        self.client = self._make_client()
        self.events = queue.Queue()
        self._players = []
        self._selected_username = None
        self._checked = set()      # usernames included via the sidebar swatches
        self._seen = set()         # usernames the sidebar has shown before
        self._panels = ()          # the notebook's panels, set up in _build_ui
        self._stale = set()        # panels waiting for a redraw
        self._quitting = False

        self.scheduler = SlotScheduler(
            self.config_obj, self._run_update_job,
            on_state_change=lambda: self.events.put(("state", None)),
        )

        self._build_ui()
        self.tray = TrayIcon(
            "WOM Tracker", on_show=self._tray_show, on_update=self._tray_update,
            on_quit=self._tray_quit,
        )
        if self.tray.active:
            self.tray.start()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.reload_players()
        self.scheduler.start()
        self.after(150, self._drain_events)
        self.after(1000, self._tick_clock)

        self.after(600, self.sharing_panel.start_if_configured)

        if not self.config_obj.get("usernames"):
            self.after(400, self._first_run_hint)

    # -- construction -----------------------------------------------------

    def _make_client(self):
        return WomClient(
            api_key=self.config_obj.get("api_key", ""),
            contact=self.config_obj.get("user_agent_contact", ""),
        )

    def _build_ui(self):
        theme.apply_ttk(self)

        toolbar = ttk.Frame(self, padding=(10, 8))
        toolbar.pack(fill=tk.X)
        self.options_button = ttk.Button(toolbar, text="Options", command=self.open_options)
        self.options_button.pack(side=tk.LEFT)
        self.update_button = ttk.Button(toolbar, text="Update now",
                                        command=lambda: self.start_update("manual"))
        self.update_button.pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar, text="Refresh views", command=self.refresh_views).pack(side=tk.LEFT)

        self.next_run_label = ttk.Label(toolbar, text="", foreground=theme.MUTED)
        self.next_run_label.pack(side=tk.RIGHT)

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

        left = ttk.Frame(body)
        body.add(left, weight=0)
        heading = ttk.Frame(left)
        heading.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(heading, text="Tracked players", font=("", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(heading, text="None", width=5,
                   command=lambda: self.check_all(False)).pack(side=tk.RIGHT)
        ttk.Button(heading, text="All", width=4,
                   command=lambda: self.check_all(True)).pack(side=tk.RIGHT, padx=(0, 4))
        ttk.Label(left, wraplength=250, justify=tk.LEFT, foreground=theme.MUTED,
                  text="Click a swatch to include a player in Summary, right-click it "
                       "to recolour. Click a name to drive the other tabs.").pack(
            anchor=tk.W, pady=(0, 4))

        list_holder = ttk.Frame(left)
        list_holder.pack(fill=tk.BOTH, expand=True)
        self.player_list = ttk.Treeview(
            list_holder, columns=("pick", "name", "xp"), show="headings",
            selectmode="browse", height=20)
        self.player_list.heading("pick", text="")
        self.player_list.heading("name", text="Player")
        self.player_list.heading("xp", text="Total XP")
        self.player_list.column("pick", width=28, anchor=tk.CENTER, stretch=False)
        self.player_list.column("name", width=150, anchor=tk.W)
        self.player_list.column("xp", width=100, anchor=tk.E)
        scroll = ttk.Scrollbar(list_holder, orient=tk.VERTICAL,
                               command=self.player_list.yview)
        self.player_list.configure(yscrollcommand=scroll.set)
        self.player_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.player_list.bind("<<TreeviewSelect>>", self._on_player_selected)
        self.player_list.bind("<Button-1>", self._on_player_click)
        self.player_list.bind("<Button-3>", self._on_player_right_click)
        self.player_list.bind("<space>", lambda _e: self._toggle_checked(
            self._selected_username))

        right = ttk.Frame(body)
        body.add(right, weight=1)
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.summary_panel = SummaryPanel(self.notebook, self.view_context)
        self.milestones_panel = MilestonesPanel(self.notebook, self.view_context)
        self.summaries_panel = SummariesPanel(self.notebook, self.view_context)
        self.chart_panel = ChartPanel(self.notebook, self.view_context)
        self.table_panel = TablePanel(self.notebook, self.view_context)
        self.notebook.add(self.summary_panel, text="Summary")
        self.notebook.add(self.milestones_panel, text="Milestones")
        self.notebook.add(self.summaries_panel, text="Summaries")
        self.notebook.add(self.chart_panel, text="Charts")
        self.notebook.add(self.table_panel, text="Tables")
        # Sharing owns the web server and the tunnel, so neither needs a
        # console window of its own. It draws no data, so it stays out of
        # the redraw set.
        self.sharing_panel = SharingPanel(self.notebook, self.config_obj)
        self.notebook.add(self.sharing_panel, text="Sharing")
        self._panels = (self.summary_panel, self.milestones_panel,
                        self.summaries_panel, self.chart_panel, self.table_panel)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._refresh_visible())

        status = ttk.Frame(self, padding=(10, 4))
        status.pack(fill=tk.X)
        self.status_label = ttk.Label(status, text="Ready")
        self.status_label.pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(status, mode="determinate", length=200)
        self.progress.pack(side=tk.RIGHT)

    # -- data -------------------------------------------------------------

    def view_context(self):
        return ViewContext(
            self.db, self.config_obj, self.selected_player(), self._players,
            selected=self.checked_players(), period=self.summary_panel.period)

    def selected_player(self):
        for player in self._players:
            if player["username"] == self._selected_username:
                return player
        return self._players[0] if self._players else None

    def checked_players(self):
        """The players ticked in the sidebar, in display order."""
        return [p for p in self._players if p["username"] in self._checked]

    def reload_players(self):
        """Re-read the roster from the database and repaint the list."""
        self._players = self._ordered_players()
        known = {p["username"] for p in self._players}
        # New arrivals start ticked; anything that left stops being remembered.
        self._checked = (self._checked | (known - self._seen)) & known
        self._seen = known
        selection = self._selected_username
        self.player_list.delete(*self.player_list.get_children())
        for player in self._players:
            self.player_list.insert(
                "", tk.END, iid=player["username"],
                values=("", player["display_name"], fmt_int(player["exp"])))
        self._repaint_swatches()
        if selection and self.player_list.exists(selection):
            self.player_list.selection_set(selection)
        elif self._players:
            self._selected_username = self._players[0]["username"]
            self.player_list.selection_set(self._selected_username)
        else:
            self._selected_username = None
        self.refresh_views()

    def _ordered_players(self):
        """Players from the database, ordered to match the configured list."""
        stored = {row["username"]: row for row in self.db.players()}
        ordered = []
        for name in self.config_obj.get("usernames", []):
            row = stored.pop(name.lower(), None)
            if row is not None:
                ordered.append(row)
        # Anything still stored but not listed (e.g. renamed) goes at the end.
        ordered.extend(stored.values())
        return ordered

    # -- redrawing --------------------------------------------------------
    #
    # Redrawing a panel is expensive - the Summary tab alone is three
    # matplotlib figures over a few dozen queries - so panels are marked stale
    # and only the visible one is actually drawn. The rest catch up when their
    # tab is selected, and nothing is drawn at all while the window is hidden
    # in the tray.

    def refresh_views(self):
        """Everything is out of date - e.g. new data landed."""
        self.invalidate()

    def invalidate(self, *panels):
        """Mark panels as needing a redraw, then draw whichever one is on show."""
        self._stale.update(panels or self._panels)
        self._refresh_visible()

    def _refresh_visible(self):
        panel = self._visible_panel()
        if panel is None or panel not in self._stale:
            return
        # Skip only when the window is genuinely away - withdrawn to the tray or
        # minimised. `winfo_viewable` is also false before the window is first
        # mapped, which would skip the startup draw and open on blank charts.
        if self.state() in ("withdrawn", "iconic"):
            return  # redraw when the window comes back
        self._stale.discard(panel)
        try:
            panel.refresh()
        except Exception:
            log.exception("refreshing %s failed", type(panel).__name__)

    def _visible_panel(self):
        try:
            selected = self.notebook.select()
        except tk.TclError:
            return None
        return next((p for p in self._panels if str(p) == selected), None)

    def _on_player_selected(self, _event=None):
        selection = self.player_list.selection()
        if selection and selection[0] != self._selected_username:
            self._selected_username = selection[0]
            # Summary and Milestones follow the swatches, not the highlight.
            self.invalidate(self.chart_panel, self.table_panel)

    # -- sidebar swatches -------------------------------------------------

    INCLUDED = "■"     # filled square, drawn in the player's chart colour
    EXCLUDED = "□"     # hollow square, greyed out

    def color_for(self, username):
        """The chart colour for a player - their override, else the palette slot."""
        index = next((i for i, p in enumerate(self._players)
                      if p["username"] == username), 0)
        return player_color(self.config_obj, username, index)

    def _paint_swatch(self, username):
        """Show the player's chart colour, or a grey outline when excluded."""
        included = username in self._checked
        tag = "swatch_{}".format(username)
        self.player_list.tag_configure(
            tag, foreground=self.color_for(username) if included else EXCLUDED_COLOR)
        self.player_list.item(username, tags=(tag,))
        self.player_list.set(
            username, "pick", self.INCLUDED if included else self.EXCLUDED)

    def _repaint_swatches(self):
        for player in self._players:
            self._paint_swatch(player["username"])

    def _on_player_click(self, event):
        """Clicking the swatch toggles inclusion; clicking elsewhere selects."""
        if self.player_list.identify_region(event.x, event.y) != "cell":
            return None
        if self.player_list.identify_column(event.x) != "#1":
            return None
        row = self.player_list.identify_row(event.y)
        if row:
            self._toggle_checked(row)
        return "break"

    def _on_player_right_click(self, event):
        """Right-clicking a player opens the colour picker for their charts."""
        row = self.player_list.identify_row(event.y)
        if not row:
            return None
        self.player_list.selection_set(row)
        self.choose_player_color(row)
        return "break"

    def choose_player_color(self, username):
        player = next((p for p in self._players if p["username"] == username), None)
        if player is None:
            return
        index = self._players.index(player)
        chosen, reset = ask_color(
            self, initial=self.color_for(username),
            title="Colour for {}".format(player["display_name"]),
            default=default_color(index))
        if not chosen and not reset:
            return  # cancelled
        set_player_color(self.config_obj, username, None if reset else chosen)
        self._repaint_swatches()
        self.refresh_views()

    def _toggle_checked(self, username):
        if not username or not self.player_list.exists(username):
            return
        if username in self._checked:
            self._checked.discard(username)
        else:
            self._checked.add(username)
        self._paint_swatch(username)
        self.invalidate(self.summary_panel, self.milestones_panel,
                        self.summaries_panel)

    def check_all(self, checked):
        self._checked = {p["username"] for p in self._players} if checked else set()
        self._repaint_swatches()
        self.invalidate(self.summary_panel, self.milestones_panel,
                        self.summaries_panel)

    # -- updating ---------------------------------------------------------

    def start_update(self, trigger="manual"):
        names = self.config_obj.get("usernames", [])
        if not names:
            messagebox.showinfo(
                "Nothing to update",
                "Add some usernames under Options first.", parent=self)
            return
        if not self.scheduler.run_now(trigger):
            self.set_status("An update is already running.")

    def _run_update_job(self, trigger):
        """Runs on the scheduler's worker thread - talk to the UI via the queue."""
        names = self.config_obj.get("usernames", [])
        self.events.put(("run_start", (trigger, len(names))))

        def progress(index, total, result):
            self.events.put(("progress", (index, total, result)))

        def starting(index, total, username):
            self.events.put(("starting", (index, total, username)))

        results = update_all(
            self.client, self.db, names, trigger=trigger, progress=progress,
            starting=starting, cancelled=lambda: self._quitting,
        )
        written = maybe_write_summaries(self.db, self.config_obj)
        self.events.put(("run_done", (trigger, results, written)))

    def _drain_events(self):
        """Pull worker-thread events onto the Tk thread."""
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        finally:
            self.after(150, self._drain_events)

    def _handle_event(self, kind, payload):
        if kind == "run_start":
            trigger, total = payload
            self.progress.config(maximum=max(total, 1), value=0)
            self.update_button.config(state=tk.DISABLED)
            self.set_status("Updating {} player{} ({})...".format(
                total, "" if total == 1 else "s", trigger))
        elif kind == "starting":
            index, total, username = payload
            self.set_status("[{}/{}] {}...".format(index, total, username))
        elif kind == "progress":
            index, total, result = payload
            self.progress.config(value=index)
            self.set_status("[{}/{}] {}: {}".format(
                index, total, result.username,
                result.message if result.ok else "failed - " + result.message))
        elif kind == "run_done":
            trigger, results, written = payload
            failed = [r for r in results if not r.ok]
            self.update_button.config(state=tk.NORMAL)
            self.progress.config(value=0)
            self.reload_players()
            summary = "Updated {} of {} at {}".format(
                len(results) - len(failed), len(results), datetime.now().strftime("%H:%M"))
            for count, noun in ((sum(r.imported for r in results), "historic snapshot"),
                                (sum(r.milestones for r in results), "new milestone"),
                                (written, "summary")):
                if count:
                    summary += " - {} {}{}".format(
                        count, noun, "" if count == 1 else "s")
            if failed:
                summary += " - {} failed ({})".format(
                    len(failed), ", ".join(r.username for r in failed[:3]))
            self.set_status(summary)
            if trigger != "manual" and self.tray.active and not self.winfo_viewable():
                self.tray.notify(summary)
        elif kind == "state":
            self._update_next_run_label()
        elif kind == "show":
            self._show_window()
        elif kind == "quit":
            self.quit_app()
        elif kind == "update":
            self.start_update("tray")

    def set_status(self, text):
        self.status_label.config(text=text)

    def _tick_clock(self):
        self._update_next_run_label()
        self.after(30000, self._tick_clock)

    def _update_next_run_label(self):
        if self.scheduler.busy:
            self.next_run_label.config(text="Update running...")
            return
        if not self.config_obj.get("usernames"):
            self.next_run_label.config(text="No usernames yet - add some under Options")
            return
        last = parse_last_run(self.config_obj.get("last_run", ""))
        last_text = fmt_ago(last.isoformat()) if last else "never"
        now = datetime.now(timezone.utc)
        next_at = self.scheduler.next_run_at(now)
        if next_at <= now:
            when = "due now"
        else:
            # Show the slot on the user's own clock, adding the Eastern time
            # when the two differ.
            local = next_at.astimezone()
            when = local.strftime("%a %H:%M")
            if local.utcoffset() != next_at.utcoffset():
                when += " ({} ET)".format(next_at.strftime("%H:%M"))
        self.next_run_label.config(
            text="Last update: {}    Next: {}".format(last_text, when))

    # -- options ----------------------------------------------------------

    def open_options(self):
        OptionsDialog(self, self.config_obj, on_saved=self._options_saved)

    def _options_saved(self, changes):
        self.client = self._make_client()
        if changes.get("prune"):
            self.db.prune_players(changes["usernames"])
        self.scheduler.poke()
        self.reload_players()
        self._update_next_run_label()
        added = changes.get("added") or []
        if added and messagebox.askyesno(
            "Fetch new players?",
            "Fetch data for {} newly added name{} now?".format(
                len(added), "" if len(added) == 1 else "s"),
            parent=self,
        ):
            self.start_update("manual")

    def _first_run_hint(self):
        if messagebox.askyesno(
            "No usernames yet",
            "No players are being tracked.\n\nOpen Options to add some now?",
            parent=self,
        ):
            self.open_options()

    # -- window lifecycle -------------------------------------------------

    def _tray_show(self):
        self.events.put(("show", None))

    def _tray_update(self):
        self.events.put(("update", None))

    def _tray_quit(self):
        self.events.put(("quit", None))

    def _show_window(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()
        self._refresh_visible()   # draw whatever went stale while hidden

    def on_close(self):
        if self.config_obj.get("minimize_to_tray", True) and self.tray.active:
            self.withdraw()
            self.tray.notify("Still running - updates continue in the background.")
            return
        self.quit_app()

    def quit_app(self):
        self._quitting = True
        self.scheduler.stop()
        self.sharing_panel.shutdown()
        self.tray.stop()
        self.destroy()

    def raise_window(self):
        """Show the window on request from a second launch, whose socket
        thread is not the UI thread - so hand it to the event loop."""
        self.events.put(("show", None))
