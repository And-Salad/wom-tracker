"""Written summaries of a player's period, generated with the Claude API.

The expensive half of this file is deliberately small. Everything Claude sees
is assembled here as a compact digest - a few hundred tokens of numbers we
already have - so a summary costs a fraction of a cent and the prompt stays the
only thing worth tuning.

Nothing is generated unless the data actually moved: each summary records a
hash of its digest, and an unchanged digest is skipped rather than re-billed.
"""

import hashlib
import logging
import os
import re

from . import periods
from .icons import SKILL_ORDER
from .util import fmt_datetime, fmt_hours, fmt_int, parse_api_time, pretty_metric

log = logging.getLogger(__name__)

# Sonnet 5 at low effort. Turning a table of numbers into a few paragraphs sits
# well inside Sonnet's range, and at $2/$10 per million tokens a summary costs
# well under a cent. Change `summary_model` in the config to try another.
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "low"

# The scheduled summaries ride the first update of the local day: the day
# just gone, the week behind it on Mondays, the month behind it on the 1st.
# They used to wait for six in the morning, which was the first update slot
# after midnight when there were only four a day. There are now one hundred
# and forty four, so the first one after the window closes is minutes old
# rather than hours, and nothing is gained by sitting on it.

# Summaries are a few paragraphs. This is a deliberately short output, not a
# guess - a longer cap would only invite a longer answer.
MAX_TOKENS = 1200

