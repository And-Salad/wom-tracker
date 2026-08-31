"""The look-back windows offered by the Summary tab."""

from datetime import datetime, timedelta, timezone


class Period:
    def __init__(self, key, label, days, bucket=None):
        self.key = key
        self.label = label
        self.days = days
        # How finely a line chart should plot this window. Updates land at
        # least four times a day - more when a player's plugin or a manual
        # refresh fires - which is far more detail than a month-wide axis can
        # show. Long windows collapse to one point per day; short ones keep
        # every snapshot, because a day's worth of points is the whole chart.
        self.bucket = bucket

    def start(self, now=None):
        now = now or datetime.now(timezone.utc)
        return now - timedelta(days=self.days)

    def start_iso(self, now=None):
        return self.start(now).strftime("%Y-%m-%dT%H:%M:%S.000Z")


PERIODS = (
    Period("day", "Day", 1),
    Period("week", "Week", 7),
    Period("month", "Month", 30, bucket="day"),
    Period("quarter", "Quarter", 91, bucket="day"),
    Period("year", "Year", 365, bucket="day"),
)

DEFAULT_PERIOD = "week"

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
        return _utc(self.start)

    def end_iso(self):
        return _utc(self.end)

    def __repr__(self):
        return "<Window {} {}>".format(self.period, self.key)


def _utc(when):
    from datetime import timezone
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def latest_window(period, now=None, offset=0):
    """The most recently *completed* window of a period.

    `offset` steps further back: 1 is the one before it, and so on. Everything
    is anchored to Eastern midnight so the boundaries line up with the update
    schedule rather than drifting against the viewer's clock.
    """
    from .scheduler import EASTERN
    now = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
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


# Written notes are produced for each of these, newest complete window first.
# Quarter and year join the list before either has ever been due, so the first
# of each is written from whatever history exists rather than from nothing -
# see the per-period prompts in data/.
SUMMARY_PERIODS = ("day", "week", "month", "quarter", "year")




def get(key):
    return _BY_KEY.get(key, _BY_KEY[DEFAULT_PERIOD])


def by_label(label):
    return _BY_LABEL.get(label, _BY_KEY[DEFAULT_PERIOD])


def labels():
    return [p.label for p in PERIODS]
