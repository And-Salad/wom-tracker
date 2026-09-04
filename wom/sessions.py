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
from datetime import timedelta, timezone

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
    return [(row["kind"], parse_api_time(row["happened_at"])) for row in rows]


def boundary_in(span, zone):
    """The local midnight inside a span, or None if it does not cross one.

    At most one can exist: MAX_SESSION_HOURS is under a day. Week, month,
    quarter and year boundaries are local midnights too, so this one answer
    covers every window the app draws.
    """
    local = span.start.astimezone(zone)
    midnight = (local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    crossing = midnight.astimezone(timezone.utc)
    return crossing if span.start < crossing < span.end else None


def share_before(span, boundary):
    """How much of the span sits before the boundary, as a fraction."""
    total = (span.end - span.start).total_seconds()
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, (boundary - span.start).total_seconds() / total))


def interpolate(opened, closed, fraction, whole=False):
    """The state part-way through a session, as {metric: value}.

    Linear in time, which assumes an even rate across the session. That is
    wrong for somebody who logs in, idles an hour and then trains, and it is
    still far closer than crediting the whole session to the minute it ended.

    Only metrics that moved are returned - anything unchanged is already
    carried forward by `state_at` and writing it again would be a row saying
    nothing. `whole` rounds, for the kinds counted in whole numbers: a
    boundary is no place to invent two thirds of a boss kill.
    """
    out = {}
    for metric, end in closed.items():
        if end is None:
            continue
        start = opened.get(metric)
        if start is None or end <= start:
            continue
        value = start + (end - start) * fraction
        if whole:
            value = float(round(value))
        if value > start:
            out[metric] = value
    return out


# Which kinds are counted in whole numbers.
WHOLE_KINDS = ("boss", "activity")
KINDS = ("skill",) + WHOLE_KINDS


def attribute(database, zone, player, since, max_hours=MAX_SESSION_HOURS):
    """Credit each known session's gain to the time it was earned in.

    Walks the account's readings from `since`, and wherever a gain sits in a
    session whose edges Dink told us about, and that session crossed a local
    midnight, writes what the account had earned by that midnight.

    Readings we know nothing about are left alone, which is the whole of the
    fallback: an account with no session events comes out of this untouched
    and every number about it stays exactly what it was.

    Existing interpolations in the window are cleared first, so a late
    logout - or a change to the rule - corrects rather than accumulates.

    That clearing is also the floor on what may be written. A span reaches
    back as far as its login, which can be up to `max_hours` before the first
    reading in the window, and anything written below `since` would be outside
    the only thing that can ever take it back: the next run clears from
    `since` and never looks lower. Those rows would be permanent, and a
    session still open when they were written would leave them interpolated
    towards an end that later moved. So nothing is written below `since` - see
    _ramp. Nothing is lost by that either, because the readings under the
    floor were inside the window on earlier runs and were corrected then; a
    three day window is hundreds of runs wide.
    """
    username = player["username"]
    if not database.session_events(username, limit=1):
        return 0

    database.clear_derived_state(player["id"], since)
    floor = parse_api_time(since)
    readings = [r["captured_at"] for r in database.query(
        "SELECT captured_at FROM snapshots WHERE player_id=? AND captured_at>=?"
        " AND COALESCE(origin,'poll') <> 'derived' ORDER BY captured_at",
        (player["id"], since))]

    written = 0
    # strict=False deliberately: this is a list zipped against itself
    # offset by one, so the shorter tail is the point of the pairing.
    for previous_at, reading_at in zip(readings, readings[1:], strict=False):
        # Any metric moving means something was earned in that gap. Gating on
        # `overall` alone would miss a reading that only moved a boss count.
        moved = database.query_one(
            "SELECT 1 FROM metrics WHERE player_id=? AND captured_at=? LIMIT 1",
            (player["id"], reading_at))
        if moved is None:
            continue
        written += _split_one(database, zone, player, previous_at, reading_at,
                              max_hours, floor)
    return written


# How much earlier than our reading a session has to have ended before it is
# worth moving. Under this it is the same moment for every purpose.
LATE_BY_SECONDS = 60


