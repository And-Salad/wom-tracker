"""The day still in progress, on its own.

The calendar next to this is finished days: each one judged, coloured, and
done with. Today is a different thing and had been sharing a function with
it - the standings table was built inside winner_calendar, so anything that
wanted the running figures had to ask for two months of squares as well.

Three views of the same day, all measured by the rule in wom/winners.py so
they cannot disagree with the squares beside them:

    standings()   where everyone stands since midnight, and this month's wins
    breakdown()   one account's day, per skill, for an opened row
    trend()       the same experience as a line, midnight to midnight

"Toward 99" throughout means experience counted only up to level 99 in each
skill. Past it a skill stops levelling, so it is the measure that lets an
account still climbing compete with one that has already maxed.
"""

from .. import theme, winners
from ..util import fmt_int, parse_api_time, pretty_metric


def is_whole_group(database, players):
    """Whether every tracked account is included.

    The round-up judged the whole group. Narrowed to some of them it is
    answering a different question from the one on screen, so its verdict
    stops overruling the figures.
    """
    everyone = database.players()
    return len(players) >= len(everyone) > 0


def standings(database, players, palette, when=None):
    """Where everyone stands in the day now in progress, and this month.

    Deliberately not a verdict - today has not been polled to its end and
    cannot qualify yet - so it shows the running figures and lets the squares
    do the awarding.

    It counts the month's wins the same way the squares are coloured, down to
    whether a written round-up overruled the figures. Asked differently, the
    two halves of one card disagreed: a square in somebody's colour, and a
    tally beside it crediting the day to whoever the figures alone preferred.

    Which is why `whole_group` is worked out here rather than passed in. It
    was a parameter with a default, and a default is a way for one half of
    the card to be asked a different question from the other by accident -
    which is the very bug this function exists to have fixed. Both halves
    derive it from the same two counts, so they cannot drift apart.
    """
    whole_group = is_whole_group(database, players)
    start, end = winners.month_range(when, back=0)
    days = winners.gains_by_day(database, players, start, end)
    won = winners.daily_winners(database, players, start, end,
                                whole_group=whole_group)
    by_nine, by_xp = _month_wins(days, won)

    scores = days.get(winners.today_key(when), {}).get("scores", {})
    nothing = {"nines": 0, "raw": 0.0, "capped": 0.0}
    rows = []
    for player in players:
        shown = scores.get(player["username"], nothing)
        rows.append({
            "username": player["username"],
            "name": player["display_name"],
            "color": palette.get(player["username"], theme.MUTED),
            "nines": shown["nines"],
            "capped": fmt_int(round(shown["capped"])),
            "moved": winners.moved(shown),
            "nine_wins": by_nine.get(player["username"], 0),
            "xp_wins": by_xp.get(player["username"], 0),
            # Ordered by the same rule the squares are, so the table reads as
            # the day's standings rather than as a second opinion.
            "rank": winners.key(shown),
        })
    rows.sort(key=lambda row: (row["rank"], row["name"]), reverse=True)
    for place, row in enumerate(rows, start=1):
        row["place"] = place
    return {"rows": rows, "month": start.strftime("%B %Y")}


def _month_wins(days, won):
    """This month's finished days, split by how each was taken.

    A day is won either by reaching a ninety-nine or, where nobody did, on
    experience - so the days somebody won are worth splitting the same way.
    """
    by_nine, by_xp = {}, {}
    for day, found in won.items():
        # Leading at four in the afternoon is not a day won.
        if not found["winner"] or found["live"]:
            continue
        scored = days.get(day, {}).get("scores", {}).get(found["winner"])
        tally = by_nine if scored and scored["nines"] else by_xp
        tally[found["winner"]] = tally.get(found["winner"], 0) + 1
    return by_nine, by_xp


def breakdown(database, player, when=None):
    """One account's day so far, skill by skill.

    The row above it says how much; this says at what. Both come from
    winners.measure_by_skill against the same two readings the standings use,
    so the parts add up to the total rather than approximating it.
    """
    opens, closes = winners.today_range(when)
    # winners.day_span, not a baseline of our own: the row above this
    # breakdown is measured by that rule, and a breakdown that opens the day
    # somewhere else explains a figure it disagrees with.
    baseline, latest = winners.day_span(database, player["id"], opens, closes)
    before = baseline[1] if baseline else None
    after = latest[1] if latest else None
    if before is None or after is None:
        return {"rows": [], "total": 0, "beyond": 0, "nines": 0,
                "note": "Nothing has been read for this account today."}

    moved = winners.measure_by_skill(before, after)
    rows = []
    for metric, shown in moved.items():
        rows.append({
            "metric": metric,
            "label": pretty_metric(metric),
            "capped": shown["capped"],
            "beyond": shown["beyond"],
            "reached_99": shown["reached_99"],
            "at_99": shown["at_99"],
        })
    # Most experience toward 99 first: the column the day is judged on.
    rows.sort(key=lambda row: (row["capped"], row["beyond"]), reverse=True)
    total = sum(row["capped"] for row in rows)
    beyond = sum(row["beyond"] for row in rows)
    nines = sum(1 for row in rows if row["reached_99"])
    return {"rows": rows, "total": total, "beyond": beyond, "nines": nines,
            "note": None if rows else "No skill has moved since midnight."}


def trend(database, players, color_for, when=None):
    """Experience toward 99 since midnight, as one line per account.

    Cumulative rather than per-reading: the question the calendar asks is who
    is ahead, and a line that climbs answers it at a glance where a row of
    spikes does not. Each point is that account's total since midnight at the
    moment it was read, which is the same number the standings show for the
    last of them - the table is this chart's right-hand end.
    """
    opens, closes = winners.today_range(when)
    series = []
    for player in players:
        states = winners.skill_states(database, player["id"],
                                      _stamp(opens), _stamp(closes))
        if not states:
            continue
        # The same reading the row and its breakdown open the day from. Given
        # a baseline of its own this line ended somewhere the table beside it
        # did not, which for a chart whose caption says "the table is this
        # chart's right-hand end" is the one thing it must not do.
        baseline, _latest = winners.day_span(database, player["id"], opens, closes)
        if baseline is None:
            continue
        base = baseline[1]
        points = []
        for stamp, state in states:
            at = parse_api_time(stamp)
            if at is None:
                continue
            shown = winners.measure(base, state)
            points.append([int(at.timestamp() * 1000), round(shown["capped"]),
                           round(shown["raw"])])
        # A flat line at zero is worth drawing - it says the account was
        # watched and did nothing, which is not the same as being absent -
        # but one lone point is not a line, so it gets the midnight it
        # started from.
        if not points:
            continue
        first = int(opens.timestamp() * 1000)
        if points[0][0] > first:
            points.insert(0, [first, 0, 0])
        series.append({"username": player["username"],
                       "name": player["display_name"],
                       "color": color_for(player),
                       "points": points})
    if not series:
        return {"empty": "Nobody included has been read yet today."}
    return {
        "type": "trend",
        "ylabel": "XP toward 99",
        "tooltip": {"style": "count", "unit": "XP toward 99"},
        # Midnight to midnight, so the axis is the day rather than however
        # much of it has happened.
        "since": int(opens.timestamp() * 1000),
        "until": int(closes.timestamp() * 1000),
        # Which zone the axis is labelled in. This day is a calendar day in
        # the configured zone, so it is read in that zone by everyone: left
        # to the browser, a viewer a few hours away would see a chart whose
        # ends were labelled 05:00, on a card that says midnight to midnight.
        "offset": int(opens.utcoffset().total_seconds() // 60),
        "series": series,
    }


def _stamp(when):
    from datetime import timezone
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
