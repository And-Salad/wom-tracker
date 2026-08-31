"""The JSON the browser draws from, and the guard in front of it.

These are the endpoints that cost something: each one is real database work,
on a machine that also runs the update schedule.
"""

from flask import (Blueprint, Response, abort, current_app, jsonify, request,
                   session)

from . import data as web_data
from . import views
from .dates import BadRequest, day_bound, local_day, offset_minutes
from .selection import (chosen, colors, current_period, database, roster,
                        settings)

api = Blueprint("api", __name__)

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
    offset = offset_minutes(request.args.get("tzoffset"))
    asked_from = (request.args.get("from") or "").strip()
    asked_to = (request.args.get("to") or "").strip()
    try:
        since = day_bound(asked_from, offset_minutes=offset)
        until = day_bound(asked_to, end_of_day=True, offset_minutes=offset)
    except BadRequest as exc:
        return Response(str(exc), status=400, mimetype="text/plain")

    # Dates that were typed win; otherwise the window is the chosen period,
    # running to now. Either half can be given on its own.
    opened = since or period.start_iso()
    rows = views.metric_table(database(), picked, opened, until,
                              colors(config, players))
    response = jsonify({
        "rows": rows,
        "period": period.label,
        # What the date inputs should read when they are snapping to the
        # period rather than holding a choice of their own. A date that was
        # asked for is echoed back as asked: `until` is the exclusive start of
        # the next day, and reporting that would move every "to" on by one.
        "window": {"from": asked_from or local_day(opened, offset),
                   "to": asked_to or local_day(_now_iso(), offset)},
    })
    response.headers["Cache-Control"] = "no-cache"
    return response


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
