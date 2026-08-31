"""Rows into view models.

These were inline in the route handlers, which made a route the only place the
shape of a page was decided and the only place it could be checked. They are
plain functions of (database, ...) so a test can call them without a request.
"""

from datetime import datetime, timezone

from .. import theme
from ..util import fmt_ago, fmt_datetime, fmt_int, parse_api_time, pretty_metric

# The written summaries come in three flavours, named the same way everywhere.
SUMMARY_FOLDERS = (("day", "Daily"), ("week", "Weekly"), ("month", "Monthly"))

METRIC_GROUPS = (("skill", "Skills"), ("boss", "Bosses"),
                 ("activity", "Activities"))


def paragraphs(text):
    """Split a summary into paragraphs for the template to wrap in <p>."""
    return [block.strip() for block in (text or "").split("\n\n") if block.strip()]


def _folder(period, title, rows):
    return {
        "period": period, "title": title, "count": len(rows),
        "entries": [{"key": row["window_key"], "label": row["label"],
                     "ago": fmt_ago(row["generated_at"]),
                     "paragraphs": paragraphs(row["text"])}
                    for row in rows],
    }


def _branch(name, username, color, folders):
    return {"player": name, "username": username, "color": color,
            "total": sum(f["count"] for f in folders), "folders": folders}


def summary_tree(database, selected, palette):
    """The folder tree on /summaries: the group round-up, then each player.

    Both branches are the same shape, which is why they are built by the same
    two helpers rather than by two near-identical blocks.
    """
    tree = []
    group = [_folder(period, title, database.group_summaries(period=period))
             for period, title in SUMMARY_FOLDERS
             if database.group_summaries(period=period)]
    if group:
        tree.append(_branch("Group", "__group__", theme.ACCENT, group))

    for player in selected:
        folders = [_folder(period, title,
                           database.summaries(player_id=player["id"], period=period))
                   for period, title in SUMMARY_FOLDERS
                   if database.summaries(player_id=player["id"], period=period)]
        if folders:
            tree.append(_branch(player["display_name"], player["username"],
                                palette[player["username"]], folders))
    return tree


def latest_round_up(database):
    """The newest group summary, ready to read without opening anything.

    This is the thing the Claude spend buys, and it was two clicks down a tree
    whose folders stay shut - the template even marked the first leaf open,
    inside a folder that was not, so the intent never took effect.
    """
    newest = None
    for period, title in SUMMARY_FOLDERS:
        rows = database.group_summaries(period=period, limit=1)
        if rows and (newest is None or rows[0]["window_key"] > newest[0]["window_key"]):
            newest = (rows[0], title)
    if newest is None:
        return None
    row, title = newest
    return {"title": title, "label": row["label"],
            "ago": fmt_ago(row["generated_at"]),
            "paragraphs": paragraphs(row["text"])}


def milestone_feed(database, selected, palette, since=None, limit=300):
    """The achievements feed, newest first."""
    from ..icons import icon_kind_for

    feed = []
    for row in database.achievements(player_ids=[p["id"] for p in selected],
                                     since=since, limit=limit):
        dated = row["achieved_at"] and row["achieved_at"] > "1990"
        accuracy = row["accuracy"]
        vague = accuracy is None or accuracy < 0 or accuracy > 86400000
        feed.append({
            "when": (("~" if vague else "")
                     + fmt_datetime(row["achieved_at"], "%d %b %Y")) if dated
                    else "unknown",
            "ago": fmt_ago(row["achieved_at"]) if dated else "",
            "player": row["display_name"],
            "color": palette.get(row["username"], theme.MUTED),
            "name": row["name"],
            "metric": row["metric"],
            "kind": icon_kind_for(row["metric"]) if row["metric"] else None,
        })
    return feed


def player_rows(database, players, palette):
    """The table on /players: one row of headline figures each."""
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
    return rows


def player_detail(database, player, period):
    """One player's current figures and what moved, grouped by kind."""
    since = period.start_iso()
    bounds = database.snapshot_bounds(player["id"], since)

    groups = []
    for kind, title in METRIC_GROUPS:
        gains = database.metric_gains(player["id"], since, kind, bounds=bounds)
        rows = []
        for row in database.latest_snapshot_metrics(player["id"], kind):
            if row["value"] is None and row["level"] is None:
                continue          # unranked and never seen: not worth a line
            rows.append({
                "metric": row["metric"],
                "label": pretty_metric(row["metric"]),
                "value": row["value"],
                "level": row["level"],
                "rank": row["rank"],
                "gained": round(gains.get(row["metric"], 0.0), 2),
            })
        # What moved first, then the rest alphabetically: on a week's view most
        # of seventy boss rows are zeroes.
        rows.sort(key=lambda r: (-r["gained"], r["label"]))
        groups.append({"kind": kind, "title": title, "rows": rows,
                       "moved": sum(1 for r in rows if r["gained"])})

    return {"player": player["display_name"], "period": period.label,
            "coverage": coverage_note(bounds[0], since), "groups": groups}


def coverage_note(baseline, since):
    """How much of the window the figures actually cover, when it is not all.

    Wise Old Man only has the readings it has, and a player seen once yesterday
    still gets a "Week" column of gains. Without this a period nobody measured
    reads exactly like a quiet one.
    """
    if baseline is None:
        return {"short": True, "since": None, "days": 0,
                "note": "not measured in this period"}
    opened = parse_api_time(since)
    measured = parse_api_time(baseline["captured_at"])
    now = datetime.now(timezone.utc)
    asked = (now - opened).total_seconds()
    if (measured - opened).total_seconds() <= asked * 0.1:
        return {"short": False}          # slop for the six-hourly cadence
    covered = max(1, int((now - measured).total_seconds() // 86400))
    return {"short": True, "days": covered,
            "since": fmt_datetime(baseline["captured_at"], "%d %b %Y"),
            "note": "measured only from {} ({}d)".format(
                fmt_datetime(baseline["captured_at"], "%d %b %Y"), covered)}
