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
from ..util import api_stamp as _stamp
from ..util import fmt_int, parse_api_time, pretty_metric


def is_whole_group(database, players):
    """Whether every tracked account is included.

    The round-up judged the whole group. Narrowed to some of them it is
    answering a different question from the one on screen, so its verdict
    stops overruling the figures.
    """
    everyone = database.players()
    return len(players) >= len(everyone) > 0


def standings(database, players, palette, when=None, board=winners.MAXING):
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
                                whole_group=whole_group, board=board)
    by_nine, by_xp = _month_wins(days, won)

    start_of_day = winners.today_range(when)[0]
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
            # Levels are read off the stored total rather than worked out
            # from experience: the level a skill is at is a column we already
            # keep, and deriving it again would be a second answer to a
            # question the reading has already answered.
            "levels": _levels_today(database, player, start_of_day),
            "capped": fmt_int(round(shown["capped"])),
            # What this board judges on, ready to print. Maxing counts
            # experience only up to ninety-nine; Grinding counts all of it.
            "score": fmt_int(round(
                shown["raw"] if board == winners.GRINDING else shown["capped"])),
            "moved": winners.moved(shown, board),
            "nine_wins": by_nine.get(player["username"], 0),
            "xp_wins": by_xp.get(player["username"], 0),
            # Ordered by the same rule the squares are, so the table reads as
            # the day's standings rather than as a second opinion.
            "rank": winners.key(shown, board),
        })
    rows.sort(key=lambda row: (row["rank"], row["name"]), reverse=True)
    for place, row in enumerate(rows, start=1):
        row["place"] = place
    return {"rows": rows, "month": start.strftime("%B %Y")}


def _levels_today(database, player, opens):
    """Total levels gained since midnight, or 0 if we cannot say.

    Read at the two edges the same way the Overview reads a window, and a
    missing edge means no answer rather than a guess - treated as zero the
    difference becomes the account's whole total level, which would report
    "+2,100 levels" for a quiet morning.
    """
    was = database.overall_at(player["id"], _stamp(opens))
    now = database.overall_at(player["id"])
    if not (was and was["level"] and now and now["level"]):
        return 0
    return max(0, now["level"] - was["level"])


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


def breakdown(database, player, when=None, board=winners.MAXING):
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

    grinding = board == winners.GRINDING
    moved = winners.measure_by_skill(before, after)
    rows = []
    for metric, shown in moved.items():
        rows.append({
            "metric": metric,
            "label": pretty_metric(metric),
            # What this board counts. Grinding counts everything, so there is
            # nothing "beyond" for it to set aside - saying otherwise would
            # print a caveat about a rule this board does not have.
            "capped": (shown["capped"] + shown["beyond"]) if grinding
                      else shown["capped"],
            "beyond": 0 if grinding else shown["beyond"],
            "reached_99": shown["reached_99"],
            "at_99": shown["at_99"],
        })
    # Most of what the day is judged on first.
    rows.sort(key=lambda row: (row["capped"], row["beyond"]), reverse=True)
    total = sum(row["capped"] for row in rows)
    beyond = sum(row["beyond"] for row in rows)
    nines = sum(1 for row in rows if row["reached_99"])
    return {"rows": rows, "total": total, "beyond": beyond, "nines": nines,
            "note": None if rows else "No skill has moved since midnight."}


def trend(database, players, color_for, when=None, board=winners.MAXING):
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
            # The judged figure first, whichever it is: the chart plots the
            # first number and the tooltip explains the second.
            judged = shown["raw"] if board == winners.GRINDING else shown["capped"]
            points.append([int(at.timestamp() * 1000), round(judged),
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


