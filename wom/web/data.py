"""Turns the database into the JSON the browser draws with D3.

The figures go to the client rather than being rendered here, so hovering and
swapping a dropdown happen in the page instead of costing a round trip.
"""

import logging
from datetime import datetime, timezone

from ..catalog import (BY_KEY, CHOICE_METRICS, COLLECTION_LOG, LOG_METRICS,
                       TOP_BOSSES, TOTAL_LEVEL, chart, specs)
from ..icons import SKILL_ORDER
from ..context import ViewContext
from ..periods import coverage_slack
from ..util import parse_api_time, pretty_metric

log = logging.getLogger(__name__)

# One wording for "nobody is ticked", shared with the endpoints in api.py -
# which already imports this module, so it lives here and travels up rather
# than being written out four times.
NOBODY_PICKED = "Include at least one player using the sidebar swatches."


def catalog():
    """The chart list the page builds its cards and dropdowns from."""
    return [spec.as_dict() for spec in specs()]


def build(database, config, key, span, players, choice=None):
    """One chart's data, or an {"empty": message} payload when there is none."""
    spec = BY_KEY.get(key)
    if spec is None or spec.build is None:
        return None
    if not players:
        return _empty(NOBODY_PICKED)
    ctx = ViewContext(database, config, players, selected=players,
                      span=span, choice=choice)
    try:
        return spec.build(ctx, choice)
    except Exception as exc:                      # one bad chart, not a bad page
        log.exception("chart data %s failed", key)
        return _empty("Chart failed: {}".format(exc))


def _empty(message):
    return {"empty": message}


# -- the four charts ------------------------------------------------------

def _levels_gained(ctx, player):
    """Total levels gained over the window.

    metric_gains returns the change in a metric's *value*, which for a skill
    is experience. Levels live in their own column, so they are read from the
    two bracketing snapshots directly.
    """
    start, end = ctx.bounds_for(player)
    if start is None or end is None or start["id"] == end["id"]:
        return 0
    levels = []
    for edge in (start, end):
        row = ctx.db.overall_at(edge["player_id"], edge["captured_at"])
        # No answer at an edge means no answer at all. Treated as level zero
        # the difference becomes the account's whole total level, so a missing
        # opening row would report "+2,100 levels" for a quiet week.
        if not (row and row["level"]):
            return 0
        levels.append(row["level"])
    return max(0, levels[1] - levels[0])


@chart("standings")
def _standings(ctx, _choice):
    """The one number per player the charts make you add up by eye.

    The stacked columns answer "what did they train"; nobody could read "who
    won" off them without summing twenty slices.
    """
    rows = []
    for player in ctx.selected:
        skills = ctx.gains(player, "skill")
        bosses = ctx.gains(player, "boss")
        rows.append({
            "username": player["username"],
            "name": player["display_name"],
            "color": ctx.color_for(player),
            "xp": round(sum(v for m, v in skills.items() if m != "overall")),
            "levels": _levels_gained(ctx, player),
            "kills": round(sum(bosses.values())),
        })
    if not any(r["xp"] or r["kills"] for r in rows):
        return _empty("Nobody gained anything in {}.".format(ctx.span.phrase))
    rows.sort(key=lambda r: -r["xp"])
    return {"type": "standings", "rows": rows,
            "coverage": _coverage(ctx, rows)}


@chart("skill_gains")
def _skill_gains(ctx, _choice):
    return _stacked(ctx, "skill", SKILL_ORDER, "Experience gained",
                    "experience gained",
                    "No experience gained by the included players in {}.")


@chart("boss_gains")
def _boss_gains(ctx, _choice):
    gains = {p["id"]: ctx.gains(p, "boss") for p in ctx.selected}
    totals = {}
    for per_player in gains.values():
        for metric, value in per_player.items():
            totals[metric] = totals.get(metric, 0.0) + value
    ranked = [m for m, _v in sorted(totals.items(), key=lambda kv: -kv[1])][:TOP_BOSSES]
    empty = "No boss kills by the included players in {}."
    if not ranked:
        return _empty(empty.format(ctx.span.phrase))
    return _stacked(ctx, "boss", ranked, "Kills gained", "kills gained", empty,
                    gains=gains)


@chart("level_trend")
def _level_trend(ctx, choice):
    choice = choice or TOTAL_LEVEL
    metric = CHOICE_METRICS.get(choice, "overall")
    return _trend(
        ctx, kind="skill", metric=metric, field="level",
        ylabel="{} level".format("Total" if metric == "overall"
                                 else pretty_metric(metric)),
        tooltip={"style": "level"},
        empty="No {} history for the included players in {{}}.".format(
            choice.lower()))


