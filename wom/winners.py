"""Who won each day, and each month.

One answer, and it is arithmetic. A day goes to whoever the rule says it goes
to, measured from the readings, and nothing else may overrule that.

A written round-up used to. It names a winner too - on judgement, where a boss
haul or a real milestone can outweigh raw experience - and where one had been
written the square took its word. That put a language model on the deciding
path of a scoreboard: the same day could be awarded differently depending on
whether a recap had been written for it yet, and on what the model made of the
figures that morning. A competition has to be able to say why somebody won,
and "the recap said so" is not a rule anybody can play to.

So the round-ups comment and the figures decide. A recap still names its own
winner in its prose, and where that differs from the square the difference is
the interesting part - which is the reason the chips on the Recaps tab quote
the calendar rather than themselves.

Days run midnight to midnight in the configured time zone - the setting the
admin page writes, read here through scheduler.zone() - which is the same
boundary the round-ups are written to. A calendar ruled in UTC days would
colour squares that the round-up for that date disagreed with. Everything
below that says "local" means that zone, not the server's.
"""

from datetime import datetime, timedelta, timezone

from .periods import coverage_slack
from .scheduler import zone
from .util import api_stamp as _stamp
from .util import parse_api_time


def month_range(when=None, back=0):
    """[start, end) of a month in local time, `back` months before this one."""
    now = (when or datetime.now(timezone.utc)).astimezone(zone())
    start = now.replace(hour=0, minute=0, second=0, microsecond=0, day=1)
    for _ in range(back):
        start = (start - timedelta(days=1)).replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def today_key(when=None):
    """Which local day is currently in progress."""
    now = (when or datetime.now(timezone.utc)).astimezone(zone())
    return now.strftime("%Y-%m-%d")


def today_range(when=None):
    """[midnight, next midnight) of the local day in progress.

    The whole day, not the part of it that has happened. What is being asked
    is "how is today going", and a chart that stops at the current minute
    redraws its own axis every ten minutes - the day is the frame, and the
    lines simply have not reached the right-hand edge yet.
    """
    now = (when or datetime.now(timezone.utc)).astimezone(zone())
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def days_in(start, end):
    """Every local midnight from start up to end, as (day, next) pairs."""
    out = []
    at = start
    while at < end:
        nxt = at + timedelta(days=1)
        out.append((at, nxt))
        at = nxt
    return out


# Experience for level 99. Above it a skill stops levelling, so experience
# past it buys nothing the game recognises.
NINETY_NINE = 13034431

# The two competitions. They run over the same days on the same readings and
# disagree on purpose: Maxing is about getting to ninety-nine, so it counts
# experience only up to it, and Grinding is about how much you did, so it
# counts all of it. An account with everything maxed cannot place on the
# first and can win the second outright, which is the point of having both.
MAXING = "maxing"
GRINDING = "grinding"
BOARDS = (MAXING, GRINDING)
BOARD_LABELS = {MAXING: "Maxing", GRINDING: "Grinding"}

# How much of a month has to have been watched before it is worth awarding.
# A month decided on the two days at the end of it is not a month anybody
# competed over, and the winner it names is really a winner of those days.
# Two weeks is where a month starts reading as a month rather than a sample.
MIN_MONTH_DAYS = 14


class Readings:
    """One walk over the readings, shared by everything that judges the days.

    Every question this module answers bottoms out in `skill_states`, which
    runs three queries per player and carries a running state across every
    reading in the span. Nothing about that walk depends on which board is
    asking - a board changes how a day is scored, not what was read - so the
    same walk was being repeated across a page that only ever wanted it once.

    On the Leaderboards page that was eight times over: two months, each asked
    both for its days (daily_winners) and for its month (month_winner, by way
    of _scored_days), and the whole of that again for the second board. Pass
    one of these through and every answer comes from the first walk.

    Request-lived, and deliberately not a global: it holds what the database
    said at the moment it was asked, and an update pass lands every ten
    minutes in the same process.
    """

    def __init__(self, database, players):
        self.db = database
        self.players = players
        self._states = {}
        self._days = {}
        self._polled = {}

    def states(self, player_id, since, until):
        key = (player_id, since, until)
        if key not in self._states:
            self._states[key] = skill_states(self.db, player_id, since, until)
        return self._states[key]

    def days(self, start, end):
        key = (_stamp(start), _stamp(end))
        if key not in self._days:
            self._days[key] = gains_by_day(self.db, self.players, start, end,
                                           readings=self)
        return self._days[key]

    def polled(self, start, end):
        key = (_stamp(start), _stamp(end))
        if key not in self._polled:
            self._polled[key] = polled_days(self.db, self.players, start, end)
        return self._polled[key]


