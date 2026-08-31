"""Turning the viewer's calendar days into the UTC stamps rows are keyed by.

Readings are stored in UTC, and every date control on the site hands over a
day in the viewer's own zone. Both the export and the Data table need the same
conversion, in both directions, so it lives in one place rather than twice.
"""

from datetime import datetime, timedelta


class BadRequest(Exception):
    """Something in the query string cannot be honoured."""


def day_bound(value, end_of_day=False, offset_minutes=0):
    """A date from a picker as the UTC stamp the rows are keyed by.

    The bound is shifted by the viewer's offset: without it an Eastern
    viewer's "to 30 August" stops at 20:00 their time and quietly drops that
    day's last reading.

    An unparseable date raises rather than returning None. None means "no
    bound", and treating a typo as no bound exports the whole history while
    looking filtered.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        day = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise BadRequest("{!r} is not a date. Use yyyy-mm-dd.".format(text))
    if end_of_day:
        day += timedelta(days=1)          # `to` is inclusive of the day named
    return (day - timedelta(minutes=offset_minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def offset_minutes(value):
    """The viewer's minutes east of UTC, as the page reports them."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return 0
    return minutes if -14 * 60 <= minutes <= 14 * 60 else 0


def local_day(iso, offset_minutes=0):
    """The other direction: which of the viewer's days a UTC stamp falls on.

    The date inputs snap to whatever window is in force, so the server has to
    say when that window opened in the viewer's terms - "2026-08-24", not an
    instant four hours off it.
    """
    if not iso:
        return ""
    stamp = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
    return (stamp + timedelta(minutes=offset_minutes)).strftime("%Y-%m-%d")
