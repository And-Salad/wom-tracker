"""Who the current request is about: the roster, the ticks, the colours.

Every page answers the same three questions before it does anything else -
which players exist, which of them this request wants, and what colour each is
drawn in. These were closures inside the app factory, which meant nothing else
could call them and nothing could test them.
"""

from datetime import datetime, timezone

from flask import current_app, request

from ..colors import player_color
from ..config import Config
from ..scheduler import next_slot, parse_last_run
from ..util import fmt_ago
from .timespan import current_timespan, labels


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
    return current_timespan(database(), players)


def viewers():
    """How many people are on the site right now, this one included.

    Counted rather than configured - see viewers.py. Zero when there is no
    counter, which is only ever a status() called outside an application.
    """
    counter = current_app.config.get("VIEWERS") if current_app else None
    return counter.count() if counter else 0


def status(config):
    """The line in the header: how many, how fresh, when next.

    Rendered into every page, and also answered as JSON to a browser that
    polls it - so it carries a machine-readable half beside the prose. `last`
    is "3m ago", which is unusable as an equality test, and `next` is
    "Wed 14:20", which cannot be counted down from.
    """
    last = parse_last_run(config.get("last_run", ""))
    upcoming = next_slot()
    return {
        "last": fmt_ago(last.isoformat()) if last else "never",
        "next": upcoming.astimezone().strftime("%a %H:%M"),
        # The roster used to be counted here, which is a number that changes
        # about once a month and that the Players page already gives. Who
        # else is reading it right now is not knowable anywhere else.
        "viewers": viewers(),
        # What actually changes when a run lands: the stored stamp itself.
        "stamp": config.get("last_run", "") or "",
        # The same instant `next` names, and this clock's reading of now, so a
        # countdown survives a browser whose clock is wrong.
        "next_at": upcoming.isoformat(timespec="seconds"),
        "now": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class Scope:
    """Who the request is about, over what window, in what colours.

    The three questions every route answers before it does anything else. The
    HTML routes had them in page_context(); the JSON routes each wrote the
    same four lines out again, six times over, with small differences nobody
    intended - which is two halves of one app disagreeing by accident about
    what a request means.
    """

    def __init__(self):
        self.config = settings()
        self.players = roster(self.config)          # everyone, display order
        self.selected = chosen(self.players)        # the ticked ones
        self.palette = colors(self.config, self.players)

    @property
    def span(self):
        """Resolved lazily: a bad date raises, and a route that never asks
        for the window should not be refused on account of one."""
        if not hasattr(self, "_span"):
            self._span = current_span(self.selected)
        return self._span


def scope():
    return Scope()


def shell(scope):
    """What every page hands the sidebar: who, and over what window.

    Not `status`: the header line is the same on every page including admin,
    so the app factory's context processor supplies it. Passing it here as
    well only overrode an identical value that had already been computed.

    It lives beside page_context because it is that context reshaped, and
    because the export page needs it too - which it used to get by reaching
    into pages.py for a private name, from inside a function, to break a
    circular import that was never there.
    """
    return {"players": scope["players"],
            "selected": {p["username"] for p in scope["selected"]},
            "colors": scope["palette"],
            "span": scope["span"].as_dict(),
            "period_labels": scope["period_labels"]}


def page_context():
    """Everything a page needs about the current request, resolved once."""
    found = Scope()
    return {
        "config": found.config,
        "players": found.players,
        "selected": found.selected,
        "palette": found.palette,
        "span": found.span,
        "period_labels": labels(),
    }