def _readings(database, players, readings):
    """The cache passed in, or a fresh one for this call alone."""
    return readings if readings is not None else Readings(database, players)


def skill_states(database, player_id, since, until):
    """[(stamp, {skill: experience})], oldest first, one per reading.

    Only changes are stored, but a reading where nothing moved is still a
    reading - it says the account stood where it stood at that moment, which
    is exactly what a day needs to be measured from. So the walk is driven by
    when the account was read, with each change laid over a running copy.

    The window bounds the changes read, and what stood before it is asked for
    directly. Read from the beginning of time instead - which is what the
    lower bound below used to be missing - this pulled an account's entire
    history into memory to answer a question about one day, and a year of
    ten-minute readings is fifty thousand rows of it per player per call.
    """
    # The newest value each skill held strictly before the window. Written out
    # rather than taken from state_at because that answers with the newest row
    # per metric whatever it holds, and a skill that has fallen off the
    # hiscores has a NULL one - which would drop a skill the walk below,
    # filtering the same way, still carries forward from its last real value.
    running = {row["metric"]: row["value"] for row in database.query(
        "SELECT metric, value FROM metrics m"
        " WHERE player_id=? AND kind='skill' AND value IS NOT NULL"
        "   AND captured_at<? AND captured_at=("
        "     SELECT MAX(captured_at) FROM metrics x"
        "      WHERE x.player_id=m.player_id AND x.kind='skill'"
        "        AND x.metric=m.metric AND x.value IS NOT NULL"
        "        AND x.captured_at<?)",
        (player_id, since, since))}
    changes = database.query(
        "SELECT captured_at, metric, value FROM metrics"
        " WHERE player_id=? AND kind='skill' AND value IS NOT NULL"
        "   AND captured_at>=? AND captured_at<? ORDER BY captured_at",
        (player_id, since, until))
    stamps = database.observations(player_id, since, until)
    if not stamps:
        return []

    states = []
    at = 0
    for stamp in stamps:
        moved = False
        while at < len(changes) and changes[at]["captured_at"] <= stamp:
            if not moved:
                running = dict(running)
                moved = True
            running[changes[at]["metric"]] = changes[at]["value"]
            at += 1
        states.append((stamp, running))
    return states


def measure(before, after):
    """One account's showing over a span, from its skills at either end.

    `nines` is the thing the game marks; `capped` is experience counted only
    up to ninety-nine in each skill, because past that a skill stops
    levelling; `raw` is all of it.
    """
    nines = 0
    capped = 0.0
    raw = 0.0
    for metric, end in after.items():
        if metric == "overall":
            continue
        # A skill with no reading behind it was below the hiscore cutoff, so
        # it counts from zero rather than being dropped - the same rule
        # db.metric_gains follows. Skipped, the day a new skill first ranks
        # counts for nothing here while the Overview chart credits every
        # point of it, and the two pages disagree about the same afternoon.
        start = before.get(metric) or 0.0
        if end <= start:
            continue
        raw += end - start
        if start < NINETY_NINE <= end:
            nines += 1
        capped += max(0.0, min(end, NINETY_NINE) - min(start, NINETY_NINE))
    return {"nines": nines, "raw": raw, "capped": capped}


def measure_by_skill(before, after):
    """The same measure, kept per skill instead of summed.

    measure() answers "how did they do"; this answers "at what", which is the
    question a reader asks the moment they see the total. Same arithmetic, so
    the parts always add up to the whole - anything that rounded or filtered
    differently here would produce a breakdown that argues with the figure it
    is breaking down.

    Skills that did not move are left out: twenty-three rows of zero is not a
    breakdown, and the caller knows the full list if it wants to say so.
    """
    out = {}
    for metric, end in after.items():
        if metric == "overall":
            continue
        start = before.get(metric) or 0.0     # unranked counts from zero, as above
        if end <= start:
            continue
        capped = max(0.0, min(end, NINETY_NINE) - min(start, NINETY_NINE))
        out[metric] = {
            "capped": capped,
            "raw": end - start,
            # Experience past 99 in a skill already at it. Counted nowhere in
            # the ranking, which is exactly why it is worth showing: it is the
            # difference between a quiet day and a day that scored nothing.
            "beyond": (end - start) - capped,
            "reached_99": start < NINETY_NINE <= end,
            "at_99": end >= NINETY_NINE,
            "before": start,
            "after": end,
        }
    return out


