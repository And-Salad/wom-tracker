"""Who the current request is about: the roster, the ticks, the colours.

Every page answers the same three questions before it does anything else -
which players exist, which of them this request wants, and what colour each is
drawn in. These were closures inside the app factory, which meant nothing else
could call them and nothing could test them.
"""

from flask import current_app, request

from ..colors import player_color
from ..config import Config
from ..scheduler import next_slot, parse_last_run
from ..util import fmt_ago

def database():
    return current_app.config["DATABASE"]


def settings():
    """Re-read per request, so a change under /admin shows up immediately."""
    return Config()


def roster(config):
    """Every tracked player, in the order the settings list them."""
    stored = {row["username"]: row for row in database().players()}
    ordered = []
    for name in config.get("usernames", []):
        row = stored.pop(name.lower(), None)
        if row is not None:
            ordered.append(row)
    ordered.extend(stored.values())
    return ordered


def chosen(players):
    """The players this request asks for.

    A bare URL with no ?player= means everyone, so a shared link works. The
    `picked` marker says the ticks are a real choice, and then an empty list
    means nobody - the sidebar sends it on every request it builds.

    This used to answer differently for pages and for data endpoints, which
    meant one URL could mean two things: unticking everyone and moving tab
    handed back the whole roster, re-ticked, while the JSON behind the same
    query said nobody was included.
    """
    wanted = request.args.getlist("player")
    marked = bool(request.args.get("picked"))
    if not wanted:
        return [] if marked else players
    wanted = {name.lower() for name in wanted}
    picked = [p for p in players if p["username"] in wanted]
    return picked if marked else (picked or players)


def colors(config, players):
    return {p["username"]: player_color(config, p["username"], index)
            for index, p in enumerate(players)}


def current_span(players=None):
    """The window the request is asking about: the period, or the dates."""
    from .timespan import current_timespan
    return current_timespan(database(), players)


def status(config):
    """The line in the header: how many, how fresh, when next."""
    last = parse_last_run(config.get("last_run", ""))
    return {
        "last": fmt_ago(last.isoformat()) if last else "never",
        "next": next_slot().astimezone().strftime("%a %H:%M"),
        "players": len(config.get("usernames", [])),
    }


def page_context():
    """Everything a page needs about the current request, resolved once."""
    from .timespan import labels
    config = settings()
    players = roster(config)
    selected = chosen(players)
    return {
        "config": config,
        "players": players,
        "selected": selected,
        "palette": colors(config, players),
        "span": current_span(selected),
        "period_labels": labels(),
    }
