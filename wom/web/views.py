"""Rows into view models.

These were inline in the route handlers, which made a route the only place the
shape of a page was decided and the only place it could be checked. They are
plain functions of (database, ...) so a test can call them without a request.
"""

from datetime import datetime, timezone

from .. import periods, theme
from ..util import fmt_ago, fmt_datetime, fmt_int, parse_api_time, pretty_metric

# The written summaries come in three flavours, named the same way everywhere.
SUMMARY_FOLDERS = (("day", "Daily"), ("week", "Weekly"), ("month", "Monthly"),
                   ("quarter", "Quarterly"), ("year", "Yearly"))

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


def latest_round_ups(database):
    """The newest round-up of each length, longest span last.

    They read as five different things rather than five versions of one: the
    daily and the weekly nearest it share only a few percent of their wording,
    because each picks out what stands out at its own scale.
    """
    out = []
    for period, title in SUMMARY_FOLDERS:
        rows = database.group_summaries(period=period, limit=1)
        if rows:
            out.append({"period": period, "title": title,
                        "label": rows[0]["label"],
                        "ago": fmt_ago(rows[0]["generated_at"]),
                        "paragraphs": paragraphs(rows[0]["text"])})
    return out


def player_note(database, player, period_key):
    """This player's newest note for one length of window, if there is one.

    The note is named by the window it covers, not by the period the page is
    set to. Those are different spans - the page's "Day" is the last twenty
    four hours, the note's is yesterday, midnight to midnight - and putting
    prose beside numbers invites the reader to assume otherwise.
    """
    rows = database.summaries(player_id=player["id"], period=period_key, limit=1)
    if not rows:
        return None
    return {"label": rows[0]["label"], "ago": fmt_ago(rows[0]["generated_at"]),
            "paragraphs": paragraphs(rows[0]["text"])}


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
    """One player's note, current figures, and what moved, grouped by kind."""
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
            "note": player_note(database, player, period.key),
            "writes_notes": period.key in periods.SUMMARY_PERIODS,
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


def metric_table(database, players, since, until, palette):
    """Every metric for the included players, and how far it moved.

    The same ground the export covers, answered in the page. A question like
    "who trained slayer this week" was previously a download and a spreadsheet;
    as rows it is a filter and a sort. One row per player per metric, so the
    browser can group it whichever way the viewer asks for.

    `until` closes the window. Where it stood is then read from the last
    reading inside the window rather than the newest one on file: a range that
    ended in June must not report June's gains against today's totals.
    """
    rows = []
    for player in players:
        start, end = database.snapshot_bounds(player["id"], since, until)
        if end is None:
            continue              # nothing on file for this player by then
        color = palette.get(player["username"], "")
        for kind, title in METRIC_GROUPS:
            gains = database.metric_gains(player["id"], since, kind,
                                          bounds=(start, end))
            for row in database.snapshot_metrics(end["id"], kind):
                if row["value"] is None and row["level"] is None:
                    continue      # unranked and never seen: not worth a line
                rows.append({
                    "player": player["display_name"],
                    "username": player["username"],
                    "color": color,
                    "kind": kind,
                    "kind_label": title,
                    "metric": row["metric"],
                    "label": pretty_metric(row["metric"]),
                    "level": row["level"],
                    "value": row["value"],
                    "rank": row["rank"],
                    "gained": round(gains.get(row["metric"], 0.0), 2),
                })
    return rows
