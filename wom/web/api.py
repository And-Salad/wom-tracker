"""The JSON the browser draws from, and the guard in front of it.

These are the endpoints that cost something: each one is real database work,
on a machine that also runs the update schedule.
"""

from flask import Blueprint, Response, abort, current_app, jsonify, session

from . import data as web_data
from . import views
from .selection import chosen, current_period, database, roster, settings

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
    from flask import request
    return request.args.get("choice")
