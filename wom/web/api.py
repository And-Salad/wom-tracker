"""The JSON the browser draws from, and the guard in front of it.

These are the endpoints that cost something: each one is real database work,
on a machine that also runs the update schedule.
"""

import re

from flask import (Blueprint, Response, abort, current_app, jsonify, request,
                   session)

from . import data as web_data
from .data import NOBODY_PICKED
from . import today, views
from ..context import ViewContext
from ..util import parse_api_time, pretty_metric
from .selection import (chosen, colors, current_span, database, roster,
                        settings)

api = Blueprint("api", __name__)

# A metric name is a lowercase key from the Wise Old Man API, and is used to
# look up an icon on disk; nothing else may reach that lookup.
METRIC_NAME = re.compile(r"^[a-z0-9_]{1,40}$")

PAUSED = ("The dashboard has paused its data endpoints after a burst of "
          "automated traffic. An admin needs to resume it.")



def _fresh(payload):
    """A JSON answer the browser must not keep.

    Only an update changes these numbers, but when one lands the reader has
    to see it: without this a heuristic cache can serve pre-update figures
    for the same URL all session.
    """
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-cache"
    return response


def _paused():
    return Response(PAUSED, status=503, mimetype="text/plain",
                    headers={"Retry-After": "3600"})


def guard():
    """A response to send instead, or None to carry on.

    Admin is not budgeted and can still read while the wire is tripped -
    clearing it blind would be worse than the burst that tripped it.
    """
    limits = current_app.config["LIMITS"]
    if session.get("wom_admin"):
        return None
    if limits.api_tripwire.tripped:
        return _paused()
    address, _source = limits.address()
    waiting = limits.api_per_address.check(address)
    if waiting:
        return Response(
            "Too many requests. Try again in {} seconds.".format(waiting),
            status=429, mimetype="text/plain",
            headers={"Retry-After": str(waiting)})
    limits.api_per_address.record(address)
    if limits.api_tripwire.note(address):
        return _paused()
    return None


def _span(players):
    """The window this request asks about.

    An unusable date raises BadRequest, which the app answers with a 400 on
    every route at once - see wom/web/app.py.
    """
    return current_span(players)


@api.route("/api/chart/<key>")
def chart_data(key):
    refused = guard()
    if refused is not None:
        return refused
    config = settings()
    players = chosen(roster(config))
    span = _span(players)
    payload = web_data.build(database(), config, key, span, players,
                             request.args.get("choice"))
    if payload is None:
        abort(404)
    # The sidebar's date inputs show whatever the period resolved to, so every
    # answer says what window it was answering over.
    payload["span"] = span.as_dict()
    return _fresh(payload)


@api.route("/api/player/<username>")
def player_detail(username):
    refused = guard()
    if refused is not None:
        return refused
    player = database().player_by_username(username)
    if player is None:
        abort(404)
    config = settings()
    span = _span(chosen(roster(config)))
    return _fresh(views.player_detail(database(), player, span))


@api.route("/api/players")
def player_rows():
    """The roster table on /players, so the ticks need no page reload."""
    refused = guard()
    if refused is not None:
        return refused
    config = settings()
    players = roster(config)
    picked = chosen(players)
    span = _span(picked)
    return _fresh({"rows": views.player_rows(database(), picked,
                                             colors(config, players)),
                   "span": span.as_dict()})


@api.route("/api/maxing/player/<username>")
def maxing_player(username):
    """One account's day so far, skill by skill, for an opened row.

    The skills and nothing else. An account's written recaps are read on the
    Recaps page, where every window it has is in one tree - here they would
    push the day's figures, which are what the row was opened for, below a
    fold of prose.
    """
    refused = guard()
    if refused is not None:
        return refused
    player = database().player_by_username(username)
    if player is None:
        abort(404)
    return _fresh(today.breakdown(database(), player))


@api.route("/api/maxing/trend")
def maxing_trend():
    """Experience toward 99 since midnight, one line per included account.

    Its own endpoint rather than a catalogue chart: the Overview's charts all
    answer over the sidebar's period, and this one is always the day in
    progress. Handing it a period it then ignores would be the confusing part.
    """
    refused = guard()
    if refused is not None:
        return refused
    config = settings()
    players = roster(config)
    picked = chosen(players)
    if not picked:
        return _fresh({"empty": NOBODY_PICKED})
    context = ViewContext(database(), config, players, selected=picked,
                          span=_span(picked))
    return _fresh(today.trend(database(), picked, context.color_for))


@api.route("/api/milestones")
def milestones():
    """The achievements feed, over whatever window the sidebar names."""
    refused = guard()
    if refused is not None:
        return refused
    config = settings()
    players = roster(config)
    picked = chosen(players)
    span = _span(picked)
    feed = views.milestone_feed(database(), picked, colors(config, players),
                                since=span.since, until=span.until)
    return _fresh({"feed": feed, "span": span.as_dict()})


@api.route("/api/table")
def metric_table():
    """Every metric for the ticked players, for the table on /export.

    Sorting and filtering happen in the browser, so the whole set goes over
    once per change of the sidebar rather than once per column click.
    """
    refused = guard()
    if refused is not None:
        return refused
    config = settings()
    players = roster(config)
    picked = chosen(players)
    if not picked:
        return _fresh({"rows": [], "empty": NOBODY_PICKED})
    span = _span(picked)
    rows = views.metric_table(database(), picked, span.since, span.until,
                              colors(config, players))
    return _fresh({"rows": rows, "span": span.as_dict()})


UNITS = {"skill": "experience", "boss": "kills", "activity": "score"}


@api.route("/api/history")
def metric_history():
    """One line per player for the metric the Data table is filtered to.

    The table says where the accounts ended up; this says how they got there.
    It is a separate call because it asks a different question of the
    database - every reading of one metric, rather than one reading of every
    metric - and because the table can be re-sorted without redrawing it.
    """
    refused = guard()
    if refused is not None:
        return refused
    kind = request.args.get("kind", "skill")
    metric = request.args.get("metric", "")
    if kind not in UNITS or not METRIC_NAME.match(metric):
        abort(404)

    config = settings()
    players = roster(config)
    picked = chosen(players)
    if not picked:
        return _fresh({"empty": NOBODY_PICKED})
    span = _span(picked)

    context = ViewContext(database(), config, players, selected=picked, span=span)
    series = web_data.trend_series(
        database(), picked, context.color_for, kind, metric, "value",
        span.since, span.until, bucket=span.bucket)
    if not series:
        return _fresh({"empty": "No readings of {} in {}.".format(
            pretty_metric(metric), span.phrase)})
    return _fresh({
        "type": "trend",
        "ylabel": "{} {}".format(pretty_metric(metric), UNITS[kind]),
        "tooltip": {"style": "count", "unit": UNITS[kind]},
        # A skill's level is a fixed function of its experience, so the chart
        # can rule the plot in levels. "overall" is the exception: total level
        # is the sum of 23 separate curves, and two accounts on the same total
        # experience need not be on the same total level - there is no shared
        # axis to draw.
        "levelAxis": kind == "skill" and metric != "overall",
        "since": _epoch_ms(span.since),
        "until": _epoch_ms(span.until) if span.until else None,
        "series": series,
    })


def _epoch_ms(iso):
    return int(parse_api_time(iso).timestamp() * 1000)
