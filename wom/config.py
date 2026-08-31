"""Persistent settings for the tracker, stored as JSON next to the app."""

import json
import os
import threading

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "wom.db")
LOG_PATH = os.path.join(DATA_DIR, "wom.log")

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
        return self

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with _lock:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            os.replace(tmp, self.path)
        return self

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

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
