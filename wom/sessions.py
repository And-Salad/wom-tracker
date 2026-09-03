"""Which stretch of time a recorded gain was actually earned in.

The hiscores do not move while somebody is logged in, so a gain reaches us as
a single jump at the first reading after they log out. Everything downstream
then treats that jump as a *moment*, and attributes three hours of training to
the ten minutes we happened to notice it in.

Dink reports both ends of a session as they happen (see web/hooks.py), which
turns that moment back into a span. Not every account runs it, and no account
ran it before today, so this has to answer for both: use what Dink said where
Dink said something, and otherwise fall back to exactly what the app did
before - the gain sits between the reading that saw it and the reading before.

Nothing here decides what to *do* with a span. It only says what the span was,
and how much of it is measured rather than assumed.
"""

import logging

from .util import parse_api_time

log = logging.getLogger(__name__)

# A login we never saw a logout for stops being evidence eventually. Someone
# who leaves a client running overnight would otherwise turn a ten minute gain
# into a sixteen hour session and smear it across two days.
MAX_SESSION_HOURS = 16

# What we know about a span's edges.
MEASURED = "measured"      # Dink told us
INFERRED = "inferred"      # our polling bracketed it


class Span:
    """When a gain was earned, and how well each end of that is known."""

    __slots__ = ("start", "end", "start_from", "end_from")

    def __init__(self, start, end, start_from, end_from):
        self.start = start
        self.end = end
        self.start_from = start_from
        self.end_from = end_from

    @property
    def measured(self):
        """True when both ends came from the plugin rather than our polling."""
        return self.start_from == MEASURED and self.end_from == MEASURED

    @property
    def seconds(self):
        return (self.end - self.start).total_seconds()

    def __eq__(self, other):
        return (isinstance(other, Span)
                and (self.start, self.end, self.start_from, self.end_from)
                == (other.start, other.end, other.start_from, other.end_from))

    def __repr__(self):
        return "Span({}..{}, {}/{})".format(
            self.start, self.end, self.start_from, self.end_from)


def resolve(previous_at, reading_at, events, max_hours=MAX_SESSION_HOURS):
    """The span a gain seen at `reading_at` was earned in.

    `previous_at` is the reading before it - the earliest moment the gain
    could have started under polling alone, and the fallback for the start.
    `events` is that player's logins and logouts as (kind, when) pairs, in
    any order; only the ones that bear on this gain are used.

    The start is, in order of preference:

      1. the first login between the two readings. If somebody logged in and
         out twice inside one polling interval, this reaches back to the first
         of them, because the gain covers both and the later login alone would
         under-count the time.
      2. a login from *before* the previous reading that was never closed -
         the long session case. Someone training for three hours across four
         of our readings logged in before all of them, and that login is the
         only record of when they started.
      3. `previous_at`, which is what the app has always used.

    The end is the last logout between the two readings, or `reading_at`.
    A logout is exact where our reading is only an upper bound, but the
    difference is minutes and the fallback is never wrong, only vague.
    """
    logins, logouts = [], []
    for kind, when in events:
        if when is None:
            continue
        (logins if kind == "login" else logouts).append(when)
    logins.sort()
    logouts.sort()

    inside = [w for w in logouts if previous_at < w <= reading_at]
    end, end_from = (inside[-1], MEASURED) if inside else (reading_at, INFERRED)

    start, start_from = _opening(previous_at, end, logins, logouts)
    # A start earlier than the previous reading is the point, not a mistake:
    # the readings in between showed no gain because the hiscores were frozen
    # while the session ran.
    if start is None or start >= end:
        return Span(previous_at, end, INFERRED, end_from)
    if (end - start).total_seconds() > max_hours * 3600:
        # A login this old is a client left running, not a session.
        log.debug("session: ignoring a login %s before %s", start, end)
        return Span(previous_at, end, INFERRED, end_from)
    return Span(start, end, start_from, end_from)


def _opening(previous_at, end, logins, logouts):
    """The login that opened the session this gain belongs to, and its source."""
    started = [w for w in logins if previous_at < w <= end]
    if started:
        return started[0], MEASURED

    # Nothing new since the last reading, so look for a session already open
    # then: the most recent login with no logout between it and that reading.
    # This is the case the whole exercise exists for - four hours of training
    # crosses several of our readings without moving any of them, and that
    # login is the only record of when it began.
    earlier = [w for w in logins if w <= previous_at]
    if earlier:
        opened = earlier[-1]
        if not any(opened < w <= previous_at for w in logouts):
            return opened, MEASURED
    return None, INFERRED


def events_for(database, username, since, until):
    """One player's logins and logouts over a window, as resolve() wants them.

    A window rather than everything: resolving one gain needs the events
    around it, and a year of logins to pick two from is a query nobody needs
    to run.
    """
    rows = database.session_events(username, since=since, until=until)
    return [(row["kind"], parse_api_time(row["received_at"])) for row in rows]