def key(shown, board=MAXING):
    """How a span is judged, as something sortable.

    On Maxing a ninety-nine takes it outright and two take it over one.
    Failing that it is experience up to ninety-nine: an account with
    everything maxed would otherwise win every day it logged in against people
    still climbing. Where somebody did reach one, the accounts level on nines
    are separated by raw experience instead - they have all been credited for
    the milestone, so the question is who did the most work around it.

    On Grinding it is experience, all of it, and nothing else. No cap and no
    credit for the milestone, because the question is only how much was done.
    """
    if board == GRINDING:
        return (0, shown["raw"])
    return (shown["nines"], shown["raw"] if shown["nines"] else shown["capped"])


def moved(shown, board=MAXING):
    """Whether this counts as having done anything the rule recognises.

    Deliberately the same test the ranking uses, not "gained any experience
    at all". An account that spent the day past 99 in everything has a big
    raw number and a score of nothing - and if that is all that happened, the
    day has no winner. Judged on raw experience here and on capped experience
    there, the calendar crowned somebody the round-up beside it called an
    empty day.
    """
    nines, tiebreak = key(shown, board)
    return bool(nines or tiebreak)


def opening_reading(before, inside, boundary):
    """Which of the two readings bracketing a boundary opens the span.

    `before` is the last reading at or before it, `inside` the first after;
    either may be None. Both are (stamp, state) pairs, and the nearer of the
    two wins - measured from the far side, an account first seen at 17:44
    after seven quiet weeks had all seven folded into that one day.

    Its own function because three things ask it about the same day - the
    standings row, the skills behind that row, and the line under it - and
    two of them used to answer with "the last reading before midnight"
    instead. That is not a rounding difference: a reading landing seconds
    after midnight carries the last ten minutes of the previous evening, so
    the row said 40,991 while the breakdown that was supposed to explain it
    said 399,457.
    """
    if before is None:
        return inside
    if inside is None:
        return before
    if _gap(inside[0], boundary) < _gap(before[0], boundary):
        return inside
    return before


def reading_at_or_before(database, player_id, boundary):
    """(stamp, {skill: experience}) as it stood at the last reading by then.

    Only changes are stored, so the state is the newest row per skill at or
    before the boundary, and the stamp is the reading those rows belong to.
    """
    rows = database.query(
        "SELECT captured_at, metric, value FROM metrics m"
        " WHERE player_id=? AND kind='skill' AND value IS NOT NULL"
        "   AND captured_at<=?"
        "   AND captured_at=(SELECT MAX(captured_at) FROM metrics x"
        "      WHERE x.player_id=m.player_id AND x.kind='skill'"
        "        AND x.metric=m.metric AND x.captured_at<=?)",
        (player_id, boundary, boundary))
    if not rows:
        return None
    state = {row["metric"]: row["value"] for row in rows}
    seen = database.query_one(
        "SELECT MAX(captured_at) AS at FROM snapshots"
        " WHERE player_id=? AND captured_at<=?", (player_id, boundary))
    return ((seen["at"] if seen and seen["at"]
             else max(r["captured_at"] for r in rows)),
            state)


def day_span(database, player_id, opens, closes):
    """The two skill readings one day is measured between, by the group's rule.

    Returns ((stamp, state), (stamp, state)) or (None, None). Every figure
    about a day in progress comes through here, so the row, its breakdown and
    the chart cannot disagree about where the day started.
    """
    states = skill_states(database, player_id, _stamp(opens), _stamp(closes))
    if not states:
        return None, None
    boundary = _stamp(opens)
    inside = next((state for state in states if state[0] > boundary), None)
    baseline = opening_reading(reading_at_or_before(database, player_id, boundary),
                               inside, boundary)
    if baseline is None:
        return None, None
    return baseline, states[-1]


