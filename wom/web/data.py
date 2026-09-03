"""Turns the database into the JSON the browser draws with D3.

The figures go to the client rather than being rendered here, so hovering and
swapping a dropdown happen in the page instead of costing a round trip.
"""

import logging
from datetime import datetime, timezone

from .. import winners
from ..catalog import (BY_KEY, CHOICE_METRICS, CLUE_TIERS, COLLECTION_LOG,
                       LOG_METRICS, TOP_BOSSES, TOTAL_LEVEL, chart, specs)
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


@chart("group_totals")
def _group_totals(ctx, _choice):
    """What the whole group did this period, as one row of figures.

    Every other card on this tab is per-account, which answers "who did what"
    and never "what did we do" - the figure somebody actually puts in the
    group chat. Each tile carries the per-account split behind it so the
    headline stays a headline and finding out who carried it is one hover.

    The two experience tiles are deliberately side by side. One is every
    point gained and the other is the part the leaderboard counts, and the
    gap between them is the experience that goes into skills already at 99 -
    invisible everywhere else on the site, and about one point in seven here.
    """
    tiles = [
        {"key": "levels", "label": "Levels gained", "format": "int",
         "note": "Across every skill"},
        {"key": "xp", "label": "XP gained", "format": "compact",
         "note": "Every skill, no 99 cap"},
        {"key": "xp99", "label": "XP toward 99", "format": "compact",
         "note": "What the leaderboard counts"},
        {"key": "kills", "label": "Boss kills", "format": "int",
         "note": "Every boss on the hiscores"},
        {"key": "collections", "label": "Collection log", "format": "int",
         "note": "New slots filled"},
        {"key": "clues", "label": "Clues completed", "format": "int",
         "note": "All tiers"},
    ]
    per_player = {tile["key"]: [] for tile in tiles}

    for player in ctx.selected:
        skills = ctx.gains(player, "skill")
        bosses = ctx.gains(player, "boss")
        activities = ctx.gains(player, "activity")
        found = {
            "levels": _levels_gained(ctx, player),
            "xp": round(sum(v for m, v in skills.items() if m != "overall")),
            "xp99": round(_toward_99(ctx, player)),
            "kills": round(sum(bosses.values())),
            "collections": round(activities.get("collections_logged", 0.0)),
            "clues": round(sum(activities.get(tier, 0.0) for tier in CLUE_TIERS)),
        }
        for key, value in found.items():
            per_player[key].append({
                "username": player["username"], "name": player["display_name"],
                "color": ctx.color_for(player), "value": value,
            })

    for tile in tiles:
        rows = per_player[tile["key"]]
        tile["total"] = sum(row["value"] for row in rows)
        # Biggest first: the tooltip is read to find out who carried it, and
        # display order buries that under whoever happens to be listed first.
        tile["rows"] = sorted(rows, key=lambda row: -row["value"])

    if not any(tile["total"] for tile in tiles):
        return _empty("Nobody gained anything in {}.".format(ctx.span.phrase))
    return {"type": "totals", "tiles": tiles,
            "coverage": _coverage(ctx, [{"username": p["username"],
                                         "name": p["display_name"],
                                         "color": ctx.color_for(p)}
                                        for p in ctx.selected])}


def _toward_99(ctx, player):
    """Experience gained counted only up to ninety-nine in each skill.

    Measured by winners.measure rather than re-derived here, so this tile and
    the Maxing tab cannot disagree about the same window - the rule that a
    skill stops earning at 13,034,431 lives in one place and both read it.
    """
    start, end = ctx.bounds_for(player)
    if start is None or end is None or start["id"] == end["id"]:
        return 0.0
    before = _skill_state(ctx, player["id"], start["captured_at"])
    after = _skill_state(ctx, player["id"], end["captured_at"])
    return winners.measure(before, after)["capped"]


def _skill_state(ctx, player_id, when):
    """{skill: experience} as it stood at one reading."""
    return {row["metric"]: row["value"]
            for row in ctx.db.state_at(player_id, when, "skill")
            if row["value"] is not None}


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


