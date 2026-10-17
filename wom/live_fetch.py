"""Reading a player's hiscores the moment Dink says they logged out.

The hiscores change at logout and at no other time, so that is the moment a
reading is worth taking - and the hourly run can only ever be up to an hour
late to it. Dink reports the logout as it happens, so anyone running it gets
their session on the dashboard within seconds of leaving the game, and the
hourly run becomes the floor for everyone else.

One worker thread and a queue. The webhook only enqueues: Dink is waiting on
the response, and a hiscores lookup is a second on a good day. Logouts for
the same player arriving together - a world hop and a logout, Dink retrying -
collapse into one reading.

Not behind the scheduler's "something is running" flag. A one-player reading
must not be lost because the hourly run happens to be going; the database is
safe across threads, and the hiscores client is shared, so the two cannot
between them ask Jagex faster than its pacing allows.
"""

import logging
import threading
import time

from . import changes, hiscores, renames, updater
from .config import Config

log = logging.getLogger(__name__)

# How long after a logout to ask. The logout screen already sees the new
# experience (docs/notes.md, "When a session actually happened"), but the
# hiscores are a different system and a few seconds' grace costs nothing.
DELAY_SECONDS = 15

# When the first reading finds nothing changed, one more try this much
# later. A session too small to move the hiscores at all reads the same
# both times, and the hourly run is there for anything slower than this.
RETRY_SECONDS = 120


class LogoutFetcher:
    def __init__(self, database, reader=None, config_factory=Config,
                 delay=DELAY_SECONDS, retry=RETRY_SECONDS):
        self.database = database
        self.reader = reader or hiscores.shared()
        self.config_factory = config_factory
        self.delay = delay
        self.retry = retry
        self._pending = {}  # username -> (due, attempt, reported name)
        self._wake = threading.Condition()
        self._thread = None
        self._stop = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop = False
        self._thread = threading.Thread(target=self._loop, name="wom-logouts",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self):
        with self._wake:
            self._stop = True
            self._wake.notify()

    def enqueue(self, username, reported=None):
        """Ask for a reading of `username` shortly. Repeats coalesce."""
        key = username.lower()
        with self._wake:
            due = time.monotonic() + self.delay
            held = self._pending.get(key)
            # A later logout keeps the earlier one's slot: the reading taken
            # then sees both.
            if held is None or held[1] > 0:
                self._pending[key] = (due, 0, reported)
            self._wake.notify()

    def _loop(self):
        while True:
            with self._wake:
                if self._stop:
                    return
                now = time.monotonic()
                ready = [(name, entry) for name, entry in self._pending.items()
                         if entry[0] <= now]
                if not ready:
                    soonest = min((e[0] for e in self._pending.values()),
                                  default=None)
                    self._wake.wait(None if soonest is None
                                    else max(0.1, soonest - now))
                    continue
                for name, _entry in ready:
                    del self._pending[name]
            for name, (_due, attempt, reported) in ready:
                try:
                    again = self.process(name, reported, attempt)
                except Exception:
                    log.exception("reading %s after logout failed", name)
                    again = False
                if again:
                    with self._wake:
                        self._pending.setdefault(name, (
                            time.monotonic() + self.retry, attempt + 1, None))

    def process(self, username, reported=None, attempt=0):
        """Take the reading. Returns True if it should be tried once more."""
        if reported:
            username = renames.check_reported_name(
                self.database, self.config_factory(), self.reader, username,
                reported)
        player = self.database.player_by_username(username)
        if player is None:
            # Not introduced yet; the next hourly run does that.
            return False
        try:
            outcome = updater.read_hiscores(self.reader, self.database, player)
        except hiscores.HiscoresError as exc:
            log.info("no reading for %s after logout: %s", username, exc)
            return False
        updater.place_sessions(self.database, [username])
        changes.bump("logout reading")
        log.info("read %s's hiscores after logout%s", username,
                 "" if outcome["moved"] else " (nothing had moved yet)")
        return not outcome["moved"] and attempt == 0
