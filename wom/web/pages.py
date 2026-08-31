"""The pages people are given. Read-only, and thin: they gather, then render."""

import os

from flask import Blueprint, abort, render_template, send_file, send_from_directory

from ..icons import ASSET_DIR, icon_path
from . import views
from .data import catalog
from .selection import database, page_context, status

pages = Blueprint("pages", __name__)


def _shell(scope):
    """What every page hands the sidebar: who, and over what window."""
    return {"players": scope["players"],
            "selected": {p["username"] for p in scope["selected"]},
            "colors": scope["palette"],
            "span": scope["span"].as_dict(),
            "period_labels": scope["period_labels"],
            "status": status(scope["config"])}


@pages.route("/")
def dashboard():
    scope = page_context()
    return render_template("dashboard.html", specs=catalog(), **_shell(scope))


@pages.route("/milestones")
def milestones():
    scope = page_context()
    span = scope["span"]
    return render_template(
        "milestones.html",
        feed=views.milestone_feed(database(), scope["selected"], scope["palette"],
                                  since=span.since, until=span.until),
        **_shell(scope))


@pages.route("/summaries")
def summaries_page():
    scope = page_context()
    return render_template(
        "summaries.html",
        latest=views.latest_round_ups(database()),
        tree=views.summary_tree(database(), scope["selected"], scope["palette"]),
        **_shell(scope))


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
    return send_from_directory(ASSET_DIR, name)
