"""The Flask app: a dashboard, a milestones feed, and the JSON behind them.

The charts are drawn in the browser with D3 (wom/web/static/charts.js); this
side only answers with data, so changing a period or a dropdown costs one
small fetch instead of a page load and a server-side render.
"""

import logging
import os
from datetime import datetime, timezone

from flask import (Flask, abort, jsonify, render_template, request, send_file,
                   send_from_directory)

from .. import periods, theme
from ..colors import player_color
from ..config import Config, DB_PATH
from ..db import Database
from ..icons import ASSET_DIR, icon_kind_for, icon_path
from ..scheduler import next_slot, parse_last_run
from ..util import fmt_ago, fmt_datetime, fmt_int, pretty_metric
from . import data as web_data

log = logging.getLogger(__name__)


def _paragraphs(text):
    """Split a summary into paragraphs for the template to wrap in <p>."""
    return [block.strip() for block in (text or "").split("\n\n") if block.strip()]


def create_app():
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["DATABASE"] = Database(DB_PATH)

    def settings():
        # Re-read each request so a change made in the desktop app - a new
        # colour, a new username - shows up without restarting the server.
        return Config()

    def roster(config):
        """Every tracked player, ordered like the desktop sidebar."""
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
                               selected={p["username"] for p in players})

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
        path = icon_path(metric, kind)
        if not os.path.exists(path):
            abort(404)
        response = send_file(path, mimetype="image/png")
        # Sprites never change; the axis of a 24-column chart asks for 24 of
        # them on every redraw, so let the browser keep them.
        response.headers["Cache-Control"] = "public, max-age=604800"
        return response

    @app.route("/assets/<path:name>")
    def asset(name):
        return send_from_directory(ASSET_DIR, name)

    @app.context_processor
    def helpers():
        # One palette for the page and the D3 charts alike.
        declarations = ["    {}: {};".format(name, value)
                        for name, value in theme.css_variables().items()]
        return {"now": datetime.now(timezone.utc),
                "css_variables": "\n".join(declarations)}

    return app
