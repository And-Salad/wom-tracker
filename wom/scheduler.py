"""Background thread that fires the update run every six hours, Eastern time."""

import logging
import threading
from datetime import datetime, timedelta, timezone, tzinfo

log = logging.getLogger(__name__)

# Update slots, in US Eastern hours: midnight, 6am, noon, 6pm.
SLOT_HOURS = (0, 6, 12, 18)

# How often the thread wakes to re-check the clock. Short enough that a slot is
# never missed by much, long enough to stay idle.
TICK_SECONDS = 20


class _UsEastern(tzinfo):
    """US Eastern fallback for machines with no IANA time zone database.

    Implements the post-2007 rule: DST runs from 02:00 on the second Sunday in
    March to 02:00 on the first Sunday in November.
    """

    _STD = timedelta(hours=-5)
    _DST = timedelta(hours=-4)

    def tzname(self, dt):
        return "EDT" if self._is_dst(dt) else "EST"

    def utcoffset(self, dt):
        return self._DST if self._is_dst(dt) else self._STD

    def dst(self, dt):
        return timedelta(hours=1) if self._is_dst(dt) else timedelta(0)

    def _is_dst(self, dt):
        if dt is None:
            return False
        naive = dt.replace(tzinfo=None)
        start = self._nth_sunday(naive.year, 3, 2).replace(hour=2)
        end = self._nth_sunday(naive.year, 11, 1).replace(hour=2)
        return start <= naive < end

    @staticmethod
    def _nth_sunday(year, month, nth):
        first = datetime(year, month, 1)
        first_sunday = 1 + (6 - first.weekday()) % 7
        return datetime(year, month, first_sunday + 7 * (nth - 1))


def _eastern():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception:
        log.warning("no IANA time zone data found; using the built-in Eastern rules")
        return _UsEastern()


EASTERN = _eastern()


def previous_slot(now=None):
    """The most recent slot at or before `now`, as an Eastern-time datetime."""
    east = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
    hour = max(h for h in SLOT_HOURS if h <= east.hour)
    return east.replace(hour=hour, minute=0, second=0, microsecond=0)


def next_slot(now=None):
    """The first slot strictly after `now`, as an Eastern-time datetime."""
    east = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
    for hour in SLOT_HOURS:
        if east.hour < hour:
            return east.replace(hour=hour, minute=0, second=0, microsecond=0)
    tomorrow = east + timedelta(days=1)
    return tomorrow.replace(hour=SLOT_HOURS[0], minute=0, second=0, microsecond=0)


class SlotScheduler:
    """Calls `job(trigger)` once per six-hour Eastern slot.

    A slot that passed while the machine was off is caught up as soon as the
    app starts, so a gap never silently swallows an update.
    """

    def __init__(self, config, job, on_state_change=None):
        self.config = config
        self.job = job
        self.on_state_change = on_state_change
        self._thread = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._running_lock = threading.Lock()
        self._busy = False

    # -- lifecycle --------------------------------------------------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="wom-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def poke(self):
        """Wake the thread early, e.g. after the username list changed."""
        self._wake.set()

    @property
    def busy(self):
        return self._busy

    # -- scheduling -------------------------------------------------------

    def next_run_at(self, now=None):
        """When the next run is owed, in Eastern time. The past means overdue."""
        now = now or datetime.now(timezone.utc)
        if self.due(now):
            return previous_slot(now)
        return next_slot(now)

    def due(self, now=None):
        now = now or datetime.now(timezone.utc)
        if not self.config.get("usernames"):
            return False  # nothing to update yet
        last = parse_last_run(self.config.get("last_run", ""))
        if last is None:
            return True  # never run: catch up straight away
        return last < previous_slot(now)

    def run_now(self, trigger="manual"):
        """Fire the job on a worker thread unless one is already running."""
        if not self.claim():
            return False
        threading.Thread(
            target=self._run_job, args=(trigger,), name="wom-update", daemon=True
        ).start()
        return True

    def claim(self):
        """Take the "something is running" flag, or False if it is already taken.

        The flag exists so a scheduled run and a manual one cannot overlap. The
        hosted admin page runs its own jobs on its own threads rather than
        through run_now, and has to take the same flag or the six-hourly slot
        can fire straight into the middle of a manual update - two passes over
        the same players, two sets of API calls, and for summaries two sets of
        paid-for Claude calls.
        """
        with self._running_lock:
            if self._busy:
                return False
            self._busy = True
        self._notify()
        return True

    def release(self):
        """Give the flag back. Always pair this with a successful claim()."""
        with self._running_lock:
            self._busy = False
        self._notify()

    # -- internals --------------------------------------------------------

    def _run_job(self, trigger):
        try:
            self.job(trigger)
            self.config["last_run"] = stamp_now()
            self.config.save()
        except Exception:
            log.exception("scheduled update failed")
        finally:
            self.release()

    def _notify(self):
        if self.on_state_change:
            try:
                self.on_state_change()
            except Exception:
                log.exception("scheduler state callback failed")

    def _loop(self):
        # Give the UI a moment to appear before a catch-up run starts.
        self._wake.wait(3)
        while not self._stop.is_set():
            try:
                if not self._busy and self.due():
                    trigger = "startup" if not self.config.get("last_run") else "scheduled"
                    self.run_now(trigger)
            except Exception:
                log.exception("scheduler tick failed")
            self._wake.wait(TICK_SECONDS)
            self._wake.clear()


def stamp_now():
    """Timestamp for `last_run`: ISO-8601, always with an offset."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_last_run(text):
    """Parse a stored `last_run` into an aware datetime, or None."""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()  # timestamps written before offsets were stored
    return parsed


def describe_schedule():
    return "Updates run every 6 hours, at 12am / 6am / 12pm / 6pm Eastern."
