"""Small formatting and parsing helpers."""

from datetime import datetime, timezone

_SMALL_WORDS = {"of", "the", "and", "in", "at"}


def parse_api_time(text):
    """Parse an ISO-8601 timestamp from the API into an aware datetime."""
    if not text:
        return None
    value = str(text).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def api_stamp(when):
    """An aware datetime as the ISO-UTC string every stored timestamp uses.

    The inverse of parse_api_time, and it belongs next to it. This same three
    lines were written out as `_stamp` in winners.py and today.py, as `_utc`
    in periods.py, and inline in limits.py, timespan.py, api.py and db.py -
    seven copies of one format string, two of which re-imported `timezone`
    inside the function to do it.
    """
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def to_local(dt):
    """An aware timestamp in this machine's zone, for display."""
    return dt.astimezone() if dt else None


def fmt_datetime(text, fmt="%Y-%m-%d %H:%M"):
    local = to_local(parse_api_time(text))
    return local.strftime(fmt) if local else "-"


def fmt_ago(text):
    """Human 'time since' for an API timestamp."""
    dt = parse_api_time(text)
    if dt is None:
        return "-"
    seconds = (datetime.now(timezone.utc) - dt).total_seconds()
    if seconds < 0:
        return "just now"
    for limit, divisor, unit in (
        (90, 1, "s"), (5400, 60, "m"), (129600, 3600, "h"),
    ):
        if seconds < limit:
            return "{:.0f}{} ago".format(seconds / divisor, unit)
    return "{:.0f}d ago".format(seconds / 86400)


def fmt_int(value, dash="-"):
    if value is None:
        return dash
    try:
        return "{:,}".format(int(round(float(value))))
    except (TypeError, ValueError):
        return dash


def fmt_hours(value, dash="-"):
    """Efficient hours, which are a decimal figure and were being flattened.

    EHP and EHB are stored as they arrive - 500.5 hours, not 500 - and went
    through fmt_int, which threw the half away and, on an exact half, rounded
    to even: 500.5 displayed as 500 while 500.6 displayed as 501. One decimal
    is what the figure is worth and all it is ever quoted to.
    """
    if value is None:
        return dash
    try:
        return "{:,.1f}".format(float(value))
    except (TypeError, ValueError):
        return dash


def pretty_metric(metric):
    """'chambers_of_xeric' -> 'Chambers of Xeric'."""
    words = str(metric).replace("-", "_").split("_")
    out = []
    for index, word in enumerate(words):
        lower = word.lower()
        out.append(lower if index and lower in _SMALL_WORDS else lower.capitalize())
    return " ".join(out)
