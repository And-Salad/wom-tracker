"""Who won each day, and each month.

Two answers to the same question, and they do not always agree. The written
round-up names a winner on judgement - a boss haul or a real milestone can
outweigh raw experience, and it says so in the prose. The figures name one on
experience gained, which is the measure the standings chart already sorts on.

The round-up's answer is better where it exists, and it exists for one day in
thirty: they are written one a day going forward, so a calendar driven by them
alone would be blank for the two months it is meant to show. So the figures
fill the grid and a round-up overrules them where one has been written.

Days are Eastern midnight to Eastern midnight, the same boundaries the
round-ups are written to. A calendar ruled in UTC days would colour squares
that the round-up for that date disagreed with.
"""

from datetime import datetime, timedelta, timezone

from .scheduler import EASTERN
from .util import parse_api_time

# The round-up judged the whole group. Narrowing to some of them makes its
# answer a different question, so past that the figures speak for themselves.
WHOLE_GROUP = object()


def month_range(when=None, back=0):
    """[start, end) of a month in Eastern time, `back` months before this one."""
    now = (when or datetime.now(timezone.utc)).astimezone(EASTERN)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0, day=1)
    for _ in range(back):
        start = (start - timedelta(days=1)).replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def days_in(start, end):
    """Every Eastern midnight from start up to end, as (day, next) pairs."""
    out = []
    at = start
    while at < end:
        nxt = at + timedelta(days=1)
        out.append((at, nxt))
        at = nxt
    return out


def _overall_readings(database, player_id, since, until):
    """Every total-experience reading in a range, oldest first.

    One query per player rather than one per day: two months is sixty-two
    boundaries and this table is two months wide.
    """
    return database.query(
        "SELECT captured_at, value FROM metrics"
        " WHERE player_id=? AND kind='skill' AND metric='overall'"
        "   AND value IS NOT NULL AND captured_at>=? AND captured_at<?"
        " ORDER BY captured_at", (player_id, since, until))


def _stamp(when):
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _gap(a, b):
    """Seconds between two ISO stamps, however far apart."""
    return abs((parse_api_time(a) - parse_api_time(b)).total_seconds())


def _player_days(readings, boundaries):
    """One player's [gain, short] for each day, by the app's own rule.

    Two things this has to get right, and the obvious version gets both wrong.

    A day with no reading is not a day with no answer. Wise Old Man records a
    snapshot when an account's hiscores move, so no reading means the account
    did not play: it stands where it stood and gains nothing. Treating that as
    unknown put five of six accounts outside almost every square and handed
    the calendar to the only one with daily readings.

    And a day is measured from the nearer of the two readings bracketing it,
    which is what baseline_snapshot does everywhere else in this app. Measured
    from the far side instead, an account first seen at 17:44 after seven
    quiet weeks has all seven folded into that one day - 9.6m experience where
    the round-up for the same date says 525,744. `short` says the day was only
    watched from partway through, which is the same thing coverage_note tells
    a reader on the other pages.
    """
    out = []
    index = 0
    carried = None                       # (stamp, value) of the last reading
    for position in range(len(boundaries) - 1):
        opens, closes = boundaries[position], boundaries[position + 1]
        before = carried
        inside = None
        while index < len(readings) and readings[index]["captured_at"] <= _stamp(closes):
            row = readings[index]
            if inside is None and row["captured_at"] > _stamp(opens):
                inside = (row["captured_at"], row["value"])
            carried = (row["captured_at"], row["value"])
            index += 1
        if carried is None:
            out.append(None)             # never seen by the end of this day
            continue
        baseline = before
        if before is None:
            baseline = inside
        elif inside is not None and _gap(inside[0], _stamp(opens)) <                 _gap(before[0], _stamp(opens)):
            baseline = inside
        if baseline is None:
            out.append(None)
            continue
        # Short only when the day was measured from well into itself. Updates
        # land every six hours, so the nearer reading is often a little after
        # midnight - which is the schedule working, not thin coverage. The
        # tenth-of-the-window slop is the same coverage_note allows.
        short = _gap(baseline[0], _stamp(opens)) > 86400 * 0.1
        out.append((max(0.0, carried[1] - baseline[1]), short))
    return out


def gains_by_day(database, players, start, end):
    """{date: {"gains": {username: experience}, "measured": [], "short": []}}."""
    boundaries = [day for day, _ in days_in(start, end)] + [end]
    # A reading before the window is what its first day is measured from.
    lookback = _stamp(start - timedelta(days=60))
    closes = _stamp(end)

    out = {}
    for position in range(len(boundaries) - 1):
        out[boundaries[position].strftime("%Y-%m-%d")] = {
            "gains": {}, "measured": [], "short": []}

    for player in players:
        readings = _overall_readings(database, player["id"], lookback, closes)
        if not readings:
            continue
        for position, found in enumerate(_player_days(readings, boundaries)):
            if found is None:
                continue
            gained, short = found
            day = out[boundaries[position].strftime("%Y-%m-%d")]
            day["measured"].append(player["username"])
            if short:
                day["short"].append(player["username"])
            if gained:
                day["gains"][player["username"]] = gained
    return out


def _best(scores):
    """The username with the most experience, or None if nobody gained any."""
    if not scores:
        return None
    winner = max(scores.items(), key=lambda pair: (pair[1], pair[0]))
    return winner[0] if winner[1] > 0 else None


def daily_winners(database, players, start, end, whole_group=False):
    """{date: {"winner", "reason", "measured", "of", "written"}} for a range.

    A day is only answered once every included account was being tracked
    through it. Before that the question has no honest answer: an account
    Wise Old Man had not started watching cannot lose a day, so whoever was
    being watched wins it by default - and for most of a history that has
    grown lopsided, that is one account collecting a whole month it was
    merely the only witness to.

    A rule rather than a date, so it holds for any group and lets go of its
    own accord as the history fills in. Days nobody gained anything on are
    blank too: nothing happened, and a colour would say something did.
    """
    days = gains_by_day(database, players, start, end)
    known = {p["username"] for p in players}
    written = _written_winners(database, "day") if whole_group else {}
    of = len(players)
    out = {}
    for day, found in days.items():
        measured = len(found["measured"])
        entry = {"winner": None, "measured": measured, "of": of,
                 "written": False, "reason": None}
        if measured < of:
            entry["reason"] = "{} of {} accounts were being tracked".format(
                measured, of)
            out[day] = entry
            continue
        named = written.get(day)
        entry["winner"] = named if named in known else _best(found["gains"])
        entry["written"] = entry["winner"] is not None and named in known
        if entry["winner"] is None:
            entry["reason"] = "nobody gained anything"
        out[day] = entry
    return out


def month_winner(database, players, start, end, whole_group=False):
    """Who took a month, counting only the days the whole group was tracked.

    Summed over every day instead, a month is won by whoever was watched
    longest rather than whoever did most - the same default the daily rule
    exists to refuse.
    """
    days = gains_by_day(database, players, start, end)
    of = len(players)
    total = {}
    counted = 0
    for found in days.values():
        if len(found["measured"]) < of:
            continue
        counted += 1
        for username, gained in found["gains"].items():
            total[username] = total.get(username, 0.0) + gained
    if not counted:
        return None
    if whole_group:
        named = _written_winners(database, "month").get(start.strftime("%Y-%m-%d"))
        if named in {p["username"] for p in players}:
            return named
    return _best(total)


def _written_winners(database, period):
    """{window_key: username} from the round-ups that named one."""
    return {row["window_key"]: row["winner"]
            for row in database.group_summaries(period=period)
            if row["winner"]}
