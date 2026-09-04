"""The pages people are given. Read-only, and thin: they gather, then render."""

import os
import re

from flask import (Blueprint, abort, redirect, render_template, request,
                   send_file, send_from_directory, url_for)

from .. import gallery, winners
from ..icons import ASSET_DIR, icon_path
from . import today, views
from .data import catalog
from .selection import database, page_context

pages = Blueprint("pages", __name__)


def _shell(scope):
    """What every page hands the sidebar: who, and over what window.

    Not `status`: the header line is the same on every page including admin,
    so the app factory's context processor supplies it. Passing it here as
    well only overrode an identical value that had already been computed.
    """
    return {"players": scope["players"],
            "selected": {p["username"] for p in scope["selected"]},
            "colors": scope["palette"],
            "span": scope["span"].as_dict(),
            "period_labels": scope["period_labels"]}


@pages.route("/")
def dashboard():
    scope = page_context()
    return render_template("dashboard.html", specs=catalog(), **_shell(scope))


# What each leaderboard calls itself and what it counts. Two competitions
# over the same days, and the only difference is the measure.
BOARDS = {
    winners.MAXING: {
        "key": winners.MAXING, "label": "Maxing",
        "measure": "XP Towards 99",
        "measure_hint": "Experience toward a 99 since midnight, which is what"
                        " the day is judged on",
        "chart_title": "Experience toward 99 today",
        "split_wins": True,
    },
    winners.GRINDING: {
        "key": winners.GRINDING, "label": "Grinding",
        "measure": "XP Gained",
        "measure_hint": "All experience gained since midnight, which is what"
                        " the day is judged on",
        "chart_title": "Experience gained today",
        # A ninety-nine never takes a day here, so the split that Maxing's
        # table makes would be a column that can only ever read nothing.
        "split_wins": False,
    },
}


@pages.route("/maxing")
def maxing():
    return _leaderboard(winners.MAXING)


@pages.route("/grinding")
def grinding():
    return _leaderboard(winners.GRINDING)


def _leaderboard(board):
    """One of the two leaderboards, which is the whole group's or it is nothing.

    Both the calendar and the standings are given every tracked account, not
    the ticked ones. It is one competition with one answer: narrowed to three
    of six it silently becomes a different competition, and the squares would
    recolour to a result nobody was playing for.

    The two have to be given the same set as each other, too. The standings
    tally each account's wins this month from the same daily verdicts the
    squares are coloured by, so a calendar judged across everyone beside a
    table judged across three would credit different days on one page.

    The chart below them does follow the ticks - it is a line per account,
    and thinning it is what the ticks are for.
    """
    scope = page_context()
    everyone = scope["players"]
    return render_template(
        "board.html",
        board=BOARDS[board],
        calendar=views.winner_calendar(database(), everyone, scope["palette"],
                                       board=board),
        today=today.standings(database(), everyone, scope["palette"],
                              board=board),
        **_shell(scope))


@pages.route("/milestones")
def milestones():
    scope = page_context()
    span = scope["span"]
    return render_template(
        "milestones.html",
        feed=views.milestone_feed(database(), scope["selected"], scope["palette"],
                                  since=span.since, until=span.until),
        categories=views.FEED_CATEGORIES,
        **_shell(scope))


@pages.route("/help")
def help_page():
    """How the whole thing works, for the people it is for.

    Not in the nav: it is read once and then never again, and a seventh tab
    for it would cost every page a little clarity to save one visit. The
    sidebar carries the link instead.
    """
    return render_template("help.html")


@pages.route("/gallery")
def gallery_page():
    scope = page_context()
    return render_template(
        "gallery.html",
        panels=views.gallery_panels(database(), scope["selected"],
                                    scope["palette"]),
        **_shell(scope))


@pages.route("/gallery/<digest>.<fmt>")
def gallery_image(digest, fmt):
    """One stored screenshot.

    The digest names the file and is checked against the database before it
    reaches the filesystem, so a crafted URL cannot walk anywhere: only a
    64-character hex string we put there ourselves resolves at all. The type
    served is the one we read out of the bytes when they arrived, never the
    one the sender claimed.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        abort(404)
    row = database().image(digest)
    if row is None or row["format"] != fmt:
        abort(404)
    path = gallery.path_for(digest, row["format"])
    if not os.path.exists(path):
        abort(404)
    response = send_file(path, mimetype=gallery.MIME[row["format"]])
    # Named by their own contents, so a URL can only ever mean one picture.
    response.headers["Cache-Control"] = "public, max-age=604800"
    response.headers["Content-Disposition"] = "inline"
    return response


@pages.route("/recaps")
def recaps_page():
    scope = page_context()
    feeds = views.recap_feeds(database(), scope["selected"], scope["palette"])
    return render_template(
        "recaps.html",
        feeds=feeds,
        any_latest=any(feed["entries"] for feed in feeds),
        tree=views.recap_tree(database(), scope["selected"], scope["palette"]),
        **_shell(scope))


@pages.route("/summaries")
def summaries_redirect():
    """The tab was called Round-ups and lived here. Links outlive renames."""
    return redirect(url_for("pages.recaps_page", **request.args), code=301)


@pages.route("/players")
def players_page():
    scope = page_context()
    return render_template(
        "players.html",
        rows=views.player_rows(database(), scope["selected"], scope["palette"]),
        **_shell(scope))


# -- files ----------------------------------------------------------------

@pages.route("/icon/<kind>/<metric>.png")
def icon(kind, metric):
    if kind not in ("skill", "boss", "activity"):
        abort(404)
    # icon_path returns None for anything that is not a metric name, which is
    # what stops a crafted URL walking out of the asset directory.
    path = icon_path(metric, kind)
    if path is None or not os.path.exists(path):
        abort(404)
    response = send_file(path, mimetype="image/png")
    # Sprites never change, and a 24-column chart asks for 24 of them on every
    # redraw, so let the browser keep them.
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response


@pages.route("/assets/<path:name>")
def asset(name):
    # send_from_directory is what stops a crafted name walking out of the
    # directory; it refuses anything that resolves outside it.
    response = send_from_directory(ASSET_DIR, name)
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response


FAVICON = os.path.join(ASSET_DIR, "favicon.png")


@pages.route("/favicon.ico")
def favicon():
    """Browsers ask for this by name whatever the page links to.

    A PNG under an .ico name is what every browser since IE11 wants anyway,
    and serving it here keeps a 404 a request out of every log.
    """
    if not os.path.exists(FAVICON):
        abort(404)
    response = send_file(FAVICON, mimetype="image/png")
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response
