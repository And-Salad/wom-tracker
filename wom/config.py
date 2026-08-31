"""Persistent settings for the tracker, stored as JSON next to the app."""

import json
import os
import threading

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "wom.db")
LOG_PATH = os.path.join(DATA_DIR, "wom.log")


def log_path_for(role):
    """Where a given entry point writes its log.

    RotatingFileHandler rotates by renaming the open file, which Windows
    refuses while another process holds it. The desktop app keeps its log open
    all session, so a CLI run sharing that file fails to roll over and grows
    past its cap. One file per role, and they never collide.
    """
    if not role or role == "app":
        return LOG_PATH
    return os.path.join(DATA_DIR, "wom-{}.log".format(role))

DEFAULTS = {
    # Player names to keep updated, in display order.
    "usernames": [],
    # Optional Wise Old Man API key. Without one the API allows 20 req/min.
    "api_key": "",
    # Contact string sent in the User-Agent header, as the API docs ask for.
    "user_agent_contact": "",
    # Claude API key for the written summaries. Left blank, the Anthropic SDK
    # falls back to ANTHROPIC_API_KEY or a logged-in profile.
    "anthropic_api_key": "",
    # Summaries cost money, so they are opt-in. Which ones get written is
    # decided by the calendar in wom/summaries.py, not configured here.
    "summaries_enabled": False,
    "summary_model": "claude-sonnet-5",
    "summary_effort": "low",
    # Chart colour overrides, {lowercase username: "#rrggbb"}. Anything not
    # listed falls back to the default palette by list position.
    "player_colors": {},
    # Hide to the system tray instead of quitting when the window is closed.
    "minimize_to_tray": True,
    # The dashboard, run from the Sharing tab rather than a console window.
    "web_port": 8000,
    "web_lan": False,
    "web_autostart": False,
    # ISO timestamp of the last completed update run; managed by the scheduler.
    # Runs happen on a fixed six-hour Eastern schedule, so there is nothing
    # here to configure - see wom/scheduler.py.
    "last_run": "",
}

_lock = threading.Lock()


class Config:
    """Dict-like settings object that writes through to disk on save()."""

    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self._data = dict(DEFAULTS)
        # Keys this instance has actually been asked to change. save() writes
        # only these, so a long-lived object cannot revert a key some other
        # process wrote in the meantime.
        self._dirty = set()
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
        except (OSError, ValueError):
            stored = {}
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in stored.items() if k in DEFAULTS})
        self._data = merged
        self._dirty.clear()
        return self

    def save(self):
        """Write our changes without discarding anyone else's.

        This object holds a snapshot taken when it was built, and the app keeps
        one for a whole session. A blind write of that snapshot would undo
        whatever another process has written since - a `--update` run's
        `last_run` being the one that bites, because reverting it makes the
        scheduler think a run is overdue and fire a duplicate pass. So re-read
        the file under the lock and lay only the changed keys over it.
        """
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with _lock:
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    on_disk = json.load(fh)
                if not isinstance(on_disk, dict):
                    on_disk = {}
            except (OSError, ValueError):
                on_disk = {}
            merged = dict(DEFAULTS)
            merged.update({k: v for k, v in on_disk.items() if k in DEFAULTS})
            merged.update({k: self._data[k] for k in self._dirty})
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(merged, fh, indent=2)
            os.replace(tmp, self.path)
            # Adopt what we just wrote, so this object matches the file again.
            self._data = merged
            self._dirty.clear()
        return self

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value
        self._dirty.add(key)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def as_dict(self):
        return dict(self._data)


def normalise_usernames(names):
    """Trim, drop blanks, and de-duplicate case-insensitively, keeping order."""
    seen = set()
    out = []
    for raw in names:
        name = " ".join(str(raw).split())
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out
