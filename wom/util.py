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


def pretty_metric(metric):
    """'chambers_of_xeric' -> 'Chambers of Xeric'."""
    words = str(metric).replace("-", "_").split("_")
    out = []
    for index, word in enumerate(words):
        lower = word.lower()
        out.append(lower if index and lower in _SMALL_WORDS else lower.capitalize())
    return " ".join(out)
