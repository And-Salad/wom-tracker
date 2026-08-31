"""Who is calling, how often they may, and the switch that stops everything.

Two different jobs. The budgets say how much one caller may have, and refuse
politely past that. The tripwire is a last resort: it watches the total across
everyone and, once that can only be a machine, latches until a person clears
it. Latching is deliberate - the point is that the app stops rather than
quietly running up a bill - but it does mean whoever trips it takes the data
offline for everyone until then, so the threshold sits far above anything a
person can produce.
"""

import logging
import threading
import time
from datetime import datetime, timezone

from flask import request

log = logging.getLogger(__name__)

# Set by the proxy in front of us and not passed through from the client, so
# these can be believed where a bare X-Forwarded-For cannot.
CLIENT_IP_HEADERS = ("Fly-Client-IP", "CF-Connecting-IP", "True-Client-IP")


def client_address():
    """The caller's address as well as we can know it, and where it came from.

    Behind a proxy `remote_addr` is the proxy, which would put every visitor in
    one bucket: six bad sign-ins from anyone would lock out everyone. Returns
    (address, source) so a log line can say which header answered.
    """
    for header in CLIENT_IP_HEADERS:
        value = (request.headers.get(header) or "").strip()
        if value:
            return value, header
    # Leftmost is the original client. Waitress strips X-Forwarded-* unless it
    # is told to trust a proxy, so this is a fallback for other deployments
    # rather than the path taken on Fly.
    forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip(), "X-Forwarded-For"
    return request.remote_addr or "?", "remote_addr"


class Budget:
    """Calls allowed per key over a window."""

    def __init__(self, allowance, window):
        self.allowance = allowance
        self.window = window
        self._seen = {}
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self._seen.clear()

    def check(self, key):
        """Seconds to wait, or 0 if a call under this key may go ahead.

        Only a call that goes ahead is recorded, so a request refused by
        another bucket does not eat this one's allowance.
        """
        now = time.monotonic()
        with self._lock:
            calls = [t for t in self._seen.get(key, ()) if now - t < self.window]
            self._seen[key] = calls
            if len(calls) >= self.allowance:
                return max(1, int(self.window - (now - calls[0])))
            return 0

    def record(self, key):
        now = time.monotonic()
        with self._lock:
            calls = [t for t in self._seen.get(key, ()) if now - t < self.window]
            calls.append(now)
            self._seen[key] = calls
            if len(self._seen) > 1024:
                self._seen = {k: v for k, v in self._seen.items() if v}


class Tripwire:
    """Counts calls across everyone and latches once the total can only be a bot.

    Not a rate limiter: it does not slow anything down, it stops. Once tripped
    the data endpoints refuse until someone clears it from the admin page, so
    an abusive run costs one burst rather than hours of billed traffic.
    """

    def __init__(self, allowance, window):
        self.allowance = allowance
        self.window = window
        self._calls = []
        self._lock = threading.Lock()
        self.tripped_at = None
        self.tripped_by = None
        self.seen_in_window = 0

    def reset(self):
        with self._lock:
            self._calls = []
            self.tripped_at = None
            self.tripped_by = None

    @property
    def tripped(self):
        return self.tripped_at is not None

    def note(self, address):
        """Record a call. Returns True if this one tripped the wire."""
        if self.tripped:
            return False
        now = time.monotonic()
        with self._lock:
            self._calls = [t for t in self._calls if now - t < self.window]
            self._calls.append(now)
            self.seen_in_window = len(self._calls)
            if len(self._calls) < self.allowance:
                return False
            self.tripped_at = datetime.now(timezone.utc)
            self.tripped_by = address
        log.error("tripwire: %d data requests in %ds, last from %s - refusing "
                  "until an admin resumes", self.allowance, self.window, address)
        return True

    def status(self):
        return {
            "tripped": self.tripped,
            "since": self.tripped_at.isoformat() if self.tripped_at else None,
            "by": self.tripped_by,
            "allowance": self.allowance,
            "window": self.window,
            "recent": self.seen_in_window,
        }


# A full export is about 5 MB of egress and walks every stored reading, on a
# machine that also runs the schedule. Five per viewer per six hours, twenty a
# day across everyone as the backstop: roughly 100 MB a day at today's size.
EXPORTS_PER_ADDRESS = 5
EXPORT_ADDRESS_WINDOW = 6 * 3600
EXPORTS_PER_DAY = 20
EXPORT_DAY_WINDOW = 24 * 3600

# The chart and player endpoints. A heavy human session is a couple of hundred
# calls in five minutes; one scripted client managed 8,400 in the same time.
# The per-address ceiling sits well above the first and far below the second,
# and the tripwire above anything a few people could produce between them.
API_PER_ADDRESS = 600
API_ADDRESS_WINDOW = 300
API_TRIP_TOTAL = 3000
API_TRIP_WINDOW = 300

_EVERYONE = "*"


class Limits:
    """Every budget for one application.

    An instance per app rather than module-level state: two apps in a process
    (which is every test run) would otherwise share one another's counters,
    and the factory had to reset globals on the way past.
    """

    def __init__(self,
                 exports_per_address=EXPORTS_PER_ADDRESS,
                 exports_per_day=EXPORTS_PER_DAY,
                 api_per_address=API_PER_ADDRESS,
                 api_trip_total=API_TRIP_TOTAL):
        self.exports_per_address = exports_per_address
        self.exports_per_day = exports_per_day
        self.export_per_address = Budget(exports_per_address, EXPORT_ADDRESS_WINDOW)
        self.export_overall = Budget(exports_per_day, EXPORT_DAY_WINDOW)
        self.api_per_address = Budget(api_per_address, API_ADDRESS_WINDOW)
        self.api_tripwire = Tripwire(api_trip_total, API_TRIP_WINDOW)

    address = staticmethod(client_address)

    def export_allowed(self, address, is_admin):
        """(seconds_to_wait, which_limit). Admin is not budgeted."""
        if is_admin:
            return 0, None
        waiting = self.export_per_address.check(address)
        if waiting:
            return waiting, "address"
        waiting = self.export_overall.check(_EVERYONE)
        if waiting:
            return waiting, "everyone"
        self.export_per_address.record(address)
        self.export_overall.record(_EVERYONE)
        return 0, None
