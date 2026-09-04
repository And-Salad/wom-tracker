"""The look-back windows offered by the Summary tab."""

from datetime import datetime, timedelta, timezone

from .util import api_stamp


class Period:
    def __init__(self, key, label, days, bucket=None):
        self.key = key
        self.label = label
        self.days = days
        # How finely a line chart should plot this window. Updates land every
        # ten minutes - more when a player's plugin or a manual refresh fires
        # - which is far more detail than a month-wide axis can show. Long
        # windows collapse to one point per day; short ones keep every
        # snapshot, because a day's worth of points is the whole chart.
        self.bucket = bucket

    def start(self, now=None):
        now = now or datetime.now(timezone.utc)
        return now - timedelta(days=self.days)

    def start_iso(self, now=None):
        return api_stamp(self.start(now))


PERIODS = (
    Period("day", "Day", 1),
    Period("week", "Week", 7),
    Period("month", "Month", 30, bucket="day"),
    Period("quarter", "Quarter", 91, bucket="day"),
    Period("year", "Year", 365, bucket="day"),
)

DEFAULT_PERIOD = "week"


# -- how late a reading may be before the figures are "short" --------------
#
# Gains are measured from the last reading at or before the window opened. It
# is never exactly on the boundary, so some lateness is the schedule working
# rather than missing data - and past some point it is genuinely missing data,
# which is what the coverage notes on the pages exist to say.
#
# Three places asked that question with a bare `> asked * 0.1`, written when
# updates ran every six hours and a tenth of the window was about one update.
# At a reading every ten minutes a tenth is enormous: it let a Week hide
# sixteen hours and a Month three whole days behind "fully covered", which is
# the exact failure the notes were added to prevent.
#
# So: a floor for short windows, a proportion for long ones, whichever is
# larger.
#
# The floor is what one update cadence costs plus room for an ordinary
# interruption - a slot is ten minutes and a pass over a group takes a few
# more, so a deploy or a restart can eat half an hour without anything being
# wrong. Two hours is comfortably past that and still well short of an
# outage worth reporting.
#
# The proportion keeps long windows from crying wolf: a baseline a day late
# into a year is a third of a per cent of it, and flagging that would make
# the note meaningless by making it constant.
COVERAGE_FLOOR_SECONDS = 2 * 3600
COVERAGE_FRACTION = 0.01


def coverage_slack(window_seconds):
    """How late the baseline may be before a window counts as short.

    Day 2h, Week 2h, Month ~7h, Year ~3.6d - against a cadence that lands a
    reading every ten minutes, so anything past these means readings are
    actually missing rather than merely not instantaneous.
    """
    return max(COVERAGE_FLOOR_SECONDS, (window_seconds or 0) * COVERAGE_FRACTION)


_BY_KEY = {p.key: p for p in PERIODS}
_BY_LABEL = {p.label: p for p in PERIODS}


# -- calendar windows -----------------------------------------------------
#
# Charts use the rolling periods above - "the last 7 days" from right now. A
# written summary needs the opposite: a closed window with a name a person can
# read, so yesterday is "Saturday 29 August" and not "the last 24 hours".

class Window:
    """One closed, named span of time: [start, end)."""

    def __init__(self, period, start, end, label):
        self.period = period          # day | week | month
        self.start = start            # aware datetime, inclusive
        self.end = end                # aware datetime, exclusive
        self.label = label            # "Saturday 29 August 2026"

    @property
    def key(self):
        """Stable identifier for this window, used as the storage key."""
        return self.start.strftime("%Y-%m-%d")

    def start_iso(self):
        return api_stamp(self.start)

    def end_iso(self):
        return api_stamp(self.end)

    def __repr__(self):
        return "<Window {} {}>".format(self.period, self.key)


def latest_window(period, now=None, offset=0):
    """The most recently *completed* window of a period.

    `offset` steps further back: 1 is the one before it, and so on. Everything
    is anchored to midnight in the configured zone, so the boundaries line up
    with the update schedule rather than drifting against a viewer's clock.
    """
    from .scheduler import zone
    now = (now or datetime.now(timezone.utc)).astimezone(zone())
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "day":
        end = midnight - timedelta(days=offset)
        start = end - timedelta(days=1)
        return Window(period, start, end, start.strftime("%A %d %B %Y"))

    if period == "week":
        # Weeks run Monday to Sunday; the newest complete one ended at the
        # Monday midnight on or before today.
        this_monday = midnight - timedelta(days=midnight.weekday())
        end = this_monday - timedelta(weeks=offset)
        start = end - timedelta(weeks=1)
        last_day = end - timedelta(days=1)      # the Sunday, for the label
        if start.month == last_day.month:
            label = "{}-{} {}".format(start.day, last_day.day,
                                      last_day.strftime("%B %Y"))
        elif start.year == last_day.year:
            label = "{} {} - {} {}".format(start.day, start.strftime("%b"),
                                           last_day.day, last_day.strftime("%b %Y"))
        else:
            label = "{} {} - {} {}".format(start.day, start.strftime("%b %Y"),
                                           last_day.day, last_day.strftime("%b %Y"))
        return Window(period, start, end, label)

    if period == "month":
        first_this = midnight.replace(day=1)
        end = first_this
        for _ in range(offset):
            end = (end - timedelta(days=1)).replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        return Window(period, start, end, start.strftime("%B %Y"))

    if period == "quarter":
        # Quarters start in January, April, July and October.
        first_this = midnight.replace(month=(midnight.month - 1) // 3 * 3 + 1,
                                      day=1)
        end = first_this
        for _ in range(offset):
            end = _quarter_before(end)
        start = _quarter_before(end)
        return Window(period, start, end, "Q{} {}".format(
            (start.month - 1) // 3 + 1, start.year))

    if period == "year":
        end = midnight.replace(month=1, day=1)
        end = end.replace(year=end.year - offset)
        start = end.replace(year=end.year - 1)
        return Window(period, start, end, str(start.year))

    raise ValueError("no calendar window for period {!r}".format(period))


def _quarter_before(start_of_quarter):
    """The first day of the quarter before this one."""
    month = start_of_quarter.month - 3
    year = start_of_quarter.year
    if month < 1:
        month += 12
        year -= 1
    return start_of_quarter.replace(year=year, month=month, day=1)


# Each player's own notes are written for all of these, newest complete window
# first. Quarter and year join the list before either has ever been due, so the
# first of each is written from whatever history exists rather than from
# nothing - see the per-period prompts in data/.
SUMMARY_PERIODS = ("day", "week", "month", "quarter", "year")

# The windows a group round-up is written for, per board. The day and the
# month are what the leaderboards judge outright; the week sits between them
# as a review - who took each of its days, and where the month stands so far.
# Quarters and years are still left alone: nothing on either leaderboard has a
# verdict for them, so a round-up would be describing a window with no result
# to put beside it. A player's own notes still cover all five, because those
# are about one account's progress and do not need a competition.
GROUP_PERIODS = ("day", "week", "month")




def get(key):
    return _BY_KEY.get(key, _BY_KEY[DEFAULT_PERIOD])


def by_label(label):
    return _BY_LABEL.get(label, _BY_KEY[DEFAULT_PERIOD])


def labels():
    return [p.label for p in PERIODS]
