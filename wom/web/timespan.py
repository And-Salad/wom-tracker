"""The span of time every page is answering over.

One sidebar drives five tabs, so what "the window" means has to be decided in
one place. A request names it in one of three ways:

    period=Week                 a rolling window, the last seven days
    period=Custom&from=&to=     the dates typed into the sidebar
    period=All time             everything held for the ticked players

The first is what the app has always done and is left exactly as it was: a
preset measures back from this instant, not from midnight, so "Day" stays the
last twenty-four hours rather than however much of today has happened.
"""

from datetime import datetime, timezone

from flask import request

from .. import periods
from ..scheduler import previous_slot
from ..util import api_stamp, parse_api_time
from .dates import BadRequest, day_bound, local_day, viewer_offset

CUSTOM = "Custom"
ALL_TIME = "All time"
# "Day" is the last twenty-four hours; this is the calendar day the viewer is
# standing in, which is what someone means when they ask how today is going.
# It is the one window whose length changes as the day runs.
TODAY = "Today"

# Long windows are drawn from one reading a day. Updates land every ten
# minutes, which is far more detail than a month-wide axis can render.
BUCKET_AFTER_DAYS = 8


class Timespan:
    """A resolved window, and everything the pages need to describe it."""

    def __init__(self, since, until, label, key=None, bucket=None,
                 from_date="", to_date=""):
        self.since = since            # ISO UTC, inclusive
        self.until = until            # ISO UTC, exclusive, or None for "to now"
        self.label = label            # "Week", "All time", "1 Jun - 14 Aug 2026"
        # The period key a written note is stored under, where there is one.
        # A custom range names no calendar window, so it has none.
        self.key = key
        self.bucket = bucket
        self.from_date = from_date    # what the sidebar's date inputs read
        self.to_date = to_date

    @property
    def phrase(self):
        """How a sentence ends when it names this window: "Nobody gained
        anything {}."

        The preposition is part of the answer. It was left to the sentence,
        which wrote "in {}" around every window - and read "in today", on
        the one period anybody opens just after midnight to find empty.
        """
        if self.key:
            return "in the last {}".format(self.label.lower())
        if self.label == ALL_TIME:
            return "in the whole history"
        if self.label == TODAY:
            return "so far today"
        if self.from_date and self.to_date and self.from_date != self.to_date:
            return "between {} and {}".format(_pretty(self.from_date),
                                              _pretty(self.to_date))
        if self.from_date:
            return "on {}".format(_pretty(self.from_date))
        return "in {}".format(self.label)

    @property
    def choice(self):
        """Which entry the sidebar's period select should be showing.

        Not the same question as the label: "All time" is a named window that
        happens to have no period key, and answering "custom" for it would
        leave the select saying Custom while the sidebar reads All time.
        """
        return self.label if self.label in labels() else CUSTOM

    def as_dict(self):
        return {"label": self.label, "from": self.from_date, "to": self.to_date,
                "choice": self.choice, "custom": self.choice == CUSTOM}


def labels():
    """Everything the sidebar's period select offers, in order."""
    return [TODAY] + list(periods.labels()) + [ALL_TIME, CUSTOM]


def current_timespan(database=None, players=None):
    """Resolve the request's window. Raises BadRequest on an unusable date."""
    offset = viewer_offset()
    # .title() would make "All time" into "All Time", so the two named
    # windows are matched on their own terms and only a period label is
    # title-cased, which is how periods.by_label wants it.
    asked = (request.args.get("period", "") or "").strip()
    asked_from = (request.args.get("from") or "").strip()
    asked_to = (request.args.get("to") or "").strip()

    # Dates are honoured whenever they are present, so a link someone pasted
    # works even if its period says something else.
    since = day_bound(asked_from, offset_minutes=offset)
    until = day_bound(asked_to, end_of_day=True, offset_minutes=offset)
    if asked.lower() == CUSTOM.lower() or since or until:
        # Backwards is not a window. Left alone it reads as a quiet period:
        # every gain clamps to zero, the figures come from the earlier date,
        # and nothing on the page says the range was impossible.
        if since and until and since >= until:
            raise BadRequest(
                "{} comes after {}. Swap the dates, or clear them with the x."
                .format(_pretty(asked_from), _pretty(asked_to)))
        opened = since or _earliest(database, players) or _rolling().start_iso()
        # The label reads back the days that were asked for. `until` is the
        # exclusive start of the day after, and naming that would report every
        # range as ending a day later than it does.
        opens_on = asked_from or local_day(opened, offset)
        closes_on = asked_to or local_day(_now(), offset)
        return Timespan(
            opened, until, "{} to {}".format(_pretty(opens_on), _pretty(closes_on)),
            key=None, bucket=_bucket(opened, until),
            from_date=opens_on, to_date=closes_on)

    if asked.lower() == TODAY.lower():
        # Midnight where the viewer is, which is the same boundary the dates
        # in the sidebar use, so "Today" and a custom range of today agree.
        day = local_day(_now(), offset)
        return Timespan(day_bound(day, offset_minutes=offset), None, TODAY,
                        key=None, bucket=None, from_date=day, to_date=day)

    if asked.lower() == ALL_TIME.lower():
        opened = _earliest(database, players) or _rolling().start_iso()
        return Timespan(opened, None, ALL_TIME, key=None,
                        bucket=_bucket(opened, None),
                        from_date=local_day(opened, offset),
                        to_date=local_day(_now(), offset))

    period = _rolling(asked.title())
    # From the last update slot, not from this second. The readings only move
    # when an update lands, so the figures are the same either way - but a
    # start that moved every request made every request a different question,
    # and nothing about one could be remembered for the next. See wom/memo.py.
    opened = period.start_iso(previous_slot().astimezone(timezone.utc))
    return Timespan(opened, None, period.label, key=period.key,
                    bucket=period.bucket,
                    from_date=local_day(opened, offset),
                    to_date=local_day(_now(), offset))


def _rolling(label=None):
    return periods.by_label(label or "") if label else periods.by_label("")


def _earliest(database, players):
    """The first reading held for the ticked players, for "All time".

    An unbounded window is not the same as no window: the gains baseline and
    a chart's axis both need a real start, and "the beginning of the data" is
    the honest one.
    """
    if database is None or not players:
        return None
    return database.earliest_reading([p["id"] for p in players])


def _bucket(since, until):
    span = (parse_api_time(until or _now())
            - parse_api_time(since)).total_seconds()
    return "day" if span > BUCKET_AFTER_DAYS * 86400 else None


def _pretty(day):
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%d %b %Y")
    except ValueError:
        return day


def _now():
    return api_stamp(datetime.now(timezone.utc))
