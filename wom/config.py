"""Persistent settings for the tracker, stored as JSON next to the app."""

import json
import os
import threading

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# A container mounts its volume somewhere other than the source tree, so the
# whole data directory can be pointed elsewhere. Everything derives from it.
#
# Resolved on each call rather than bound to a module constant at import time.
# Constants were read the first time anything imported this module, which made
# the location of the database, the config and the prompts a property of
# import order: a test that wanted a directory of its own had to set the
# environment before the first import of anything at all, and could never
# change its mind afterwards. Nothing here is hot enough for an os.environ
# lookup and a join to matter.
def data_dir():
    """The directory holding the database, the config, the prompts and the logs."""
    return os.environ.get("WOM_DATA_DIR") or os.path.join(APP_DIR, "data")


def config_path():
    return os.path.join(data_dir(), "config.json")


def db_path():
    return os.path.join(data_dir(), "wom.db")


def log_path_for(role):
    """Where a given entry point writes its log.

    RotatingFileHandler rotates by renaming the open file, which Windows
    refuses while another process holds it. The server keeps its log open for
    as long as it runs, so a CLI job sharing that file would fail to roll over
    and grow past its cap. One file per role, and they never collide.
    """
    return os.path.join(data_dir(), "wom-{}.log".format(role or "app"))

DEFAULTS = {
    # Player names to keep updated, in display order.
    "usernames": [],
    # The local day history was last thinned on, in the zone configured below.
    # Anything the app writes has to be declared here: save() keeps only the
    # keys it knows, so a key that
    # is not listed is written and then dropped on the next read - which had
    # this compacting, and vacuuming, on every ten-minute run.
    "last_compact": "",
    # Optional Wise Old Man API key. Without one the API allows 20 req/min,
    # which is ample here - a run of six players is twelve requests every ten
    # minutes. A key that the API rejects is worse than none: it answers 403
    # to every request, so the client drops it and carries on without.
    "api_key": "",
    # The clock everything dated runs on: day boundaries, the calendar, and
    # the window each round-up is written for. An IANA name, so daylight
    # saving follows that place rather than this machine.
    "timezone": "America/New_York",
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
    # Per-player secrets for Dink's metadata webhook, {lowercase username:
    # token}. The plugin cannot send a header, so the URL is the whole
    # credential - which is why each player gets their own: it says who sent
    # a request without trusting the name in the body, and one leak is
    # revoked alone. Issued and revoked from the admin page.
    "dink_tokens": {},
    # Set when the data endpoints' tripwire latches, cleared from the admin
    # page. Here rather than in memory so a restart does not resume serving.
    "api_tripped_at": "",
    "api_tripped_by": "",
    # ISO timestamp of the last completed update run; managed by the scheduler.
    # Runs happen on a fixed interval - every SLOT_MINUTES, on the wall-clock
    # boundary - so there is nothing here to configure. See wom/scheduler.py.
    "last_run": "",
}

# Settings a hosted deployment supplies as environment variables rather than
# in config.json: on Fly these arrive from `fly secrets set`, which keeps them
# out of the volume and out of the admin page's reach.
ENV_KEYS = {
    "api_key": "WOM_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
}

_lock = threading.Lock()


def env_value(key):
    """The environment's value for a setting, or None if it does not set it."""
    name = ENV_KEYS.get(key)
    value = os.environ.get(name) if name else None
    return value.strip() if value and value.strip() else None


class Config:
    """Dict-like settings object that writes through to disk on save()."""

    def __init__(self, path=None):
        self.path = path or config_path()
        self._data = dict(DEFAULTS)
        # Keys this instance has actually been asked to change. save() writes
        # only these, so a long-lived object cannot revert a key some other
        # process wrote in the meantime.
        self._dirty = set()
        self._from_env = set()
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                stored = json.load(fh)
        except (OSError, ValueError):
            stored = {}
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in stored.items() if k in DEFAULTS})
        # The environment wins, and never becomes dirty, so a save can never
        # write a secret back out to the config file.
        self._from_env = set()
        for key in ENV_KEYS:
            value = env_value(key)
            if value is not None:
                merged[key] = value
                self._from_env.add(key)
        self._data = merged
        self._dirty.clear()
        return self

    def is_from_env(self, key):
        """True when this setting comes from the environment and is read-only."""
        return key in getattr(self, "_from_env", ())

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
                with open(self.path, encoding="utf-8") as fh:
                    on_disk = json.load(fh)
                if not isinstance(on_disk, dict):
                    on_disk = {}
            except (OSError, ValueError):
                on_disk = {}
            merged = dict(DEFAULTS)
            merged.update({k: v for k, v in on_disk.items() if k in DEFAULTS})
            merged.update({k: self._data[k] for k in self._dirty
                           if not self.is_from_env(k)})
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
