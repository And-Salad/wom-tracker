"""How many people are looking at the site right now.

The header used to count the roster, which is a number that changes about
once a month and was already on the Players page twice over. What a reader
cannot get anywhere else is whether anybody else is here - so this counts
that instead.

Nobody is asked to say they are here. Every open tab already polls
/api/status on a timer, and every page load is a request too, so the traffic
that keeps the header true is the same traffic this counts: whoever has been
seen inside the window is watching, and whoever has not is not. A tab that
goes to the background stops polling on purpose (see static/live.js), so it
drops out a few minutes after it is looked away from, which is the answer we
want rather than a bug.

In memory and per process, like the budgets in limits.py and for the same
reason - two apps in one process must not share a count. It also means the
number resets on a deploy, which is honest enough: every open page asks again
within a minute.
"""

import hashlib
import threading
import time

# Long enough that a tab polling once a minute is never missed even after the
# poll has backed off a step, short enough that somebody who closed the tab is
# gone within about the time it takes to notice.
WINDOW = 300

# Above this many keys in the window, prune the ones that have aged out. The
# same shape as Budget's cap, and here for the same reason: a caller rotating
# addresses would otherwise grow this dict without bound.
CAP = 1024


def fingerprint(address, agent):
    """One viewer, as far as we can tell them apart.

    The address alone would count a household or an office as one reader, so
    the browser's own description of itself joins it - two phones on one
    connection are usually two strings. It is a hash rather than the parts
    because nothing here ever needs to read them back: this is a count, and
    keeping a list of who was on the site would be a different thing entirely
    from keeping how many.
    """
    seed = "{}\n{}".format(address or "", (agent or "")[:200])
    return hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:16]


class Viewers:
    """Distinct fingerprints seen in the last WINDOW seconds."""

    def __init__(self, window=WINDOW):
        self.window = window
        self._seen = {}
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self._seen.clear()

    def saw(self, key, now=None):
        """Note that this viewer is here, and answer the count including them."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            self._seen[key] = moment
            if len(self._seen) > CAP:
                self._prune(moment)
            return self._live(moment)

    def count(self, now=None):
        moment = time.monotonic() if now is None else now
        with self._lock:
            return self._live(moment)

    # -- internals, both called with the lock held -------------------------

    def _live(self, now):
        return sum(1 for last in self._seen.values() if now - last < self.window)

    def _prune(self, now):
        self._seen = {key: last for key, last in self._seen.items()
                      if now - last < self.window}
