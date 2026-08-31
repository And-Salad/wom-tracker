"""The pages people are given. Read-only, and thin: they gather, then render."""

import os

from flask import Blueprint, abort, render_template, send_file, send_from_directory

from .. import periods
from ..icons import ASSET_DIR, icon_path
from . import views
from .data import catalog
from .selection import (colors, current_period, database, page_context, roster,
                        settings, status)

pages = Blueprint("pages", __name__)


@pages.route("/")
def dashboard():
    scope = page_context()
    return render_template(
        "dashboard.html", players=scope["players"],
        selected={p["username"] for p in scope["selected"]},
        colors=scope["palette"], periods=periods.labels(),
        period=current_period(), specs=catalog(),
        status=status(scope["config"]))


@pages.route("/milestones")
def milestones():
    from flask import request

    scope = page_context()
    label = request.args.get("period", "All time")
    period = None if label == "All time" else periods.by_label(label)
    return render_template(
        "milestones.html", players=scope["players"],
        selected={p["username"] for p in scope["selected"]},
        colors=scope["palette"],
        periods=["All time"] + periods.labels(), period=label,
        feed=views.milestone_feed(database(), scope["selected"], scope["palette"],
                                  since=period.start_iso() if period else None),
        status=status(scope["config"]))


@pages.route("/summaries")
def summaries_page():
    scope = page_context()
    return render_template(
        "summaries.html", players=scope["players"],
        selected={p["username"] for p in scope["selected"]},
        colors=scope["palette"],
        tree=views.summary_tree(database(), scope["selected"], scope["palette"]),
        status=status(scope["config"]))


@pages.route("/players")
def players_page():
    config = settings()
    players = roster(config)
    palette = colors(config, players)
    return render_template(
        "players.html", rows=views.player_rows(database(), players, palette),
        players=players, colors=palette,
        selected={p["username"] for p in players},
        periods=periods.labels(), period=current_period(),
        status=status(config))


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
