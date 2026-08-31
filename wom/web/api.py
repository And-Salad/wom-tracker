"""The JSON the browser draws from, and the guard in front of it.

These are the endpoints that cost something: each one is real database work,
on a machine that also runs the update schedule.
"""

import re

from flask import (Blueprint, Response, abort, current_app, jsonify, request,
                   session)

from . import data as web_data
from . import views
from ..context import ViewContext
from ..util import parse_api_time, pretty_metric
from .dates import BadRequest, day_bound, local_day, offset_minutes
from .selection import (chosen, colors, current_period, database, roster,
                        settings)

api = Blueprint("api", __name__)

# A metric name is a lowercase key from the Wise Old Man API, and is used to
# look up an icon on disk; nothing else may reach that lookup.
METRIC_NAME = re.compile(r"^[a-z0-9_]{1,40}$")

PAUSED = ("The dashboard has paused its data endpoints after a burst of "
          "automated traffic. An admin needs to resume it.")


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


@api.route("/api/chart/<key>")
def chart_data(key):
    refused = guard()
    if refused is not None:
        return refused
    config = settings()
    players = chosen(roster(config), strict=True)
    payload = web_data.build(database(), config, key, current_period(), players,
                             request_choice())
    if payload is None:
        abort(404)
    response = jsonify(payload)
    # Only an update changes these numbers; a reload should still re-ask.
    response.headers["Cache-Control"] = "no-cache"
    return response


@api.route("/api/player/<username>")
def player_detail(username):
    refused = guard()
    if refused is not None:
        return refused
    player = database().player_by_username(username)
    if player is None:
        abort(404)
    return jsonify(views.player_detail(database(), player, current_period()))


def request_choice():
    return request.args.get("choice")


@api.route("/api/table")
def metric_table():
    """Every metric for the ticked players, for the table on /export.

    Sorting and filtering happen in the browser, so the whole set goes over
    once per change of player, period or dates rather than once per column
    click.
    """
    refused = guard()
    if refused is not None:
        return refused
    config = settings()
    players = roster(config)
    picked = chosen(players, strict=True)
    if not picked:
        return jsonify({"rows": [],
                        "empty": "Include at least one player using the "
                                 "sidebar swatches."})
    period = current_period()
    try:
        window = _window(period)
    except BadRequest as exc:
        return Response(str(exc), status=400, mimetype="text/plain")

    rows = views.metric_table(database(), picked, window["since"],
                              window["until"], colors(config, players))
    offset = offset_minutes(request.args.get("tzoffset"))
    response = jsonify({
        "rows": rows,
        "period": period.label,
        # What the date inputs should read when they are snapping to the
        # period rather than holding a choice of their own. A date that was
        # asked for is echoed back as asked: `until` is the exclusive start of
        # the next day, and reporting that would move every "to" on by one.
        "window": {"from": (request.args.get("from") or "").strip()
                           or local_day(window["since"], offset),
                   "to": (request.args.get("to") or "").strip()
                         or local_day(_now_iso(), offset)},
    })
    response.headers["Cache-Control"] = "no-cache"
    return response


UNITS = {"skill": "experience", "boss": "kills", "activity": "score"}


@api.route("/api/history")
def metric_history():
    """One line per player for the metric the Data table is filtered to.

    The table says where six accounts ended up; this says how they got there.
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
    picked = chosen(players, strict=True)
    if not picked:
        return jsonify(_nothing("Include at least one player using the "
                                "sidebar swatches."))
    try:
        window = _window(current_period())
    except BadRequest as exc:
        return Response(str(exc), status=400, mimetype="text/plain")

    context = ViewContext(database(), config, players, selected=picked)
    series = web_data.trend_series(
        database(), picked, context.color_for, kind, metric, "value",
        window["since"], window["until"], bucket=window["bucket"])
    if not series:
        return jsonify(_nothing("No readings of {} in this window.".format(
            pretty_metric(metric))))
    response = jsonify({
        "type": "trend",
        "ylabel": "{} {}".format(pretty_metric(metric), UNITS[kind]),
        "tooltip": {"style": "count", "unit": UNITS[kind]},
        # A skill's level is a fixed function of its experience, so the chart
        # can rule the plot in levels. "overall" is the exception: total level
        # is the sum of 23 separate curves, and two accounts on the same total
        # experience need not be on the same total level - there is no shared
        # axis to draw.
        "levelAxis": kind == "skill" and metric != "overall",
        "since": _epoch_ms(window["since"]),
        "until": _epoch_ms(window["until"]) if window["until"] else None,
        "series": series,
    })
    response.headers["Cache-Control"] = "no-cache"
    return response


def _nothing(message):
    return {"empty": message}


def _epoch_ms(iso):
    return int(parse_api_time(iso).timestamp() * 1000)


def _window(period):
    """The window both Data endpoints answer over: the dates, else the period.

    Long windows are bucketed to one reading a day. Updates land at least four
    times daily, which is far more detail than a month-wide axis can draw.
    """
    offset = offset_minutes(request.args.get("tzoffset"))
    since = day_bound((request.args.get("from") or "").strip(),
                      offset_minutes=offset)
    until = day_bound((request.args.get("to") or "").strip(), end_of_day=True,
                      offset_minutes=offset)
    opened = since or period.start_iso()
    span = (parse_api_time(until or _now_iso()) -
            parse_api_time(opened)).total_seconds()
    return {"since": opened, "until": until,
            "bucket": "day" if span > 8 * 86400 else None}


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
