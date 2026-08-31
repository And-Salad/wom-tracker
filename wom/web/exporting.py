"""Handing the stored readings back as a file.

One row per metric per reading. The whole history is tens of thousands of rows
and several megabytes, so it is generated and written a line at a time rather
than assembled and then sent.
"""

import csv
import io
import json
from datetime import datetime

from flask import (Blueprint, Response, abort, current_app, render_template,
                   request, session)

from .dates import BadRequest, day_bound, offset_minutes
from .selection import chosen, database, roster, settings

exporting = Blueprint("exporting", __name__)

KINDS = (("skill", "Skills"), ("boss", "Bosses"), ("activity", "Activities"))
COLUMNS = ("captured_at", "player", "username", "kind", "metric",
           "value", "level", "rank")


@exporting.route("/export")
def export_page():
    from .selection import page_context
    from .pages import _shell
    scope = page_context()
    return render_template("export.html", kinds=KINDS, **_shell(scope))


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
