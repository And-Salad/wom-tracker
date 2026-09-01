"""Background thread that fires the update run on a fixed interval.

Slots sit on wall-clock boundaries in the configured time zone - see zone()
below, which reads the setting the admin page writes. Nothing here is tied to
one place: "Eastern" appears only as the default, and as the fallback rules
for a machine with no time zone database at all.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone, tzinfo

log = logging.getLogger(__name__)

# Minutes between update runs. Ten is a long way inside what Wise Old Man
# allows - a run is twelve requests against a limit of twenty a minute, spaced
# out to about forty seconds, so this is a seven per cent duty cycle - and it
# cuts the blind spot a daily figure is measured across from six hours to ten
# minutes. Anything under a minute is refused by the API's own per-player
# cooldown, which holds a repeat update open rather than declining it.
#
# Has to divide 60. Slots are found by rounding the minute hand down, so a
# value that does not go evenly into an hour would put the last slot of one
# hour a short step from the first of the next, and wants_achievements() -
# which asks whether a slot landed on the hour - could stop matching at all.
SLOT_MINUTES = 10

# Achievements move rarely and cost a request per player, so they are not
# fetched on every run. On the hour is often enough and halves the traffic.
ACHIEVEMENT_MINUTE = 0

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


# The clock everything dated runs on: days, the calendar, and the window each
# round-up is written for. Configurable, because "midnight" is a local idea and
# a group in Perth should not have its days end at nine in the morning.
DEFAULT_ZONE = "America/New_York"

_zone = None


def zone():
    """The configured time zone, resolved once and remembered.

    Resolved lazily rather than at import: the setting lives in a file that
    may not exist yet when this module is first imported, and a long-running
    server has to notice when it changes. The admin page calls forget_zone()
    after a save, which is what makes the change take effect without a
    restart.
    """
    global _zone
    if _zone is None:
        from .config import Config
        _zone = zone_named((Config().get("timezone") or "").strip() or DEFAULT_ZONE)
    return _zone


def forget_zone():
    """Re-read the setting on the next call."""
    global _zone
    _zone = None


def zone_named(name):
    """One named zone, or the nearest honest thing to it.

    Windows ships no IANA database, so `tzdata` is a requirement there. Where
    it is missing anyway, Eastern still works from the rules built in below;
    anything else falls back to UTC rather than silently pretending, since a
    wrong offset would put every day boundary in the wrong place.
    """
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        if name == DEFAULT_ZONE:
            log.warning("no time zone database found; using the built-in "
                        "Eastern rules")
            return _UsEastern()
        log.error("cannot load the time zone %r - falling back to UTC. Install "
                  "tzdata, or set one this machine knows.", name)
        return timezone.utc


def previous_slot(now=None, minutes=SLOT_MINUTES):
    """The most recent slot at or before `now`, in the configured zone.

    Slots sit on wall-clock boundaries rather than counting from whenever the
    process happened to start, so they stay predictable across a restart and a
    missed one is still recognisably missed.

    `minutes` has to divide 60: the boundary is found by rounding the minute
    hand down, which only lines up with the hour for a factor of it. See
    SLOT_MINUTES.
    """
    east = (now or datetime.now(timezone.utc)).astimezone(zone())
    return east.replace(minute=east.minute - east.minute % minutes,
                        second=0, microsecond=0)


def next_slot(now=None, minutes=SLOT_MINUTES):
    """The first slot strictly after `now`, in the configured zone."""
    return previous_slot(now, minutes) + timedelta(minutes=minutes)


def wants_achievements(now=None, minutes=SLOT_MINUTES):
    """Whether this run should also re-read everyone's milestones."""
    return previous_slot(now, minutes).minute == ACHIEVEMENT_MINUTE


class SlotScheduler:
    """Calls `job(trigger)` once per slot - every SLOT_MINUTES, on the boundary.

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

    # -- scheduling -------------------------------------------------------

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
        through run_now, and has to take the same flag or a scheduled slot
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
