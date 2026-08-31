"""The Flask app: a dashboard, a milestones feed, and the JSON behind them.

The charts are drawn in the browser with D3 (wom/web/static/charts.js); this
side only answers with data, so changing a period or a dropdown costs one
small fetch instead of a page load and a server-side render.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import (Flask, Response, abort, jsonify, render_template, request,
                   send_file, send_from_directory)
from werkzeug.middleware.proxy_fix import ProxyFix

from .. import periods, theme
from ..colors import player_color
from ..config import Config, DB_PATH
from ..db import Database
from ..icons import ASSET_DIR, icon_kind_for, icon_path
from ..scheduler import next_slot, parse_last_run
from ..util import (fmt_ago, fmt_datetime, fmt_int, parse_api_time,
                    pretty_metric)
from . import data as web_data
from .admin import PASSWORD_ENV, admin as admin_blueprint, admin_enabled
from .jobs import JobRunner

log = logging.getLogger(__name__)


class _Budget:
    """Calls allowed per key over a window. One instance per bucket."""

    def __init__(self, allowance, window):
        self.allowance = allowance
        self.window = window
        self._seen = {}
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self._seen.clear()

    def check(self, key):
        """Seconds to wait, or 0 if a call under this key may go ahead.

        Only a call that is going ahead is recorded, so a request refused by
        another bucket does not eat this one's allowance.
        """
        now = time.monotonic()
        with self._lock:
            calls = [t for t in self._seen.get(key, ()) if now - t < self.window]
            self._seen[key] = calls
            if len(calls) >= self.allowance:
                return max(1, int(self.window - (now - calls[0])))
            return 0

    def record(self, key):
        now = time.monotonic()
        with self._lock:
            calls = [t for t in self._seen.get(key, ()) if now - t < self.window]
            calls.append(now)
            self._seen[key] = calls
            if len(self._seen) > 1024:    # addresses that stopped asking
                self._seen = {k: v for k, v in self._seen.items() if v}


# A full export is about 5 MB of egress and walks every stored reading, on a
# machine that also runs the schedule. Nobody browsing needs many: five per
# viewer per six hours, and twenty a day across everyone as the backstop, is
# roughly 100 MB a day at today's size. Signing in as admin skips both.
EXPORTS_PER_ADDRESS = 5
EXPORT_ADDRESS_WINDOW = 6 * 3600
EXPORTS_PER_DAY = 20
EXPORT_DAY_WINDOW = 24 * 3600
_EVERYONE = "*"

_export_per_address = _Budget(EXPORTS_PER_ADDRESS, EXPORT_ADDRESS_WINDOW)
_export_overall = _Budget(EXPORTS_PER_DAY, EXPORT_DAY_WINDOW)


def _export_allowed(address, is_admin):
    """(seconds_to_wait, which_limit). Admin is not budgeted."""
    if is_admin:
        return 0, None
    waiting = _export_per_address.check(address)
    if waiting:
        return waiting, "address"
    waiting = _export_overall.check(_EVERYONE)
    if waiting:
        return waiting, "everyone"
    _export_per_address.record(address)
    _export_overall.record(_EVERYONE)
    return 0, None


class BadRequest(Exception):
    """Something in the query string cannot be honoured."""


def _day_bound(value, end_of_day=False, offset_minutes=0):
    """A date from the picker as the UTC stamp the rows are keyed by.

    Readings are stored in UTC but the picker hands over the viewer's local
    day, so the bound is shifted by their offset: without it an Eastern
    viewer's "to 30 August" stops at 20:00 their time and quietly drops that
    day's 18:00 reading.

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
        day += timedelta(days=1)      # `to` is inclusive of the day named
    return (day - timedelta(minutes=offset_minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def _offset_minutes(value):
    """The viewer's minutes east of UTC, as the page reports them."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return 0
    return minutes if -14 * 60 <= minutes <= 14 * 60 else 0


def _safe_cell(value):
    """Defuse a text cell a spreadsheet would treat as a formula.

    Excel and Sheets run a cell beginning =, +, - or @. Player names come from
    the Wise Old Man API, so a hostile one would otherwise be a formula in
    everyone's download. Only text is touched; the numbers stay numbers.
    """
    text = "" if value is None else str(value)
    dangerous = ("=", "+", "-", "@", "\t", "\r")
    return "'" + text if text[:1] in dangerous else text


def _csv_stream(rows):
    """Yield the export a line at a time, so nothing is held whole in memory."""
    import csv
    import io as _io

    buffer = _io.StringIO()
    writer = csv.writer(buffer)

    def flush():
        value = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return value

    writer.writerow(["captured_at", "player", "username", "kind", "metric",
                     "value", "level", "rank"])
    yield flush()
    for row in rows:
        writer.writerow([row["captured_at"], _safe_cell(row["display_name"]),
                         _safe_cell(row["username"]), row["kind"],
                         _safe_cell(row["metric"]), row["value"], row["level"],
                         row["rank"]])
        yield flush()


def _json_stream(rows):
    """The same, as a JSON array built one element at a time."""
    import json as _json
    yield "["
    first = True
    for row in rows:
        yield ("" if first else ",") + _json.dumps({
            "captured_at": row["captured_at"], "player": row["display_name"],
            "username": row["username"], "kind": row["kind"],
            "metric": row["metric"], "value": row["value"],
            "level": row["level"], "rank": row["rank"]})
        first = False
    yield "]"


# Set by the proxy in front of us, and not passed through from the client, so
# these can be believed where a bare X-Forwarded-For cannot.
CLIENT_IP_HEADERS = ("Fly-Client-IP", "CF-Connecting-IP", "True-Client-IP")


def client_address():
    """The caller's address as well as we can know it, and where it came from.

    Behind a proxy `remote_addr` is the proxy, which would put every visitor
    in one throttling bucket: six bad sign-ins from anyone would lock out
    everyone. Returns (address, source) so a log line can say which header
    answered.
    """
    for header in CLIENT_IP_HEADERS:
        value = (request.headers.get(header) or "").strip()
        if value:
            return value, header
    # Leftmost is the original client. Note that waitress strips X-Forwarded-*
    # unless it is told to trust a proxy, so on Fly this is a fallback for
    # other deployments rather than the path normally taken - Fly-Client-IP is
    # not a forwarded header and comes through untouched.
    forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip(), "X-Forwarded-For"
    return request.remote_addr or "?", "remote_addr"


def _coverage_note(baseline, since):
    """How much of the window the figures actually cover, when it is not all.

    Wise Old Man only has the readings it has, and a player it saw once
    yesterday still gets a "Week" column of gains. The charts caption this and
    the written summaries spell it out; without it here a period nobody
    measured reads exactly like a quiet one.
    """
    if baseline is None:
        return {"short": True, "since": None, "days": 0,
                "note": "not measured in this period"}
    opened = parse_api_time(since)
    measured = parse_api_time(baseline["captured_at"])
    asked = (datetime.now(timezone.utc) - opened).total_seconds()
    inside = (measured - opened).total_seconds()
    if inside <= asked * 0.1:            # slop for the six-hourly cadence
        return {"short": False}
    covered = max(1, int((datetime.now(timezone.utc) - measured).total_seconds()
                         // 86400))
    return {"short": True, "days": covered,
            "since": fmt_datetime(baseline["captured_at"], "%d %b %Y"),
            "note": "measured only from {} ({}d)".format(
                fmt_datetime(baseline["captured_at"], "%d %b %Y"), covered)}


def _paragraphs(text):
    """Split a summary into paragraphs for the template to wrap in <p>."""
    return [block.strip() for block in (text or "").split("\n\n") if block.strip()]


def create_app():
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    # Only for the scheme: Fly terminates TLS, so without this every request
    # looks like plain HTTP and the HSTS header never goes out. The client
    # address is resolved by client_address() instead - ProxyFix reads the
    # rightmost X-Forwarded-For entry, which behind Fly is Fly's own hop.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1)
    app.config["DATABASE"] = Database(DB_PATH)
    app.config["JOBS"] = JobRunner()
    # Set by web_app.py when it starts the scheduler; None when the dashboard
    # is served without one, in which case there is nothing to collide with.
    app.config.setdefault("SCHEDULER", None)
    _export_per_address.reset()
    _export_overall.reset()

    # Admin is registered only when a password exists. A deployment that
    # forgets to set one has no admin routes at all, rather than open ones.
    app.config["ADMIN"] = admin_enabled()
    if app.config["ADMIN"]:
        app.secret_key = _session_key()
        app.permanent_session_lifetime = timedelta(days=14)
        # Every admin action is a form POST authenticated by this cookie alone.
        # Lax is what stops another site POSTing one on a signed-in viewer's
        # behalf; browsers default to it, but that is their choice rather than
        # ours until it is said here. Secure because fly.toml forces HTTPS.
        app.config.update(SESSION_COOKIE_SAMESITE="Lax",
                          SESSION_COOKIE_HTTPONLY=True,
                          SESSION_COOKIE_SECURE=_https_only())
        app.register_blueprint(admin_blueprint)
    else:
        log.warning("%s is not set: the admin pages are disabled", PASSWORD_ENV)

    def settings():
        # Re-read each request so a change made under /admin - a new colour,
        # a new username - shows up without restarting the server.
        return Config()

    def roster(config):
        """Every tracked player, in the order the settings list them."""
        database = app.config["DATABASE"]
        stored = {row["username"]: row for row in database.players()}
        ordered = []
        for name in config.get("usernames", []):
            row = stored.pop(name.lower(), None)
            if row is not None:
                ordered.append(row)
        ordered.extend(stored.values())
        return ordered

    def chosen(config, players, strict=False):
        """The players the query string asks for.

        A bare URL with no ?player= means everyone, so a link to the dashboard
        works. `strict` turns off that fallback for the chart data, where an
        empty list means the viewer really has unticked every box and should
        be told so rather than shown the whole roster back.
        """
        wanted = request.args.getlist("player")
        if not wanted:
            return [] if strict and request.args.get("picked") else players
        wanted = {w.lower() for w in wanted}
        picked = [p for p in players if p["username"] in wanted]
        return picked if strict else (picked or players)

    def colors(config, players):
        return {p["username"]: player_color(config, p["username"], index)
                for index, p in enumerate(players)}

    def status(config):
        last = parse_last_run(config.get("last_run", ""))
        return {
            "last": fmt_ago(last.isoformat()) if last else "never",
            "next": next_slot().astimezone().strftime("%a %H:%M"),
            "players": len(config.get("usernames", [])),
        }

    # -- pages ------------------------------------------------------------

    @app.route("/")
    def dashboard():
        config = settings()
        players = roster(config)
        selected = chosen(config, players)
        period = periods.by_label(request.args.get("period", "").title() or "Week")
        return render_template(
            "dashboard.html", players=players,
            selected={p["username"] for p in selected},
            colors=colors(config, players), periods=periods.labels(),
            period=period, specs=web_data.catalog(), status=status(config))

    @app.route("/milestones")
    def milestones():
        config = settings()
        database = app.config["DATABASE"]
        players = roster(config)
        selected = chosen(config, players)
        label = request.args.get("period", "All time")
        period = None if label == "All time" else periods.by_label(label)
        rows = database.achievements(
            player_ids=[p["id"] for p in selected],
            since=period.start_iso() if period else None, limit=300)
        feed = []
        palette = colors(config, players)
        for row in rows:
            dated = row["achieved_at"] and row["achieved_at"] > "1990"
            accuracy = row["accuracy"]
            vague = accuracy is None or accuracy < 0 or accuracy > 86400000
            feed.append({
                "when": (("~" if vague else "") + fmt_datetime(row["achieved_at"], "%d %b %Y"))
                        if dated else "unknown",
                "ago": fmt_ago(row["achieved_at"]) if dated else "",
                "player": row["display_name"],
                "color": palette.get(row["username"], theme.MUTED),
                "name": row["name"],
                "metric": row["metric"],
                "kind": icon_kind_for(row["metric"]) if row["metric"] else None,
            })
        return render_template(
            "milestones.html", players=players,
            selected={p["username"] for p in selected}, colors=palette,
            periods=["All time"] + periods.labels(), period=label,
            feed=feed, status=status(config))

    @app.route("/summaries")
    def summaries_page():
        config = settings()
        database = app.config["DATABASE"]
        players = roster(config)
        selected = chosen(config, players)
        palette = colors(config, players)

        tree = []
        group_folders = []
        for period, title in (("day", "Daily"), ("week", "Weekly"),
                              ("month", "Monthly")):
            rows = database.group_summaries(period=period)
            if rows:
                group_folders.append({
                    "period": period, "title": title, "count": len(rows),
                    "entries": [{"key": r["window_key"], "label": r["label"],
                                 "ago": fmt_ago(r["generated_at"]),
                                 "paragraphs": _paragraphs(r["text"])}
                                for r in rows],
                })
        if group_folders:
            tree.append({
                "player": "Group", "username": "__group__",
                "color": theme.ACCENT,
                "total": sum(f["count"] for f in group_folders),
                "folders": group_folders,
            })

        for player in selected:
            folders = []
            for period, title in (("day", "Daily"), ("week", "Weekly"),
                                  ("month", "Monthly")):
                rows = database.summaries(player_id=player["id"], period=period)
                if not rows:
                    continue
                folders.append({
                    "period": period, "title": title, "count": len(rows),
                    "entries": [{
                        "key": row["window_key"],
                        "label": row["label"],
                        "ago": fmt_ago(row["generated_at"]),
                        "paragraphs": _paragraphs(row["text"]),
                    } for row in rows],
                })
            if folders:
                tree.append({
                    "player": player["display_name"],
                    "username": player["username"],
                    "color": palette[player["username"]],
                    "total": sum(f["count"] for f in folders),
                    "folders": folders,
                })
        return render_template("summaries.html", players=players,
                               selected={p["username"] for p in selected},
                               colors=palette, tree=tree,
                               status=status(config))

    @app.route("/players")
    def players_page():
        config = settings()
        database = app.config["DATABASE"]
        players = roster(config)
        palette = colors(config, players)
        rows = []
        for player in players:
            overall = database.query_one(
                "SELECT level FROM metrics WHERE player_id=? AND kind='skill'"
                " AND metric='overall' ORDER BY captured_at DESC LIMIT 1",
                (player["id"],))
            rows.append({
                "name": player["display_name"],
                "username": player["username"],
                "color": palette[player["username"]],
                "type": pretty_metric(player["type"] or "-"),
                "combat": fmt_int(player["combat_level"]),
                "total_level": fmt_int(overall["level"] if overall else None),
                "exp": fmt_int(player["exp"]),
                "ehp": fmt_int(player["ehp"]),
                "ehb": fmt_int(player["ehb"]),
                "updated": fmt_ago(player["updated_at"]),
                "snapshots": fmt_int(database.snapshot_count(player["id"])),
            })
        return render_template("players.html", rows=rows, status=status(config),
                               players=players, colors=palette,
                               periods=periods.labels(),
                               period=periods.by_label(
                                   request.args.get("period", "").title() or "Week"),
                               selected={p["username"] for p in players})

    @app.route("/api/player/<username>")
    def player_detail(username):
        """One player's current figures and what moved, for the expanding rows.

        Fetched when a row is opened rather than rendered with the page: six
        players' worth of every skill, boss and activity is a few hundred
        kilobytes nobody has asked to see yet.
        """
        database = app.config["DATABASE"]
        player = database.player_by_username(username)
        if player is None:
            abort(404)
        period = periods.by_label(request.args.get("period", "").title() or "Week")
        since = period.start_iso()
        bounds = database.snapshot_bounds(player["id"], since)

        groups = []
        for kind, title in (("skill", "Skills"), ("boss", "Bosses"),
                            ("activity", "Activities")):
            gains = database.metric_gains(player["id"], since, kind, bounds=bounds)
            rows = []
            for row in database.latest_snapshot_metrics(player["id"], kind):
                if row["value"] is None and row["level"] is None:
                    continue        # unranked and never seen: not worth a line
                rows.append({
                    "metric": row["metric"],
                    "label": pretty_metric(row["metric"]),
                    "value": row["value"],
                    "level": row["level"],
                    "rank": row["rank"],
                    "gained": round(gains.get(row["metric"], 0.0), 2),
                })
            # What moved first, then the rest alphabetically: on a week's view
            # most of a hundred boss rows are zeroes.
            rows.sort(key=lambda r: (-r["gained"], r["label"]))
            groups.append({"kind": kind, "title": title, "rows": rows,
                           "moved": sum(1 for r in rows if r["gained"])})

        return jsonify({
            "player": player["display_name"],
            "period": period.label,
            "coverage": _coverage_note(bounds[0], since),
            "groups": groups,
        })

    # -- exporting ---------------------------------------------------------

    @app.route("/export")
    def export_page():
        config = settings()
        players = roster(config)
        return render_template(
            "export.html", players=players, colors=colors(config, players),
            selected={p["username"] for p in players},
            kinds=[("skill", "Skills"), ("boss", "Bosses"),
                   ("activity", "Activities")],
            status=status(config))

    @app.route("/export.<fmt>")
    def export_data(fmt):
        if fmt not in ("csv", "json"):
            abort(404)
        # A full export walks every stored reading, and the scheduler and the
        # summary writer are threads in this same process on one shared vCPU.
        from flask import session as _session
        address, _source = client_address()
        waiting, which = _export_allowed(address,
                                         bool(_session.get("wom_admin")))
        if waiting:
            hours = max(1, waiting // 3600)
            return Response(
                ("Exports are limited to {} per six hours. Try again in about "
                 "{} hour{}.".format(EXPORTS_PER_ADDRESS, hours,
                                     "" if hours == 1 else "s")
                 if which == "address" else
                 "The daily export limit for everyone ({} a day) has been "
                 "reached. Sign in as admin, or try again in about {} hour{}."
                 .format(EXPORTS_PER_DAY, hours, "" if hours == 1 else "s")),
                status=429, mimetype="text/plain",
                headers={"Retry-After": str(waiting)})
        config = settings()
        database = app.config["DATABASE"]
        chosen_players = chosen(config, roster(config), strict=True)
        kinds = [k for k in request.args.getlist("kind")
                 if k in ("skill", "boss", "activity")]
        offset = _offset_minutes(request.args.get("tzoffset"))
        try:
            since = _day_bound(request.args.get("from"), offset_minutes=offset)
            until = _day_bound(request.args.get("to"), end_of_day=True,
                               offset_minutes=offset)
        except BadRequest as exc:
            return Response(str(exc), status=400, mimetype="text/plain")
        rows = database.export_rows([p["id"] for p in chosen_players],
                                    kinds=kinds, since=since, until=until)
        name = "wom-export-{}.{}".format(
            datetime.now().strftime("%Y%m%d"), fmt)
        stream = _csv_stream(rows) if fmt == "csv" else _json_stream(rows)
        return Response(stream, mimetype=(
            "text/csv" if fmt == "csv" else "application/json"), headers={
                "Content-Disposition": 'attachment; filename="{}"'.format(name)})

    # -- data -------------------------------------------------------------

    @app.route("/api/chart/<key>")
    def chart_data(key):
        config = settings()
        players = chosen(config, roster(config), strict=True)
        period = periods.by_label(request.args.get("period", "").title() or "Week")
        payload = web_data.build(app.config["DATABASE"], config, key, period,
                                 players, request.args.get("choice"))
        if payload is None:
            abort(404)
        response = jsonify(payload)
        # Only an update changes these numbers; a reload should still re-ask.
        response.headers["Cache-Control"] = "no-cache"
        return response

    # -- images -----------------------------------------------------------

    @app.route("/icon/<kind>/<metric>.png")
    def icon(kind, metric):
        if kind not in ("skill", "boss", "activity"):
            abort(404)
        # icon_path returns None for anything that is not a metric name, which
        # is what stops a crafted URL walking out of the asset directory.
        path = icon_path(metric, kind)
        if path is None or not os.path.exists(path):
            abort(404)
        response = send_file(path, mimetype="image/png")
        # Sprites never change; the axis of a 24-column chart asks for 24 of
        # them on every redraw, so let the browser keep them.
        response.headers["Cache-Control"] = "public, max-age=604800"
        return response

    @app.route("/assets/<path:name>")
    def asset(name):
        return send_from_directory(ASSET_DIR, name)

    @app.after_request
    def harden(response):
        """Headers the browser should enforce, since the link is public.

        The 301 to HTTPS does not protect a first plain-HTTP visit, nothing
        stopped the admin page being framed, and no policy said where scripts
        may come from. Inline script is forbidden outright - the two pages that
        had any now load it from /static - while inline *styles* are allowed,
        because the templates colour swatches that way and a style attribute
        cannot execute.
        """
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.context_processor
    def helpers():
        # One palette for the page and the D3 charts alike.
        declarations = ["    {}: {};".format(name, value)
                        for name, value in theme.css_variables().items()]
        from flask import session
        return {"now": datetime.now(timezone.utc),
                "css_variables": "\n".join(declarations),
                "admin_enabled": app.config["ADMIN"],
                "signed_in": bool(session.get("wom_admin")),
                # The header carries this on every page, admin included, so it
                # is supplied here rather than by each view in turn.
                "status": status(settings())}

    return app


def _https_only():
    """Mark the cookie Secure unless this is a plain-HTTP local run."""
    return os.environ.get("WOM_INSECURE_COOKIE", "").strip().lower() not in (
        "1", "true", "yes")


def _session_key():
    """The key that signs the admin cookie.

    Set WOM_SECRET_KEY to keep sessions alive across restarts. Without one a
    fresh key is minted per process, which is safe but signs everyone out
    whenever the server restarts.
    """
    from os import urandom
    given = os.environ.get("WOM_SECRET_KEY", "").strip()
    if given:
        return given
    log.info("WOM_SECRET_KEY is not set; admin sessions end when this "
             "process does")
    return urandom(32)