def _player_days(states, boundaries):
    """One account's (score, gained, short) for each day, or None if unseen.

    Two things this has to get right. A day with no reading is not a day with
    no answer: Wise Old Man records a snapshot when an account's hiscores
    move, so no reading means it did not play - it stands where it stood and
    gains nothing. And a day is measured from the nearer of the two readings
    bracketing it, the rule baseline_snapshot follows everywhere else.
    Measured from the far side, an account first seen at 17:44 after seven
    quiet weeks had all seven folded into that one day.
    """
    # Each boundary formatted once, not once per reading. These are the same
    # thirty-odd stamps however many readings sit between them, and being
    # asked for inside the walk below they cost one strftime per reading per
    # day: on a year of ten-minute readings for six accounts that was 648,000
    # calls and most of the three seconds the Round-ups page took to render.
    marks = [_stamp(boundary) for boundary in boundaries]

    out = []
    index = 0
    carried = None                       # (stamp, {skill: experience})
    # Everything before the first boundary settles first. Left in the loop it
    # was consumed during the first day's own iteration, so that day had no
    # reading behind it and was measured from its first reading instead of
    # from the day before's close - wrong for the 1st of every month.
    while index < len(states) and states[index][0] <= marks[0]:
        carried = states[index]
        index += 1
    for position in range(len(marks) - 1):
        opens, closes = marks[position], marks[position + 1]
        before = carried
        inside = None
        while index < len(states) and states[index][0] <= closes:
            if inside is None and states[index][0] > opens:
                inside = states[index]
            carried = states[index]
            index += 1
        if carried is None:
            out.append(None)             # never seen by the end of this day
            continue
        baseline = opening_reading(before, inside, opens)
        if baseline is None:
            out.append(None)
            continue
        # Short only when the day was measured from well into itself. A
        # reading a little after midnight is the schedule working, not thin
        # coverage; see periods.coverage_slack for where the line sits.
        short = _gap(baseline[0], opens) > coverage_slack(86400)
        out.append((measure(baseline[1], carried[1]), short))
    return out


def _gap(a, b):
    """Seconds between two ISO stamps, however far apart."""
    return abs((parse_api_time(a) - parse_api_time(b)).total_seconds())


def polled_days(database, players, start, end):
    """The local days on which the tracker actually looked at everyone.

    Without this a day only has to have every account *on file* to count,
    which is true of every day since each account's first reading - and a day
    nobody was polled then reads as a day nobody played. It is not: Wise Old
    Man records a snapshot when the hiscores move, so silence means "no
    change" only if somebody asked. Where nobody asked, silence means nothing
    at all, and the one account that submits its own readings takes the day
    against five accounts that were never looked at.

    A run that came back with a result for every player it set out to update
    is that evidence. Each run records how many that was, because the answer
    is about the day it ran: measured against today's roster instead, adding
    a seventh account would blank every day behind it. Runs are stamped UTC
    and days are local, so they are moved before they are counted.

    (Whether every account *now* included was on file through the day is a
    different test, and gains_by_day's "measured" answers it.)
    """
    rows = database.query(
        "SELECT started_at, ok_count, roster FROM runs"
        " WHERE started_at>=? AND started_at<?",
        (_stamp(start), _stamp(end)))
    local = zone()
    days = set()
    for row in rows:
        # Runs from before the column existed have no roster of their own;
        # today's is the best guess available for them.
        enough = row["roster"] if row["roster"] else len(players)
        if (row["ok_count"] or 0) < enough:
            continue
        when = parse_api_time(row["started_at"])
        if when is not None:
            days.add(when.astimezone(local).strftime("%Y-%m-%d"))
    return days


def gains_by_day(database, players, start, end, readings=None):
    """{date: {"scores": {username: measure}, "measured": [], "short": []}}."""
    walk = _readings(database, players, readings)
    boundaries = [day for day, _ in days_in(start, end)] + [end]
    # A reading before the window is what its first day is measured from.
    lookback = _stamp(start - timedelta(days=60))
    closes = _stamp(end)

    out = {}
    for position in range(len(boundaries) - 1):
        out[boundaries[position].strftime("%Y-%m-%d")] = {
            "scores": {}, "measured": [], "short": []}

    for player in players:
        states = walk.states(player["id"], lookback, closes)
        if not states:
            continue
        for position, found in enumerate(_player_days(states, boundaries)):
            if found is None:
                continue
            shown, short = found
            day = out[boundaries[position].strftime("%Y-%m-%d")]
            day["measured"].append(player["username"])
            if short:
                day["short"].append(player["username"])
            if moved(shown):
                day["scores"][player["username"]] = shown
    return out


def _best(scores, board=MAXING):
    """Whoever scores highest, or None if nobody moved at all."""
    if not scores:
        return None
    winner = max(scores.items(), key=lambda pair: (key(pair[1], board), pair[0]))
    return winner[0] if moved(winner[1], board) else None


