"""Turning the viewer's calendar days into the UTC stamps rows are keyed by.

Readings are stored in UTC, and every date control on the site hands over a
day in the viewer's own zone. Both the export and the Data table need the same
conversion, in both directions, so it lives in one place rather than twice.
"""

from datetime import datetime, timedelta, timezone

from ..util import api_stamp


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
    except ValueError as exc:
        raise BadRequest("{!r} is not a date. Use yyyy-mm-dd.".format(text)) from exc
    if end_of_day:
        day += timedelta(days=1)          # `to` is inclusive of the day named
    return api_stamp((day - timedelta(minutes=offset_minutes))
                     .replace(tzinfo=timezone.utc))


def offset_minutes(value):
    """The viewer's minutes east of UTC, as the page reports them."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return 0
    return minutes if -14 * 60 <= minutes <= 14 * 60 else 0


OFFSET_COOKIE = "wom_tz"


def viewer_offset():
    """The viewer's offset from the request, falling back to their cookie.

    A page is rendered before any script on it has run, so the first paint
    has no query string to read the offset from. Without the cookie the dates
    in the sidebar are computed in UTC, and an Eastern viewer after 20:00
    sees a "To" of tomorrow until they touch something. The cookie is written
    by sidebar.js and holds nothing but a number of minutes.
    """
    from flask import request
    given = request.args.get("tzoffset")
    if given is not None:
        return offset_minutes(given)
    return offset_minutes(request.cookies.get(OFFSET_COOKIE))


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
