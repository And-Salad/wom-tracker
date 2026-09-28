"""The day still in progress, on its own.

The calendar next to this is finished days: each one judged, coloured, and
done with. Today is a different thing and had been sharing a function with
it - the standings table was built inside winner_calendar, so anything that
wanted the running figures had to ask for two months of squares as well.

Three views of the same day, all measured by the rule in wom/winners.py so
they cannot disagree with the squares beside them:

    standings()   where everyone stands since midnight, and this month's wins
    breakdown()   one account's day, per skill, for an opened row
    trend()       the same experience as a line, midnight to midnight -
                  for today, or for any finished day picked off the calendar

"Toward 99" throughout means experience counted only up to level 99 in each
skill. Past it a skill stops levelling, so it is the measure that lets an
account still climbing compete with one that has already maxed.
"""

from .. import theme, winners
from ..util import api_stamp as _stamp
from ..util import fmt_int, parse_api_time, pretty_metric


def standings(database, players, palette, when=None, board=winners.MAXING,
              readings=None):
    """Where everyone stands on one day, and in its month up to it.

    Today unless `when` falls in another day - a square picked off the
    calendar. Today's figures are deliberately not a verdict: it has not been
    polled to its end and cannot qualify yet. A finished day's are the ones
    its square was judged on, and `verdict` says what that square says.

    It counts the month's wins the same way the squares are coloured, from
    the same daily verdicts. Asked differently, the two halves of one card
    disagreed: a square in somebody's colour, and a tally beside it
    crediting the day to somebody else. For a finished day they are counted
    up to and including it, so the page reads as it did that evening rather
    than mixing that day's figures with a tally from weeks later.
    """
    walk = readings if readings is not None else winners.Readings(
        database, players)
    start, end = winners.month_range(when, back=0)
    days = walk.days(start, end)
    won = winners.daily_winners(database, players, start, end, board=board,
                                readings=walk)
    chosen = winners.today_key(when)
    by_nine, by_xp = _month_wins(
        days, {day: found for day, found in won.items() if day <= chosen})

    start_of_day, end_of_day = winners.today_range(when)
    label = day_label(when)
    # Levels are read at the day's close, which for today is "now".
    closes = None if label == "today" else end_of_day
    scores = days.get(chosen, {}).get("scores", {})
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
            "levels": _levels_today(database, player, start_of_day, closes),
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
    # Ties go the way the square breaks them - by username, not by the name
    # shown - or a dead heat would head the table with the account the
    # square did not give the day to.
    rows.sort(key=lambda row: (row["rank"], row["username"]), reverse=True)
    for place, row in enumerate(rows, start=1):
        row["place"] = place
    return {"rows": rows, "month": start.strftime("%B %Y"),
            "month_name": start.strftime("%B"), "label": label,
            # Under a column heading, where "on Sun 30 Aug" is too long.
            "short": "Today" if label == "today" else "{} {}".format(
                start_of_day.day, start_of_day.strftime("%b")),
            "day": chosen, "verdict": _verdict(won.get(chosen), players)}


def _verdict(found, players):
    """What the square for a finished day says, in words; None for today.

    The table under a picked day is that day's figures, and a square can be
    blank for reasons the figures do not show - nobody was polled, or not
    everybody was tracked yet. Saying so beside them is what stops a table
    with a clear leader reading as a day that leader won.
    """
    if found is None or found["live"]:
        return None
    if found["winner"]:
        names = {p["username"]: p["display_name"] for p in players}
        return "Taken by {}.".format(names.get(found["winner"], found["winner"]))
    return "Not awarded: {}.".format(found["reason"] or "no result")


def _levels_today(database, player, opens, closes=None):
    """Total levels gained since midnight, or 0 if we cannot say.

    Up to `closes` for a finished day, and up to now for today.

    Read at the two edges the same way the Overview reads a window, and a
    missing edge means no answer rather than a guess - treated as zero the
    difference becomes the account's whole total level, which would report
    "+2,100 levels" for a quiet morning.
    """
    was = database.overall_at(player["id"], _stamp(opens))
    now = database.overall_at(player["id"],
                              _stamp(closes) if closes is not None else None)
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
    label = day_label(when)
    # winners.day_span, not a baseline of our own: the row above this
    # breakdown is measured by that rule, and a breakdown that opens the day
    # somewhere else explains a figure it disagrees with.
    baseline, latest = winners.day_span(database, player["id"], opens, closes)
    before = baseline[1] if baseline else None
    after = latest[1] if latest else None
    if before is None or after is None:
        return {"rows": [], "total": 0, "beyond": 0, "nines": 0,
                "label": label,
                "note": "Nothing was read for this account {}.".format(label)
                if label != "today"
                else "Nothing has been read for this account today."}

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
            "label": label,
            "note": None if rows else
            "No skill has moved since midnight." if label == "today"
            else "No skill moved {}.".format(label)}


def day_label(when=None):
    """How a chart names the day it is drawing: "today", or "on Sat 20 Sep".

    Said by the server rather than put together in the browser, because which
    day is today is a question about the configured zone, not about the
    clock of whoever is reading.
    """
    opens = winners.today_range(when)[0]
    if opens.strftime("%Y-%m-%d") == winners.today_key():
        return "today"
    # No year: the calendar it is picked from holds two months. The day is
    # written as a number rather than with %d, which pads it with a zero.
    return "on {} {} {}".format(opens.strftime("%a"), opens.day,
                                opens.strftime("%b"))


def trend(database, players, color_for, when=None, board=winners.MAXING):
    """Experience toward 99 since midnight, as one line per account.

    Cumulative rather than per-reading: the question the calendar asks is who
    is ahead, and a line that climbs answers it at a glance where a row of
    spikes does not. Each point is that account's total since midnight at the
    moment it was read, which is the same number the standings show for the
    last of them - the table is this chart's right-hand end.

    `when` picks the day: any moment in it, today if left out. A finished day
    is drawn the same way from the same readings its square was judged on,
    so the line that ends highest is the square's colour.
    """
    opens, closes = winners.today_range(when)
    label = day_label(when)
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
        return {"empty": "Nobody included has been read yet today."
                if label == "today"
                else "Nobody included was read {}.".format(label),
                "day": opens.strftime("%Y-%m-%d"), "label": label}
    # Named for what this board counts: Grinding's axis read "toward 99" for
    # as long as both boards shared this function, and it has no such rule.
    unit = "XP gained" if board == winners.GRINDING else "XP toward 99"
    return {
        "type": "trend",
        # Which day this is, and what to call it - the card's title and
        # caption follow it, and the calendar marks the square it belongs to.
        "day": opens.strftime("%Y-%m-%d"),
        "label": label,
        "ylabel": unit,
        "tooltip": {"style": "count", "unit": unit},
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