def _split_one(database, zone, player, previous_at, reading_at, max_hours, floor):
    """Put one gain where it was earned, if we know better than the reading.

    Three corrections, and the second is the common one.

    A session that crossed a local midnight is divided at it, so the evening
    half counts for the evening.

    A session that *ended* before our reading found it is moved back to when
    it ended. Somebody who logged out at 23:55 and was noticed at 00:10 had
    their whole evening credited to the next day - no midnight falls inside
    that session, so dividing it was never going to help.

    And a session we know the start of is drawn out across the readings it
    ran through, so a chart of it slopes rather than jumps. That one changes
    no total and is only about how the hours in between are drawn; see _ramp.
    """
    before = parse_api_time(previous_at) - timedelta(hours=max_hours)
    span = resolve(parse_api_time(previous_at), parse_api_time(reading_at),
                   events_for(database, player["username"],
                              before.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), reading_at),
                   max_hours=max_hours)
    if span.start_from == INFERRED and span.end_from == INFERRED:
        return 0            # nothing was measured; leave it exactly as it was

    written = 0
    ended = parse_api_time(reading_at) - span.end
    if span.end_from == MEASURED and ended.total_seconds() > LATE_BY_SECONDS:
        # The whole gain, at the moment the session actually closed.
        written += _place(database, player, previous_at, reading_at, span.end,
                          1.0, zone)

    crossing = boundary_in(span, zone)
    # The midnight can fall below the floor too: a session that opened before
    # the window did carries its first midnight in with it. The end above
    # cannot - it is later than `previous_at`, which is inside the window by
    # construction - so only this one is checked.
    if crossing is not None and crossing >= floor:
        written += _place(database, player, previous_at, reading_at, crossing,
                          share_before(span, crossing), zone)

    return written + _ramp(database, player, previous_at, reading_at, span, floor)


def _ramp(database, player, previous_at, reading_at, span, floor):
    """Spread a measured session's experience across the time it ran for.

    Everything above places the gain at one moment. That is right for the
    ledger and wrong for a chart: the readings taken during a session all
    repeat the value from before it, because the hiscores are frozen while
    somebody is logged in, so a line drawn through them lies flat for three
    hours and then goes vertical. What was earned steadily reads as a jump.

    So at each reading the session ran past, we write what the account had
    reached by then. Linear in time, and invented - `interpolate` says why it
    is still closer than the alternative - but it contradicts nothing. A
    frozen reading recorded no metric row at all, which is exactly the gap
    these fill, and they are marked `derived` so the chart can draw them as a
    guess and a recomputation can take them back.

    Only where the *start* was measured. Without a login there is no moment to
    ramp from, and sloping up from the previous reading would be asserting a
    session we have no evidence of.

    Skills only. Experience is the thing that accrues continuously; a boss
    count genuinely does jump, and the plugin already reports those exactly as
    they happen (see gameplay.py), so smearing them would replace evidence
    with arithmetic.
    """
    if span.start_from != MEASURED or span.seconds <= 0:
        return 0
    opened = {r["metric"]: r["value"]
              for r in database.state_at(player["id"], previous_at, "skill")}
    closed = {r["metric"]: r["value"]
              for r in database.state_at(player["id"], reading_at, "skill")}

    # From the login, not from the previous reading. A four hour session
    # crosses several readings without moving any of them, so the ones that
    # need filling in mostly sit *before* the gap this gain was noticed in.
    # The pairs that bracket them were stepped over for showing no change,
    # which is what leaves them free to be written here.
    #
    # But never below `floor`, which is where attribute() clears from. A login
    # can predate the window by up to MAX_SESSION_HOURS, and a row written
    # under the floor is one no later run can revise: it would keep whatever
    # end the session appeared to have at the time, which for a session still
    # open is not the end it turned out to have. Readings under the floor were
    # inside the window on earlier runs and were ramped then.
    first = max(span.start, floor).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    written = 0
    for stamp in database.observations(player["id"], first, reading_at):
        when = parse_api_time(stamp)
        # Strictly inside: the reading that opens the gap is real, and the
        # moment the session closed already carries the whole gain.
        if when is None or when <= span.start or when >= span.end:
            continue
        fraction = (when - span.start).total_seconds() / span.seconds
        rows = [("skill", metric, value) for metric, value
                in interpolate(opened, closed, fraction).items()]
        if rows:
            written += database.record_derived_state(player["id"], stamp, rows)
    return written


def _place(database, player, previous_at, reading_at, when, fraction, _zone):
    """Write what the account had reached at one moment inside the gain."""
    rows = []
    for kind in KINDS:
        opened = {r["metric"]: r["value"]
                  for r in database.state_at(player["id"], previous_at, kind)}
        closed = {r["metric"]: r["value"]
                  for r in database.state_at(player["id"], reading_at, kind)}
        for metric, value in interpolate(opened, closed, fraction,
                                         whole=kind in WHOLE_KINDS).items():
            rows.append((kind, metric, value))
    if not rows:
        return 0
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return database.record_derived_state(player["id"], stamp, rows)
