"""Figures remembered between requests, for as long as the data behind them holds.

The Overview asks for seven charts at once, one request each, and asks again
every time a box in the sidebar is ticked. Each request used to start from
nothing: the group tiles and the standings added up the same gains for the
same players, the skill and boss cards read them again, and ticking an eighth
account recomputed the seven already on screen. None of it had changed - the
readings only move when an update lands, every ten minutes.

So a player's figures for a window are kept here, keyed by what they were
asked for and by a `version` - see Database.data_version - that moves whenever
anything they are read from could have. They also expire after a couple of
minutes whatever the version says, because the version is a handful of cheap
reads rather than a proof: a write that lands between them is caught by the
clock instead.
"""

import threading
import time
from collections import OrderedDict

# How long a remembered figure is trusted without the version moving. Long
# enough to cover somebody clicking through the sidebar; short enough that a
# write the version missed is on screen within a couple of minutes anyway.
TTL_SECONDS = 120

# Entries, not bytes. One is a player's gains for one kind, or one metric's
# history, over one window - a few kilobytes at most. A few thousand is every
# combination a group browses in a day, with room over.
MAX_ENTRIES = 4096


class Memo:
    """A small thread-safe cache, emptied whenever the data version moves."""

    def __init__(self, ttl=TTL_SECONDS, size=MAX_ENTRIES, clock=time.monotonic):
        self.ttl = ttl
        self.size = size
        self.clock = clock
        self._entries = OrderedDict()
        self._version = None
        self._lock = threading.Lock()

    def get(self, version, key, compute):
        """The remembered value for `key` under `version`, computed if need be.

        Computed outside the lock: two requests asking for the same figure at
        once both work it out, which is what happened before any of this, and
        is better than one waiting on the other's database reads.
        """
        now = self.clock()
        with self._lock:
            if version != self._version:
                self._entries.clear()
                self._version = version
            found = self._entries.get(key)
            if found is not None and now - found[0] < self.ttl:
                self._entries.move_to_end(key)
                return found[1]
        value = compute()
        with self._lock:
            if version == self._version:
                self._entries[key] = (now, value)
                self._entries.move_to_end(key)
                while len(self._entries) > self.size:
                    self._entries.popitem(last=False)
        return value