@chart("log_and_clues")
def _log_and_clues(ctx, choice):
    choice = choice or COLLECTION_LOG
    metric = LOG_METRICS.get(choice, "collections_logged")
    log_slots = metric == "collections_logged"
    return _trend(
        ctx, kind="activity", metric=metric, field="value",
        ylabel="Collection log slots" if log_slots
               else "{} completed".format(choice),
        tooltip={"style": "count", "unit": "slots" if log_slots else "completed"},
        empty="No {} history for the included players in {{}}.".format(
            choice.lower()))


# -- shared shapes --------------------------------------------------------

def _stacked(ctx, kind, metrics, ylabel, unit, empty, gains=None):
    """One column per metric, one slice per player who gained anything."""
    if gains is None:
        gains = {p["id"]: ctx.gains(p, kind) for p in ctx.selected}
    metrics = list(metrics)
    series = []
    for player in ctx.selected:
        values = [round(gains[player["id"]].get(metric, 0.0), 2) for metric in metrics]
        if not any(values):
            continue     # keep the legend to players who actually did something
        series.append({"username": player["username"],
                       "name": player["display_name"],
                       "color": ctx.color_for(player),
                       "values": values})
    if not series:
        return _empty(empty.format(ctx.span.phrase))
    return {
        "type": "stacked", "ylabel": ylabel, "unit": unit, "iconKind": kind,
        "metrics": [{"key": m, "label": pretty_metric(m)} for m in metrics],
        "series": series, "coverage": _coverage(ctx, series),
    }


def _coverage(ctx, series):
    """Name the players whose bars cover less than the period asks for.

    Wise Old Man only has the snapshots it has. A player it started watching
    three weeks ago still gets a bar on the Year chart, and without this the
    viewer has no way to know they are comparing a year against three weeks.
    """
    # Measured against the window, not against now: a range that closed in
    # June covers what it covers, and comparing it to today would report every
    # player as short of it.
    closes = (parse_api_time(ctx.span.until) if ctx.span.until
              else datetime.now(timezone.utc))
    opened = parse_api_time(ctx.span.since)
    asked = (closes - opened).total_seconds()
    notes = []
    for entry in series:
        player = next((p for p in ctx.selected
                       if p["username"] == entry["username"]), None)
        start = ctx.baseline(player) if player is not None else None
        if start is None:
            continue
        measured = parse_api_time(start["captured_at"])
        if (measured - opened).total_seconds() <= coverage_slack(asked):
            continue
        notes.append({"name": entry["name"], "color": entry["color"],
                      "since": measured.strftime("%d %b %Y"),
                      "days": max(1, int((closes - measured).total_seconds() // 86400))})
    return notes


def trend_series(database, players, color_for, kind, metric, field,
                 since, until=None, bucket=None):
    """One line per player for one metric, oldest point first.

    Shared by the Overview's fixed trends and the Data page's chart, which
    plots whichever metric the table is filtered to. Players with nothing to
    plot are left out rather than drawn as an empty legend entry.
    """
    series = []
    for player in players:
        rows = database.metric_history(player["id"], metric, kind, since=since,
                                       until=until, bucket=bucket)
        points = []
        for row in rows:
            when = parse_api_time(row["captured_at"])
            if when is None or row[field] is None:
                continue
            # [epoch ms, plotted value, the raw metric behind it] - the last
            # is the XP behind a level, and is what the tooltip spells out.
            points.append([int(when.timestamp() * 1000), row[field], row["value"]])
        if not points:
            continue
        series.append({"username": player["username"],
                       "name": player["display_name"],
                       "color": color_for(player),
                       "points": points})
    return series


def _trend(ctx, kind, metric, field, ylabel, tooltip, empty):
    """One line per player, sampled at whatever cadence the period wants."""
    since = ctx.span.since
    series = trend_series(ctx.db, ctx.selected, ctx.color_for, kind, metric,
                          field, since, ctx.span.until, bucket=ctx.span.bucket)
    if not series:
        return _empty(empty.format(ctx.span.phrase))
    start = parse_api_time(since)
    return {
        "type": "trend", "ylabel": ylabel, "tooltip": tooltip,
        # The baseline reading deliberately sits before the window; pin the
        # axis to the window itself so the line starts at the left edge.
        "since": int(start.timestamp() * 1000),
        "series": series,
    }
