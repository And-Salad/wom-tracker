"""Handing the stored readings back as a file.

One row per metric per reading. The whole history is tens of thousands of rows
and several megabytes, so it is generated and written a line at a time rather
than assembled and then sent.
"""

import csv
import io
import json
from datetime import datetime, timedelta

from flask import (Blueprint, Response, abort, current_app, render_template,
                   request, session)

from .selection import chosen, colors, database, roster, settings, status

exporting = Blueprint("exporting", __name__)

KINDS = (("skill", "Skills"), ("boss", "Bosses"), ("activity", "Activities"))
COLUMNS = ("captured_at", "player", "username", "kind", "metric",
           "value", "level", "rank")


class BadRequest(Exception):
    """Something in the query string cannot be honoured."""


@exporting.route("/export")
def export_page():
    config = settings()
    players = roster(config)
    return render_template("export.html", players=players,
                           colors=colors(config, players),
                           selected={p["username"] for p in players},
                           kinds=KINDS, status=status(config))


@exporting.route("/export.<fmt>")
def export_data(fmt):
    if fmt not in ("csv", "json"):
        abort(404)
    limits = current_app.config["LIMITS"]
    address, _source = limits.address()
    waiting, which = limits.export_allowed(address, bool(session.get("wom_admin")))
    if waiting:
        return _refused(limits, waiting, which)

    config = settings()
    kinds = [k for k in request.args.getlist("kind")
             if k in dict(KINDS)]
    offset = offset_minutes(request.args.get("tzoffset"))
    try:
        since = day_bound(request.args.get("from"), offset_minutes=offset)
        until = day_bound(request.args.get("to"), end_of_day=True,
                          offset_minutes=offset)
    except BadRequest as exc:
        return Response(str(exc), status=400, mimetype="text/plain")

    rows = database().export_rows(
        [p["id"] for p in chosen(roster(config), strict=True)],
        kinds=kinds, since=since, until=until)
    name = "wom-export-{}.{}".format(datetime.now().strftime("%Y%m%d"), fmt)
    stream = csv_stream(rows) if fmt == "csv" else json_stream(rows)
    return Response(stream,
                    mimetype="text/csv" if fmt == "csv" else "application/json",
                    headers={"Content-Disposition":
                             'attachment; filename="{}"'.format(name)})


def _refused(limits, waiting, which):
    hours = max(1, waiting // 3600)
    plural = "" if hours == 1 else "s"
    if which == "address":
        message = ("Exports are limited to {} per six hours. Try again in about "
                   "{} hour{}.".format(limits.exports_per_address, hours, plural))
    else:
        message = ("The daily export limit for everyone ({} a day) has been "
                   "reached. Sign in as admin, or try again in about {} hour{}."
                   .format(limits.exports_per_day, hours, plural))
    return Response(message, status=429, mimetype="text/plain",
                    headers={"Retry-After": str(waiting)})


# -- the query string -----------------------------------------------------

def day_bound(value, end_of_day=False, offset_minutes=0):
    """A date from the picker as the UTC stamp the rows are keyed by.

    Readings are stored in UTC but the picker hands over the viewer's local
    day, so the bound is shifted by their offset: without it an Eastern
    viewer's "to 30 August" stops at 20:00 their time and quietly drops that
    day's last reading.

    An unparseable date raises rather than returning None. None means "no
    bound", and treating a typo as no bound exports the whole history while
    looking filtered.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        day = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise BadRequest("{!r} is not a date. Use yyyy-mm-dd.".format(text))
    if end_of_day:
        day += timedelta(days=1)          # `to` is inclusive of the day named
    return (day - timedelta(minutes=offset_minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def offset_minutes(value):
    """The viewer's minutes east of UTC, as the page reports them."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return 0
    return minutes if -14 * 60 <= minutes <= 14 * 60 else 0


# -- writing it out -------------------------------------------------------

def safe_cell(value):
    """Defuse a text cell a spreadsheet would treat as a formula.

    Excel and Sheets run a cell beginning =, +, - or @. Player names come from
    the Wise Old Man API, so a hostile one would otherwise be a formula in
    everyone's download. Only text is touched; the numbers stay numbers.
    """
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def csv_stream(rows):
    """Yield the export a line at a time, so nothing is held whole in memory."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush():
        value = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return value

    writer.writerow(COLUMNS)
    yield flush()
    for row in rows:
        writer.writerow([row["captured_at"], safe_cell(row["display_name"]),
                         safe_cell(row["username"]), row["kind"],
                         safe_cell(row["metric"]), row["value"], row["level"],
                         row["rank"]])
        yield flush()


def json_stream(rows):
    """The same, as a JSON array built one element at a time."""
    yield "["
    first = True
    for row in rows:
        yield ("" if first else ",") + json.dumps({
            "captured_at": row["captured_at"], "player": row["display_name"],
            "username": row["username"], "kind": row["kind"],
            "metric": row["metric"], "value": row["value"],
            "level": row["level"], "rank": row["rank"]})
        first = False
    yield "]"