@chart("xp_trend")
def _xp_trend(ctx, _choice):
    """Every point of experience gained, counted from the start of the period.

    The only line on the site that plots experience over time is the Maxing
    tab's, and it measures experience *toward* ninety-nine - the right rule
    for the competition, since an account with everything maxed should not be
    able to take a day off people still climbing, and the wrong one for the
    question "how much did we actually do". An account training a maxed skill
    scores nothing there and draws a flat line through a real day's work.

    So this is the other number, and Overview is where it belongs: the
    standings card at the top of this tab already totals it per player, and
    nothing until now has plotted it.
    """
    payload = _trend(
        ctx, kind="skill", metric="overall", field="value",
        ylabel="XP gained",
        tooltip={"style": "count", "unit": "XP"},
        empty="No experience gained by the included players in {}.")
    if "series" in payload:
        found = {player["username"]: player for player in ctx.selected}
        payload["series"] = [_from_zero(ctx, found[s["username"]], s)
                             for s in payload["series"]]
    return payload


def _from_zero(ctx, player, series):
    """One player's line re-expressed as the change over the window.

    Total experience is not comparable between accounts - six of them here
    span 6.7M to 265M, so plotted raw the lines are six flat rows in reading
    order and the chart says nothing a sorted list would not. The gain is
    what the card is about, so every line starts at zero and the question
    becomes who moved, which is answerable by looking.

    Measured from the reading bounds_for already chose, which is the one the
    standings card at the top of this tab is measured from, so the two agree
    by construction rather than by coincidence. Subtracting the last reading
    at or before the boundary instead looks equivalent and is not: Wise Old
    Man's history has holes, and for an account whose previous reading is
    from 2022 that folds four years into "this month" - eighteen times the
    standings figure beside it, and enough to reorder the group.
    """
    start, _end = ctx.bounds_for(player)
    if start is None:
        return series
    opened = int(parse_api_time(start["captured_at"]).timestamp() * 1000)
    base = _skill_state(ctx, player["id"], start["captured_at"]).get("overall")
    if base is None:
        return series
    series = dict(series)
    # Readings before the one the gain is measured from would draw a negative
    # tail into the window, so the line starts where the measurement does.
    series["points"] = [[stamp, value - base, raw]
                        for stamp, value, raw in series["points"]
                        if stamp >= opened]
    return series


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
        gained_ylabel="Levels gained",
        gained_tooltip={"style": "count", "unit": "levels"},
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
        gained_ylabel="Slots gained" if log_slots
                      else "{} gained".format(choice),
        gained_tooltip={"style": "count",
                        "unit": "slots" if log_slots else "completed"},
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


def _trend(ctx, kind, metric, field, ylabel, tooltip, empty,
           gained_ylabel=None, gained_tooltip=None):
    """One line per player, sampled at whatever cadence the period wants.

    A card with modes is read two ways off this one payload - see
    catalog.TREND_MODES. The "Gained" reading is the same series minus the
    value it opened on, which the browser does; what it cannot do is name the
    axis, because "Total level" has to become "Levels gained" rather than
    growing a suffix. So both labels are settled here, next to each other.
    """
    since = ctx.span.since
    series = trend_series(ctx.db, ctx.selected, ctx.color_for, kind, metric,
                          field, since, ctx.span.until, bucket=ctx.span.bucket)
    if not series:
        return _empty(empty.format(ctx.span.phrase))
    start = parse_api_time(since)
    return {
        "type": "trend", "ylabel": ylabel, "tooltip": tooltip,
        "ylabelGained": gained_ylabel or "{} gained".format(ylabel),
        "tooltipGained": gained_tooltip or {"style": "count", "unit": ""},
        # The baseline reading deliberately sits before the window; pin the
        # axis to the window itself so the line starts at the left edge.
        "since": int(start.timestamp() * 1000),
        "series": series,
    }