def daily_winners(database, players, start, end, when=None,
                  board=MAXING, readings=None):
    """{date: {"winner", "reason", "measured", "of"}} for a range.

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
    walk = _readings(database, players, readings)
    days = walk.days(start, end)
    polled = walk.polled(start, end)
    of = len(players)
    running = today_key(when)
    out = {}
    for day, found in days.items():
        measured = len(found["measured"])
        # The day in progress shows who is ahead but has not been won: it is
        # hours from over, and the last poll of it has not happened.
        live = day == running
        entry = {"winner": None, "measured": measured, "of": of,
                 "reason": None, "live": live}
        if measured < of:
            entry["reason"] = "{} of {} accounts were being tracked".format(
                measured, of)
            out[day] = entry
            continue
        if not live and day not in polled:
            entry["reason"] = "the tracker was not watching that day"
            out[day] = entry
            continue
        entry["winner"] = _best(found["scores"], board)
        if entry["winner"] is None:
            entry["reason"] = "nobody gained anything"
        out[day] = entry
    return out


def placings(found, of, board=MAXING):
    """{username: points} for one day, by where each account placed.

    A win is worth as much as the field it was won against, so taking a day
    five accounts played counts for more than taking one two did. Accounts
    that gained nothing score nothing.
    """
    ranked = sorted(found["scores"].items(),
                    key=lambda pair: (key(pair[1], board), pair[0]), reverse=True)
    return {username: of - place for place, (username, _) in enumerate(ranked)}


def month_points(database, players, start, end, minimum=0, board=MAXING,
                 readings=None):
    """{username: average daily points} over the days the whole group was on.

    A month is the average of its days rather than one measurement across the
    whole of it. Measured end to end, a single ninety-nine on the 3rd takes
    the month whatever anybody did on the other thirty; averaged, it is worth
    one good day, which is what it was.

    `minimum` is how many of those days there have to be before the answer
    means anything; below it there is no answer, not a provisional one.
    """
    points, counted = _scored_days(database, players, start, end, board,
                                   readings)
    if counted < max(1, minimum):
        return {}
    return {username: total / counted for username, total in points.items()}


def counted_days(database, players, start, end, board=MAXING, readings=None):
    """How many days of a span the whole group was watched through."""
    return _scored_days(database, players, start, end, board, readings)[1]


def _scored_days(database, players, start, end, board=MAXING, readings=None):
    """({username: total points}, how many days those came from)."""
    walk = _readings(database, players, readings)
    days = walk.days(start, end)
    polled = walk.polled(start, end)
    of = len(players)
    running = today_key()
    points = {p["username"]: 0.0 for p in players}
    counted = 0
    for day, found in days.items():
        # The same tests the squares pass, and never the day in progress: a
        # month should not be decided partly on an afternoon.
        if day == running or len(found["measured"]) < of or day not in polled:
            continue
        counted += 1
        for username, scored in placings(found, of, board).items():
            points[username] += scored
    return points, counted


def month_winner(database, players, start, end, board=MAXING,
                 readings=None):
    """Who took a month, on the average of the days that counted.

    Nobody, where too few of them counted: see MIN_MONTH_DAYS.
    """
    points = month_points(database, players, start, end,
                          minimum=MIN_MONTH_DAYS, board=board,
                          readings=readings)
    if not points:
        return None
    best = max(points.items(), key=lambda pair: (pair[1], pair[0]))
    return best[0] if best[1] > 0 else None


def ranking(database, players, window, board=MAXING, readings=None):
    """Every account over one window, best first by the rule.

    A day is judged directly. Anything longer is judged on the average of its
    days, which is how the calendar heads a month - so a monthly round-up and
    the month above it cannot name different winners.
    """
    walk = _readings(database, players, readings)
    totals = []
    for player in players:
        states = walk.states(player["id"],
                             _stamp(window.start - timedelta(days=60)),
                             _stamp(window.end))
        found = _player_days(states, [window.start, window.end])[0] if states else None
        shown = found[0] if found else {"nines": 0, "raw": 0.0, "capped": 0.0}
        totals.append({"username": player["username"],
                       "name": player["display_name"],
                       "short": bool(found and found[1]), "points": None, **shown})

    if window.period != "day":
        # Only a month has a floor. A week has seven days in it, and asking a
        # fortnight of a fortnight would void every one of them.
        minimum = MIN_MONTH_DAYS if window.period == "month" else 0
        points = month_points(database, players, window.start, window.end,
                              minimum=minimum, board=board, readings=walk)
        voided = bool(minimum and not points)
        # Only worth asking when the answer is going to be printed: it walks
        # every player's readings again.
        days = counted_days(database, players, window.start, window.end,
                            board, readings=walk) if voided else None
        for row in totals:
            row["points"] = points.get(row["username"], 0.0)
            row["voided"] = voided
            row["days"] = days
        totals.sort(key=lambda row: (row["points"], key(row, board),
                                     row["username"]), reverse=True)
        return totals

    totals.sort(key=lambda row: (key(row, board), row["username"]), reverse=True)
    return totals
