"""The Sharing tab: run the dashboard and its public link from this window.

Everything here used to be two console windows kept alive by hand. The point
of the tab is that there is nothing left to keep alive - close the app and both
stop with it, open it and they can come back on their own.
"""

import logging
import queue
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import messagebox, ttk

from .. import sharing, theme

log = logging.getLogger(__name__)


class SharingPanel(ttk.Frame):
    """Start/stop the local dashboard and an optional public tunnel."""

    def __init__(self, master, config):
        super().__init__(master)
        self.config_obj = config
        # The server thread and cloudflared's reader both report progress.
        # Tk is not thread-safe - not even `after` - so they post here and the
        # UI thread drains it, the same way the main window handles its own
        # worker events.
        self.events = queue.Queue()
        self.server = sharing.WebServer(on_event=self._post)
        self.tunnel = sharing.Tunnel(on_event=self._post, on_url=self._post_url)

        pad = {"padx": 10, "pady": 6}

        # -- the dashboard -------------------------------------------------
        box = ttk.LabelFrame(self, text="Dashboard", padding=(10, 8))
        box.pack(fill=tk.X, **pad)

        row = ttk.Frame(box)
        row.pack(fill=tk.X)
        self.server_button = ttk.Button(row, text="Start", width=8,
                                        command=self.toggle_server)
        self.server_button.pack(side=tk.LEFT)
        self.server_state = ttk.Label(row, text="stopped", foreground=theme.MUTED)
        self.server_state.pack(side=tk.LEFT, padx=10)

        ttk.Label(row, text="Port").pack(side=tk.LEFT, padx=(20, 4))
        self.port = tk.StringVar(value=str(self.config_obj.get(
            "web_port", sharing.DEFAULT_PORT)))
        ttk.Entry(row, textvariable=self.port, width=7).pack(side=tk.LEFT)

        self.lan = tk.BooleanVar(value=self.config_obj.get("web_lan", False))
        ttk.Checkbutton(box, variable=self.lan, text=(
            "Also reachable from other machines on this network "
            "(needs a firewall rule)")).pack(anchor=tk.W, pady=(8, 0))

        self.autostart = tk.BooleanVar(
            value=self.config_obj.get("web_autostart", False))
        ttk.Checkbutton(box, variable=self.autostart,
                        text="Start the dashboard when this app starts",
                        command=self._save_autostart).pack(anchor=tk.W)

        self.local_links = ttk.Label(box, text="", foreground=theme.ACCENT,
                                     cursor="hand2")
        self.local_links.pack(anchor=tk.W, pady=(8, 0))
        self.local_links.bind("<Button-1>", self._open_local)

        # -- the tunnel ----------------------------------------------------
        box = ttk.LabelFrame(self, text="Public link", padding=(10, 8))
        box.pack(fill=tk.X, **pad)
        ttk.Label(box, wraplength=640, justify=tk.LEFT, foreground=theme.MUTED,
                  text=("A Cloudflare quick tunnel gives friends a link that "
                        "works anywhere, without opening a port on your router. "
                        "The address is unlisted but not password protected, "
                        "and a new one is minted every time it starts.")
                  ).pack(anchor=tk.W, pady=(0, 8))

        row = ttk.Frame(box)
        row.pack(fill=tk.X)
        self.tunnel_button = ttk.Button(row, text="Open link", width=10,
                                        command=self.toggle_tunnel)
        self.tunnel_button.pack(side=tk.LEFT)
        self.tunnel_state = ttk.Label(row, text="closed", foreground=theme.MUTED)
        self.tunnel_state.pack(side=tk.LEFT, padx=10)

        row = ttk.Frame(box)
        row.pack(fill=tk.X, pady=(8, 0))
        self.tunnel_url = ttk.Entry(row, state="readonly", width=52)
        self.tunnel_url.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.copy_button = ttk.Button(row, text="Copy", width=6,
                                      command=self.copy_url, state=tk.DISABLED)
        self.copy_button.pack(side=tk.LEFT, padx=(6, 0))
        self.open_button = ttk.Button(row, text="Open", width=6,
                                      command=self.open_url, state=tk.DISABLED)
        self.open_button.pack(side=tk.LEFT, padx=(4, 0))

        # -- what it has been doing ----------------------------------------
        box = ttk.LabelFrame(self, text="Activity", padding=(10, 8))
        box.pack(fill=tk.BOTH, expand=True, **pad)
        self.log = tk.Text(box, height=7, wrap=tk.WORD, relief=tk.FLAT,
                           background=theme.PANEL, foreground=theme.MUTED,
                           font=("Consolas", 9), highlightthickness=0)
        self.log.pack(fill=tk.BOTH, expand=True)
        self.log.configure(state=tk.DISABLED)

        self._refresh_controls()
        self.after(200, self._drain_events)

    # -- the dashboard ----------------------------------------------------

    def toggle_server(self):
        if self.server.running:
            self.tunnel.stop()      # a tunnel to a stopped server is a dead link
            self.server.stop()
        else:
            try:
                port = int(self.port.get())
            except ValueError:
                messagebox.showerror("Bad port", "The port must be a number.",
                                     parent=self)
                return
            host = sharing.EVERYWHERE if self.lan.get() else sharing.LOCAL_ONLY
            try:
                self.server.start(host, port)
            except Exception as exc:
                messagebox.showerror("Could not start", str(exc), parent=self)
                self._post("could not start: {}".format(exc))
                return
            self._remember(web_port=port, web_lan=self.lan.get())
        self._refresh_controls()

    def _save_autostart(self):
        self._remember(web_autostart=self.autostart.get())

    def _remember(self, **settings):
        for key, value in settings.items():
            self.config_obj[key] = value
        self.config_obj.save()

    def start_if_configured(self):
        """Called once at launch, for people who always want it running."""
        if not self.autostart.get() or self.server.running:
            return
        try:
            self.server.start(
                sharing.EVERYWHERE if self.lan.get() else sharing.LOCAL_ONLY,
                int(self.port.get()))
        except Exception as exc:
            self._post("could not start on launch: {}".format(exc))
        self._refresh_controls()

    def _open_local(self, _event=None):
        urls = self.server.urls()
        if urls:
            webbrowser.open(urls[0])

    # -- the tunnel -------------------------------------------------------

    def toggle_tunnel(self):
        if self.tunnel.running:
            self.tunnel.stop()
        else:
            if not self.server.running:
                messagebox.showinfo(
                    "Start the dashboard first",
                    "The tunnel forwards to the dashboard, so the dashboard "
                    "has to be running.", parent=self)
                return
            try:
                self.tunnel.start(self.server.port)
            except Exception as exc:
                messagebox.showerror("Could not open a tunnel", str(exc),
                                     parent=self)
                self._post(str(exc))
                return
        self._refresh_controls()

    def copy_url(self):
        if self.tunnel.url:
            self.clipboard_clear()
            self.clipboard_append(self.tunnel.url)
            self._post("link copied to the clipboard")

    def open_url(self):
        if self.tunnel.url:
            webbrowser.open(self.tunnel.url)

    # -- plumbing ---------------------------------------------------------

    def _post(self, message):
        """Note a line of progress. Safe to call from any thread."""
        self.events.put(("log", message))

    def _post_url(self, url):
        self.events.put(("url", url))

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._write(payload)
                else:
                    self._show_url(payload)
        except queue.Empty:
            pass
        except Exception:
            log.exception("draining sharing events")
        finally:
            if self.winfo_exists():
                self.after(200, self._drain_events)

    def _write(self, message):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, "{}  {}\n".format(
            datetime.now().strftime("%H:%M:%S"), message))
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)
        self._refresh_controls()

    def _show_url(self, url):
        self.tunnel_url.configure(state=tk.NORMAL)
        self.tunnel_url.delete(0, tk.END)
        self.tunnel_url.insert(0, url)
        self.tunnel_url.configure(state="readonly")
        self._refresh_controls()

    def _refresh_controls(self):
        running = self.server.running
        self.server_button.config(text="Stop" if running else "Start")
        if running:
            where = ("this machine and the local network" if
                     self.server.shared_on_network else "this machine only")
            self.server_state.config(text="running - {}".format(where),
                                     foreground=theme.ACCENT)
            self.local_links.config(text="   ".join(self.server.urls()))
        else:
            self.server_state.config(text="stopped", foreground=theme.MUTED)
            self.local_links.config(text="")

        open_tunnel = self.tunnel.running
        self.tunnel_button.config(text="Close link" if open_tunnel else "Open link")
        self.tunnel_state.config(
            text="open" if self.tunnel.url else
                 ("starting..." if open_tunnel else "closed"),
            foreground=theme.ACCENT if self.tunnel.url else theme.MUTED)
        state = tk.NORMAL if self.tunnel.url else tk.DISABLED
        self.copy_button.config(state=state)
        self.open_button.config(state=state)

    def shutdown(self):
        """Stop both, so closing the app leaves nothing behind."""
        self.tunnel.stop()
        self.server.stop()