# Per million tokens, for the estimate the CLI prints before spending anything.
PRICING = {"claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0),
           "claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0)}

# Offered on the admin page, cheapest last.
SUMMARY_MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5")

# How hard the model works before answering, which moves both the bill and the
# quality. Offered beside the model because it is the same kind of decision:
# it was a setting only reachable by hand-editing config.json on the volume,
# which for a hosted deployment meant it was not reachable at all.
SUMMARY_EFFORTS = ("low", "medium", "high")

DEFAULT_PROMPT = """\
You write short progress notes about Old School RuneScape players for a small
group of friends who track each other's accounts.

You will be given one player's figures for a single period. Write 2-4 short
paragraphs, in plain prose, addressed to the group rather than to the player.

- Lead with whatever is genuinely the most interesting thing in the numbers.
- Name specific skills, bosses and totals. Numbers are the point.
- Note what they clearly focused on, and anything that stopped or started.
- If a milestone was hit in the period, mention it.
- If the period was quiet, say so plainly and briefly. Do not invent activity
  or pad it out.

Do not use headings, bullet points, or emoji. Do not congratulate or cheerlead.
Do not speculate about intentions beyond what the numbers support.
"""


def base_prompt_path(kind="player"):
    return _prompt_file("summary_prompt" if kind == "player" else "group_prompt")


def period_prompt_path(period_key, kind="player"):
    stem = "summary_prompt" if kind == "player" else "group_prompt"
    return _prompt_file("{}_{}".format(stem, period_key))


GROUP_PROMPT = """\
You write a short group round-up for a handful of friends who track each
other's Old School RuneScape accounts.

You will be given every tracked player's figures for one period, side by side.
Write exactly three short paragraphs, in plain prose, addressed to the group.

- Open by naming a winner for the period and saying plainly why they won.
  Choose on the numbers, and say what you judged on - most XP is the obvious
  measure, but a huge boss haul or a real milestone can outweigh it. If it was
  close, say it was close and name the runner-up.
- Then pick out what is actually notable: a standout skill or boss, someone who
  changed what they were doing, anyone who went quiet.
- Close with a comparison or two that puts the numbers in perspective - who is
  pulling ahead, who is gaining on whom, how the group did overall.

Do not use headings, bullet points, or emoji. Do not congratulate or cheerlead,
and do not hand out consolation prizes. If nobody did much, say so.
"""


def prompt_path(config=None, period=None, kind="player"):
    """Which file supplies the prompt for a period.

    One base file covers every period. Drop in a `summary_prompt_week.txt`
    (or _day, _month - and `group_prompt_*` for the round-up) and that period
    uses it instead: a daily note and a monthly retrospective often want
    different instructions, but nobody should have to maintain six files to
    get started.
    """
    stem = "summary_prompt" if kind == "player" else "group_prompt"
    if period is not None:
        key = period if isinstance(period, str) else period.key
        specific = _prompt_file("{}_{}".format(stem, key))
        if os.path.exists(specific):
            return specific
    return _prompt_file(stem)


def _prompt_file(name):
    from .config import data_dir
    return os.path.join(data_dir(), name + ".txt")


def load_prompt(config=None, period=None, kind="player"):
    """The tunable system prompt, created from the default on first use."""
    path = prompt_path(config, period, kind)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(DEFAULT_PROMPT if kind == "player" else GROUP_PROMPT)
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


# -- the digest -----------------------------------------------------------

def build_digest(database, config, player, window):
    """Everything Claude is told about one player's window, as plain text."""
    since, until = window.start_iso(), window.end_iso()
    lines = ["Player: {}".format(player["display_name"]),
             "Account type: {}".format(pretty_metric(player["type"] or "unknown")),
             "Period: {} ({})".format(window.label, _period_noun(window.period))]
    lines += _coverage(database, player, window)

    overall = database.overall_at(player["id"])
    if overall:
        lines.append("Total level now: {}   Total XP now: {}".format(
            fmt_int(overall["level"]), fmt_int(overall["value"])))
    lines.append("Efficient hours: {} played, {} bossed".format(
        fmt_hours(player["ehp"]), fmt_hours(player["ehb"])))

    skills = database.metric_gains(player["id"], since, "skill", until=until)
    total_xp = sum(v for k, v in skills.items() if k != "overall")
    lines.append("")
    lines.append("Experience gained this period: {}".format(fmt_int(total_xp)))
    ranked = [(m, v) for m, v in skills.items() if m != "overall" and v]
    ranked.sort(key=lambda kv: -kv[1])
    if ranked:
        lines.append("XP by skill (largest first):")
        for metric, value in ranked:
            lines.append("  {:<14} {}".format(pretty_metric(metric), fmt_int(value)))
    else:
        lines.append("No experience gained this period.")

    levels = _levels_gained(database, player, since, until)
    if levels:
        lines.append("")
        lines.append("Levels gained: " + ", ".join(
            "{} +{} (now {})".format(pretty_metric(m), gained, now)
            for m, gained, now in levels))

    bosses = database.metric_gains(player["id"], since, "boss", until=until)
    killed = sorted(((m, v) for m, v in bosses.items() if v), key=lambda kv: -kv[1])
    lines.append("")
    if killed:
        lines.append("Boss kills this period:")
        for metric, value in killed:
            lines.append("  {:<28} {}".format(pretty_metric(metric), fmt_int(value)))
    else:
        lines.append("No boss kills this period.")

    activities = database.metric_gains(player["id"], since, "activity", until=until)
    done = sorted(((m, v) for m, v in activities.items() if v), key=lambda kv: -kv[1])
    if done:
        lines.append("")
        lines.append("Clues, collection log and other activities:")
        for metric, value in done:
            lines.append("  {:<28} +{}".format(pretty_metric(metric), fmt_int(value)))

    milestones = [row for row in database.achievements(
        player_ids=[player["id"]], since=since, limit=50)
        if row["achieved_at"] and row["achieved_at"] < until]
    if milestones:
        lines.append("")
        lines.append("Milestones reached this period:")
        for row in milestones:
            lines.append("  {} ({})".format(
                row["name"], fmt_datetime(row["achieved_at"], "%d %b")))

    return "\n".join(lines)


def _week_context(database, players, window, board):
    """What a weekly round-up needs beyond the week's own totals.

    A week is not judged in its own right on either leaderboard - the day and
    the month are - so on the figures alone it would read as a third
    competition nobody is playing. What it is for is review: who took each of
    its days, and where that leaves the month it sits in.
    """
    from . import winners

    lines = ["", "Days of this week, and who took each:"]
    won = winners.daily_winners(database, players, window.start, window.end,
                                whole_group=True, board=board)
    names = {p["username"]: p["display_name"] for p in players}
    tally = {}
    for day in sorted(won):
        found = won[day]
        if found["live"]:
            continue
        if found["winner"]:
            tally[found["winner"]] = tally.get(found["winner"], 0) + 1
            lines.append("  {}: {}".format(day, names.get(found["winner"],
                                                          found["winner"])))
        else:
            lines.append("  {}: nobody ({})".format(
                day, found["reason"] or "no result"))
    if tally:
        lines.append("  Days taken this week: {}".format(", ".join(
            "{} {}".format(names.get(u, u), n)
            for u, n in sorted(tally.items(), key=lambda kv: -kv[1]))))

    start, end = winners.month_range(window.end)
    points = winners.month_points(database, players, start, end, board=board)
    counted = winners.counted_days(database, players, start, end, board)
    lines.append("")
    lines.append("The month so far ({} - {} days counted), running average"
                 " points per day, which is what the month is awarded on:"
                 .format(start.strftime("%B %Y"), counted))
    if points:
        for username, score in sorted(points.items(), key=lambda kv: -kv[1]):
            lines.append("  {}: {:.2f}".format(names.get(username, username),
                                               score))
    else:
        lines.append("  Not enough days counted yet to stand anybody up.")
    return lines


def _ranking_lines(ranked):
    """The order the group's own rule puts them in, for the digest.

    Worked out here rather than left to the model, so the round-up and the
    calendar square beside it cannot name different winners.
    """
    from . import winners

    averaged = ranked and ranked[0]["points"] is not None
    voided = bool(ranked and ranked[0].get("voided"))
    lines = ["Standings by the group's rule - a ninety-nine takes a day, then two",
             "beat one; failing that, experience counted only up to level 99 in",
             "each skill, since past that a skill stops levelling."]
    if voided:
        lines.append("This month is not awarded: only {} of its days were watched"
                     .format(ranked[0].get("days")))
        lines.append("with everyone on file, and a month needs {}. Say so plainly -"
                     .format(winners.MIN_MONTH_DAYS))
        lines.append("the order below is who did the most work, not who won:")
    elif averaged:
        lines.append("Over a period longer than a day the order is the average of")
        lines.append("its days, so one big day does not decide the whole of it:")
    else:
        lines.append("")
    for place, row in enumerate(ranked, start=1):
        lines.append("  {}. {} - {}{} new 99s, {} xp toward 99s,"
                     " {} xp in total{}".format(
            place, row["name"],
            "{:.2f} pts a day, ".format(row["points"]) if averaged else "",
            row["nines"], fmt_int(row["capped"]), fmt_int(row["raw"]),
            "  (only measured from partway into the period)" if row["short"] else ""))
    # Decided the same way the calendar decides it, or a month with no day
    # the whole group was tracked through would still be handed to somebody.
    top = ranked[0] if ranked else None
    if top is None or voided:
        winner = None
    elif averaged:
        winner = top["name"] if top["points"] else None
    else:
        winner = top["name"] if (top["nines"] or top["raw"]) else None
    lines.append("")
    empty = ("nobody - too little of the month was watched for it to count"
             if voided else "nobody - the period was empty")
    lines.append("Winner: {}".format(winner or empty))
    lines.append("")
    return lines


# How each competition is judged, said plainly enough for the round-up to
# judge the same way rather than reaching for the most obvious number.
BOARD_RULES = {
    "maxing": "Maxing - judged on experience gained up to level 99 in each"
              " skill. Reaching a 99 takes the period outright, and experience"
              " past 99 does not count.",
    "grinding": "Grinding - judged on total experience gained, all of it, with"
                " no cap and no special credit for reaching a 99.",
}


def build_group_digest(database, config, players, window, board="maxing"):
    """Every tracked player's figures for one window, side by side.

    Built from the same numbers the individual summaries use rather than from
    their prose: comparisons need the figures, and this way the round-up does
    not depend on the individual write-ups having been generated first.

    The competition's own rule goes at the top, because the two boards are the
    same figures judged differently and a round-up handed only the numbers
    would pick the winner the numbers suggest rather than the one who won.
    """
    from . import winners

    since, until = window.start_iso(), window.end_iso()
    lines = ["Competition: {}".format(BOARD_RULES.get(board, board)),
             "Period: {} ({})".format(window.label, _period_noun(window.period)),
             "Players compared: {}".format(len(players)), ""]
    lines.extend(_ranking_lines(winners.ranking(database, players, window,
                                                board=board)))
    if window.period == "week":
        lines.extend(_week_context(database, players, window, board))

    for player in players:
        skills = database.metric_gains(player["id"], since, "skill", until=until)
        total_xp = sum(v for k, v in skills.items() if k != "overall")
        top = sorted(((m, v) for m, v in skills.items() if m != "overall" and v),
                     key=lambda kv: -kv[1])[:3]
        bosses = database.metric_gains(player["id"], since, "boss", until=until)
        kills = sum(bosses.values())
        best_boss = max(bosses.items(), key=lambda kv: kv[1]) if bosses else None
        activities = database.metric_gains(player["id"], since, "activity", until=until)
        levels = _levels_gained(database, player, since, until)
        milestones = [row["name"] for row in database.achievements(
            player_ids=[player["id"]], since=since, limit=25)
            if row["achieved_at"] and row["achieved_at"] < until]
        overall = database.overall_at(player["id"])

        lines.append("{} ({})".format(
            player["display_name"], pretty_metric(player["type"] or "unknown")))
        lines.append("  XP gained: {}".format(fmt_int(total_xp)))
        lines.append("  Top skills: {}".format(
            ", ".join("{} {}".format(pretty_metric(m), fmt_int(v)) for m, v in top)
            or "none"))
        lines.append("  Boss kills: {}{}".format(
            fmt_int(kills),
            "  (most: {} {})".format(pretty_metric(best_boss[0]), fmt_int(best_boss[1]))
            if best_boss else ""))
        if levels:
            lines.append("  Levels: {}".format(", ".join(
                "{} +{} (now {})".format(pretty_metric(m), g, n)
                for m, g, n in levels)))
        clues = sum(v for m, v in activities.items() if m.startswith("clue_scrolls_")
                    and m != "clue_scrolls_all")
        log_slots = activities.get("collections_logged", 0)
        if clues or log_slots:
            lines.append("  Clues: {}   Collection log: +{}".format(
                fmt_int(clues), fmt_int(log_slots)))
        if milestones:
            lines.append("  Milestones: {}".format("; ".join(milestones[:6])))
        if overall:
            lines.append("  Standing: total level {}, {} total XP".format(
                fmt_int(overall["level"]), fmt_int(overall["value"])))
        # Coverage varies wildly between players, and a ranking that ignores
        # that is a ranking of who happened to be measured.
        for note in _coverage(database, player, window):
            lines.append("  " + note.replace("Data coverage: ", "Coverage: ").strip())
        lines.append("")

    return "\n".join(lines).rstrip()


def _coverage(database, player, window):
    """How well the stored readings actually cover this window.

    Gains are the difference between two snapshots, and Wise Old Man only has
    the snapshots it has. When the one a window is measured from sits days or
    weeks outside it, the figures cover that whole stretch: the experience was
    logged when the reading landed, not necessarily earned in this period.
    Telling Claude that is the difference between a fair note and an invented
    burst of activity.
    """
    since, until = window.start_iso(), window.end_iso()
    start, end = database.snapshot_bounds(player["id"], since, until)
    counted = database.query_one(
        "SELECT COUNT(*) AS n FROM snapshots WHERE player_id=?"
        " AND captured_at>=? AND captured_at<?", (player["id"], since, until))
    inside = counted["n"] if counted else 0

    if start is None or end is None or start["id"] == end["id"]:
        lines = ["Data coverage: no pair of readings covers this period. Every"
                 " figure below is zero because nothing was measured, not"
                 " because nothing was done."]
        anchor = _nearest_reading(database, player, window)
        if anchor:
            lines.append("  " + anchor)
        return lines

    opened = parse_api_time(since)
    measured = parse_api_time(start["captured_at"])
    lines = ["Data coverage: {} reading{} inside the period, measured from {}"
             " to {}.".format(inside, "" if inside == 1 else "s",
                              fmt_datetime(start["captured_at"], "%d %b %H:%M"),
                              fmt_datetime(end["captured_at"], "%d %b %H:%M"))]
    behind = (opened - measured).total_seconds()
    if behind > 6 * 3600:
        lines.append(
            "  That baseline is {} before the period even opened, so the"
            " figures below span that gap as well. The account was not seen"
            " during it: treat the totals as work spread across the whole"
            " stretch, logged when the reading finally landed, rather than as"
            " a burst inside this period.".format(_duration(behind)))
    elif -behind > 6 * 3600:
        lines.append(
            "  There is no reading from the start of the period - the earliest"
            " is {} into it, so anything done before that is missing from the"
            " figures below.".format(_duration(-behind)))
    return lines


def _nearest_reading(database, player, window):
    """Where this account stood at the reading closest to an unmeasured window.

    A window that predates tracking has nothing to report, and saying only
    "nothing was measured" leaves the reader with no idea who this person is.
    The nearest reading either side is not a measurement of the period, and the
    prompts are told to present it as the landmark it is rather than as a
    figure for the window.
    """
    opened, closed = window.start_iso(), window.end_iso()
    before = database.query_one(
        "SELECT id, captured_at FROM snapshots WHERE player_id=? AND captured_at<?"
        " ORDER BY captured_at DESC LIMIT 1", (player["id"], opened))
    after = database.query_one(
        "SELECT id, captured_at FROM snapshots WHERE player_id=? AND captured_at>=?"
        " ORDER BY captured_at ASC LIMIT 1", (player["id"], closed))
    if before is None and after is None:
        return "  Nothing is stored for this account from any date."
    if before is None:
        chosen, side = after, "after"
    elif after is None:
        chosen, side = before, "before"
    else:
        edge = parse_api_time(opened)
        chosen, side = (
            (before, "before")
            if abs((edge - parse_api_time(before["captured_at"])).total_seconds())
            <= abs((parse_api_time(after["captured_at"]) - edge).total_seconds())
            else (after, "after"))
    # Keyed by when the reading was taken, not by a snapshot id: the metrics
    # table stopped carrying one when it went to storing only what changed, so
    # this query had been raising on every window an account was not tracked
    # through - which is precisely the case this whole function exists for.
    overall = database.overall_at(player["id"], chosen["captured_at"])
    if overall is None:
        return None
    return ("Nearest reading {} this period: {} - total level {}, {} XP."
            " That is a landmark, not a figure for the period.".format(
                side, fmt_datetime(chosen["captured_at"], "%d %b %Y"),
                fmt_int(overall["level"]), fmt_int(overall["value"])))


def _duration(seconds):
    days = int(seconds // 86400)
    if days >= 1:
        return "{} day{}".format(days, "" if days == 1 else "s")
    hours = max(1, int(seconds // 3600))
    return "{} hour{}".format(hours, "" if hours == 1 else "s")


def _period_noun(period):
    return {"day": "one day", "week": "one week",
            "month": "one month"}.get(period, period)


def _levels_gained(database, player, since, until=None):
    """(metric, levels gained, level at the end) for skills that levelled up."""
    out = []
    for skill in SKILL_ORDER + ("overall",):
        rows = database.metric_history(player["id"], skill, "skill", since=since)
        if until:
            rows = [r for r in rows if r["captured_at"] < until]
        levels = [r["level"] for r in rows if r["level"] is not None]
        if len(levels) >= 2 and levels[-1] > levels[0]:
            out.append((skill, int(levels[-1] - levels[0]), int(levels[-1])))
    return out


def digest_hash(digest):
    return hashlib.sha256(digest.encode("utf-8")).hexdigest()[:16]


# -- the call -------------------------------------------------------------

class SummaryError(Exception):
    """Generating a summary failed; the caller decides whether that matters."""


def _client(config):
    import anthropic
    key = (config.get("anthropic_api_key") or "").strip()
    if key:
        return anthropic.Anthropic(api_key=key)
    # No key in the config: fall back to the environment or a logged-in
    # profile, which is how the SDK resolves credentials by default.
    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise SummaryError(
            "no Anthropic API key - add one under Options, or set ANTHROPIC_API_KEY")
    return anthropic.Anthropic()


def estimate(config, system, digest):
    """Token count and cost for a request, without sending it."""
    client = _client(config)
    model = config.get("summary_model") or DEFAULT_MODEL
    counted = client.messages.count_tokens(
        model=model, system=system,
        messages=[{"role": "user", "content": digest}])
    input_rate, output_rate = PRICING.get(model, PRICING[DEFAULT_MODEL])
    # Assume the model uses most of its allowance; the real figure is lower.
    cost = (counted.input_tokens * input_rate + MAX_TOKENS * output_rate) / 1e6
    return counted.input_tokens, cost


def generate(config, system, digest):
    """Ask Claude for one summary. Returns (text, usage dict)."""
    import anthropic
    client = _client(config)
    model = config.get("summary_model") or DEFAULT_MODEL
    effort = config.get("summary_effort") or DEFAULT_EFFORT
    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            output_config={"effort": effort},
            system=system,
            messages=[{"role": "user", "content": digest}],
        )
    except anthropic.AuthenticationError as exc:
        raise SummaryError("the Anthropic API key was rejected") from exc
    except anthropic.PermissionDeniedError as exc:
        raise SummaryError("that API key is not allowed to use this model") from exc
    except anthropic.NotFoundError as exc:
        raise SummaryError("unknown model: {}".format(model)) from exc
    except anthropic.RateLimitError as exc:
        raise SummaryError(
            "rate limited by the Anthropic API; try again shortly") from exc
    except anthropic.APIStatusError as exc:
        raise SummaryError("Anthropic API error {}: {}".format(
            exc.status_code, exc.message)) from exc
    except anthropic.APIConnectionError as exc:
        raise SummaryError("could not reach the Anthropic API") from exc

    if response.stop_reason == "refusal":
        raise SummaryError("the model declined to answer")
    text = "\n".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise SummaryError("the model returned nothing")
    usage = {"input_tokens": response.usage.input_tokens,
             "output_tokens": response.usage.output_tokens, "model": model}
    return text.strip(), usage


# -- orchestration --------------------------------------------------------

def summarise_player(database, config, player, window, force=False):
    """Generate and store one summary for one window. Returns (text, note)."""
    digest = build_digest(database, config, player, window)
    fingerprint = digest_hash(digest)
    existing = database.summary(player["id"], window.period, window.key)
    if existing and existing["digest_hash"] == fingerprint and not force:
        return existing["text"], "unchanged"

    system = load_prompt(config, window.period)
    text, usage = generate(config, system, digest)
    database.save_summary(player["id"], window, text, fingerprint, usage)
    return text, "generated ({} in, {} out)".format(
        usage["input_tokens"], usage["output_tokens"])


def due_periods(database, now=None):
    """Which summaries today owes, on the calendar the schedule runs on.

    The first update after midnight covers the day just gone. Mondays add the
    week behind them, and the first of the month adds the month. Nothing is
    time-gated beyond that: a window is owed from the moment it closes until
    something writes it, which is also what catches up a machine that was off
    on the day it should have run.

    Everything is judged in the configured time zone so it lines up with the
    update schedule rather than drifting against the viewer's own clock.
    """
    from datetime import datetime, timezone

    from .scheduler import zone
    now = (now or datetime.now(timezone.utc)).astimezone(zone())

    # A period is owed when its newest complete window has not been written
    # yet. That is what catches up a machine asleep on the day itself: the
    # Monday window stays unwritten until something writes it.
    owed = []
    for period in periods.SUMMARY_PERIODS:
        window = periods.latest_window(period, now)
        if _missing(database, period, window.key):
            owed.append(period)
    return owed


def _missing(database, period, window_key):
    """True when this window still needs writing - player notes or the group one."""
    players = database.query_one(
        "SELECT COUNT(*) AS n FROM summaries WHERE period=? AND window_key=?",
        (period, window_key))
    if not (players and players["n"]):
        return True
    # Only the periods the group recap covers can be owed one. Asked about a
    # week, this used to answer "missing" forever - there is no weekly group
    # recap to find - so every run re-wrote every player's weekly note.
    if period not in periods.GROUP_PERIODS:
        return False
    # Every board owes its own round-up for the window. Asking about one of
    # them would call the window done while the other had nothing written.
    from . import winners
    return any(database.group_summary(period, window_key, board) is None
               for board in winners.BOARDS)


def maybe_write_summaries(database, config, now=None):
    """Write whatever summaries the calendar owes, after an update pass.

    Updates run every ten minutes; summaries do not. Returns how many were
    actually written - zero when the feature is off, when today's are already
    done, or when nothing a summary describes has changed.
    """
    if not config.get("summaries_enabled"):
        return 0

    keys = due_periods(database, now)
    if not keys:
        return 0
    players = database.players()
    if not players:
        return 0

    results = summarise_all(database, config, players, keys)
    written = sum(1 for r in results
                  if not r.get("failed") and r["note"].startswith("generated"))
    log.info("summaries: wrote %d across %s", written, ", ".join(keys))
    return written


def summarise_group(database, config, players, window, force=False,
                    board="maxing"):
    """Generate and store one board's group round-up for one window."""
    digest = build_group_digest(database, config, players, window, board)
    fingerprint = digest_hash(digest)
    existing = database.group_summary(window.period, window.key, board)
    if existing and existing["digest_hash"] == fingerprint and not force:
        return existing["text"], "unchanged"

    system = load_prompt(config, window.period, kind="group")
    text, usage = generate(config, system, digest)
    winner, text = split_winner(text, players)
    database.save_group_summary(window, text, fingerprint, usage, winner=winner,
                                board=board)
    return text, "generated ({} in, {} out)".format(
        usage["input_tokens"], usage["output_tokens"])


WINNER_LINE = re.compile(r"^\s*WINNER:\s*(.+?)\s*$", re.IGNORECASE)


def split_winner(text, players):
    """Pull the named winner off the front of a round-up, if it named one.

    The prompt asks for one line before the prose so the calendar on the
    Round-ups page has something to colour a day by. It is taken off the text
    rather than left in it: the line is for the machine, and a reader should
    see the paragraphs the round-up has always been.

    A name that matches no tracked account is dropped rather than stored. The
    model is asked for an exact display name and usually gives one, but a
    calendar keyed on a name nothing can look up would just be blank squares
    that look like a bug.
    """
    lines = (text or "").lstrip().splitlines()
    if not lines:
        return None, text
    match = WINNER_LINE.match(lines[0])
    if match is None:
        return None, text
    rest = "\n".join(lines[1:]).lstrip("\n")
    named = match.group(1).strip().strip(".").lower()
    for player in players:
        if named in (player["username"], (player["display_name"] or "").lower()):
            return player["username"], rest
    if named in ("none", "nobody", "no one", "-"):
        return None, rest
    log.warning("round-up named a winner nothing matches: %r", match.group(1))
    return None, rest


def summarise_all(database, config, players, period_keys=None, force=False,
                  progress=None, now=None):
    """Write the newest complete window of each period. Returns result dicts."""
    keys = period_keys or ["day"]
    results = []
    for player in players:
        for key in keys:
            window = periods.latest_window(key, now)
            entry = {"player": player["display_name"], "period": window.label}
            try:
                _text, note = summarise_player(database, config, player, window, force)
                entry["note"] = note
            except SummaryError as exc:
                entry["note"] = "failed: {}".format(exc)
                entry["failed"] = True
                log.warning("summary failed for %s/%s: %s",
                            player["display_name"], key, exc)
            except Exception as exc:
                entry["note"] = "failed: {}".format(exc)
                entry["failed"] = True
                log.exception("summary crashed for %s/%s", player["display_name"], key)
            results.append(entry)
            if progress:
                progress(entry)

    # One recap per window per board, after the individual notes, and only
    # for the windows a leaderboard has something to say about - a quarter or
    # a year has no result to put beside it on either.
    #
    # It always compares the whole roster, never the caller's subset: there is
    # a single stored recap per window, so writing one from a partial
    # selection would file a two-player comparison as the verdict for everyone.
    from . import winners

    roster = database.players()
    for key in [k for k in keys if k in periods.GROUP_PERIODS]:
        window = periods.latest_window(key, now)
        for board in winners.BOARDS:
            label = winners.BOARD_LABELS[board]
            entry = {"player": label, "period": window.label}
            try:
                _text, note = summarise_group(database, config, roster, window,
                                              force, board=board)
                entry["note"] = note
            except SummaryError as exc:
                entry["note"] = "failed: {}".format(exc)
                entry["failed"] = True
                log.warning("%s round-up failed for %s: %s", label, key, exc)
            except Exception as exc:
                entry["note"] = "failed: {}".format(exc)
                entry["failed"] = True
                log.exception("%s round-up crashed for %s", label, key)
            results.append(entry)
            if progress:
                progress(entry)
    return results
