"""Rows into view models.

These were inline in the route handlers, which made a route the only place the
shape of a page was decided and the only place it could be checked. They are
plain functions of (database, ...) so a test can call them without a request.
"""

import json
from datetime import datetime, timedelta, timezone

from .. import gameplay, periods, theme
from ..util import (fmt_ago, fmt_datetime, fmt_hours, fmt_int,
                    parse_api_time, pretty_metric)

# A player's own notes cover every window, named the same way everywhere.
SUMMARY_FOLDERS = (("day", "Daily"), ("week", "Weekly"), ("month", "Monthly"),
                   ("quarter", "Quarterly"), ("year", "Yearly"))

# The group recap covers the two the Maxing Leaderboard judges, in the order
# the page reads them: the day just gone, then the month behind it.
GROUP_FOLDERS = tuple((period, title) for period, title in SUMMARY_FOLDERS
                      if period in periods.GROUP_PERIODS)

METRIC_GROUPS = (("skill", "Skills"), ("boss", "Bosses"),
                 ("activity", "Activities"))


def paragraphs(text):
    """Split a summary into paragraphs for the template to wrap in <p>."""
    return [block.strip() for block in (text or "").split("\n\n") if block.strip()]


def group_verdicts(database, rows):
    """{(period, window_key): what the Maxing Leaderboard said}, for every row.

    The group recap is the leaderboard's feed, so every entry carries what the
    calendar decided for the window it covers. That is not always what the
    prose decided - the recap judges on its own reading and the squares judge
    on the rule - and where they differ the difference is the interesting
    part rather than something to paper over.

    Judged across every tracked account, never the ticked ones, because that
    is how the calendar judges it. Given a subset this answered a different
    question from the page it is quoting, so ticking two names off changed
    who a chip said had won a day back in August.

    Every day in range is settled in one pass. Asked window by window this
    walked a month of readings per row, which across a year of daily recaps
    is the same work three hundred times over.
    """
    from .. import winners

    players = database.players()
    days = [row["window_key"] for row in rows if row["period"] == "day"]
    months = sorted({row["window_key"] for row in rows if row["period"] == "month"})
    local = winners.zone()

    found = {}
    if days:
        opens = datetime.strptime(min(days), "%Y-%m-%d").replace(tzinfo=local)
        closes = (datetime.strptime(max(days), "%Y-%m-%d").replace(tzinfo=local)
                  + timedelta(days=1))
        for key, won in winners.daily_winners(database, players, opens, closes,
                                              whole_group=True).items():
            found[("day", key)] = won["winner"]
    for key in months:
        start = datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=local)
        end = (start + timedelta(days=32)).replace(day=1)
        found[("month", key)] = winners.month_winner(database, players, start,
                                                     end, whole_group=True)

    out = {}
    for row in rows:
        at = (row["period"], row["window_key"])
        out[at] = {
            "username": found.get(at),
            # A month with less than a fortnight of counted days is not
            # awarded at all, and says so rather than leaving a blank where a
            # name goes.
            "unawarded": row["period"] == "month" and not found.get(at),
        }
    return out


def _recap(row, verdict, palette, by_name):
    """One stored group recap, with the leaderboard's verdict beside it."""
    username = verdict["username"]
    return {
        "key": row["window_key"], "period": row["period"],
        "label": row["label"], "ago": fmt_ago(row["generated_at"]),
        "paragraphs": paragraphs(row["text"]),
        "winner": by_name.get(username, username) if username else None,
        "color": palette.get(username, theme.MUTED) if username else None,
        "unawarded": verdict["unawarded"],
        # Only the group's entries are the leaderboard's feed. A player's own
        # note is not a verdict about anything, and labelling it "no verdict"
        # would invent a question it was never answering.
        "judged": True,
    }


def recap_feed(database, players, palette):
    """The newest day and the newest month, for the top of the page.

    Two, not five. The recap is the Maxing Leaderboard's feed, and the
    leaderboard colours days and awards months; a weekly or yearly one was
    describing a window with no result to put beside it.
    """
    pairs = []
    for period, title in GROUP_FOLDERS:
        found = database.group_summaries(period=period, limit=1)
        if found:
            pairs.append((title, found[0]))
    if not pairs:
        return []
    verdicts = group_verdicts(database, [row for _title, row in pairs])
    # Named from the roster, not the ticks: the account a chip names is
    # whoever the calendar says won, and they need not be one of the ticked
    # ones - looked up in `players` an unticked winner had no name to show.
    by_name = {p["username"]: p["display_name"] for p in database.players()}
    return [dict(_recap(row, verdicts[(row["period"], row["window_key"])],
                        palette, by_name), title=title)
            for title, row in pairs]


def _branch(name, username, color, folders):
    return {"player": name, "username": username, "color": color,
            "total": sum(folder["count"] for folder in folders),
            "folders": folders}


def recap_tree(database, players, palette):
    """Everything written so far: the group first, then each account.

    Two shapes under one tree, because they answer different questions about
    the same days. The group's branch holds the two windows the leaderboard
    judges, each entry carrying its verdict. Each account's branch holds all
    five, with no verdict, because a quarter of one account's progress is not
    something the calendar has an opinion about.
    """
    tree = []

    group_folders = []
    everything = []
    for period, title in GROUP_FOLDERS:
        rows = database.group_summaries(period=period)
        if rows:
            group_folders.append((period, title, rows))
            everything.extend(rows)
    if group_folders:
        verdicts = group_verdicts(database, everything)
        by_name = {p["username"]: p["display_name"] for p in database.players()}
        tree.append(_branch("Group", "__group__", theme.ACCENT, [
            {"period": period, "title": title, "count": len(rows),
             "entries": [_recap(row,
                                verdicts[(row["period"], row["window_key"])],
                                palette, by_name)
                         for row in rows]}
            for period, title, rows in group_folders]))

    for player in players:
        folders = player_recaps(database, player)
        if folders:
            tree.append(_branch(player["display_name"], player["username"],
                                palette.get(player["username"], theme.MUTED),
                                folders))
    return tree


def player_recaps(database, player):
    """One account's own notes, newest first, in a folder per window length.

    All five windows, where the group recap has two: these are about one
    account's progress, which does not stop being worth writing because the
    leaderboard has no verdict for a quarter.
    """
    folders = []
    for period, title in SUMMARY_FOLDERS:
        rows = database.summaries(player_id=player["id"], period=period)
        if rows:
            folders.append({
                "period": period, "title": title, "count": len(rows),
                "entries": [{"key": row["window_key"], "label": row["label"],
                             "ago": fmt_ago(row["generated_at"]),
                             "paragraphs": paragraphs(row["text"]),
                             # Not a leaderboard result, so no verdict beside
                             # it - see _recap.
                             "judged": False, "winner": None,
                             "color": None, "unawarded": False}
                            for row in rows],
            })
    return folders


# What each feed row is, for the filter above the table. The order is the
# order the filter offers them in.
FEED_CATEGORIES = (
    ("milestone", "Milestones"),
    ("collection", "Collection log"),
    ("quest", "Quests"),
    ("diary", "Diaries"),
    ("combat_task", "Combat tasks"),
)


def milestone_feed(database, selected, palette, since=None, until=None,
                   limit=300):
    """The achievements feed, newest first.

    Two sources. Wise Old Man's milestones, which every account has, and what
    a player's own client reported, which only the ones who opted in have. A
    reader should not have to care which is which, so they are merged and
    sorted together - but each row says what it is, so they can be filtered.
    """
    from ..icons import icon_kind_for

    ids = [p["id"] for p in selected]
    feed = []
    for row in database.achievements(player_ids=ids, since=since, until=until,
                                     limit=limit):
        dated = row["achieved_at"] and row["achieved_at"] > "1990"
        accuracy = row["accuracy"]
        vague = accuracy is None or accuracy < 0 or accuracy > 86400000
        feed.append({
            "at": row["achieved_at"] if dated else "",
            "when": (("~" if vague else "")
                     + fmt_datetime(row["achieved_at"], "%d %b %Y")) if dated
                    else "unknown",
            "ago": fmt_ago(row["achieved_at"]) if dated else "",
            "player": row["display_name"],
            "color": palette.get(row["username"], theme.MUTED),
            "name": row["name"],
            "detail": "",
            "category": "milestone",
            "metric": row["metric"],
            "kind": icon_kind_for(row["metric"]) if row["metric"] else None,
        })

    for row in database.feed_events(gameplay.FEED_KINDS, player_ids=ids,
                                    since=since, until=until, limit=limit):
        payload = _payload(row["payload"])
        feed.append({
            "at": row["happened_at"],
            "when": fmt_datetime(row["happened_at"], "%d %b %Y"),
            "ago": fmt_ago(row["happened_at"]),
            "player": row["display_name"] or row["username"],
            "color": palette.get(row["username"], theme.MUTED),
            "name": row["subject"] or "",
            "detail": gameplay.detail(row["kind"], payload),
            "category": row["kind"],
            "metric": None,
            "kind": None,
        })

    # Newest first, and anything Wise Old Man could not date at all last -
    # a milestone with no date is not news, and putting it on top would push
    # what actually happened today off the screen.
    feed.sort(key=lambda row: row["at"] or "", reverse=True)
    return feed[:limit]


def _payload(text):
    try:
        return json.loads(text or "{}")
    except ValueError:
        return {}


# The gallery's panels, in the order they appear. Each is a kind of thing
# worth a picture, and the only kinds whose images the webhook accepts.
GALLERY_PANELS = (("death", "Deaths"), ("pet", "Pets"))

# How many of each are shown. More are kept - see wom/gallery.py - so this can
# grow without anyone having to play again first.
GALLERY_SHOWN = 10


def gallery_panels(database, selected, palette, shown=GALLERY_SHOWN):
    """The pictures, newest first, grouped by what they are.

    A panel with nothing in it is still returned, so the page can say which
    kinds exist and are simply empty rather than silently offering fewer
    toggles than the last time somebody looked.
    """
    ids = [p["id"] for p in selected]
    panels = []
    for key, label in GALLERY_PANELS:
        rows = database.images(kind=key, player_ids=ids, limit=shown)
        panels.append({
            "key": key,
            "label": label,
            "images": [{
                "src": "/gallery/{}.{}".format(row["digest"], row["format"]),
                "player": row["display_name"] or row["username"],
                "color": palette.get(row["username"], theme.MUTED),
                "caption": row["caption"] or "",
                "when": fmt_datetime(row["happened_at"], "%d %b %Y %H:%M"),
                "ago": fmt_ago(row["happened_at"]),
            } for row in rows],
        })
    return panels


def player_rows(database, players, palette):
    """The table on /players: one row of headline figures each."""
    rows = []
    for player in players:
        overall = database.overall_at(player["id"])
        rows.append({
            "name": player["display_name"],
            "username": player["username"],
            "color": palette[player["username"]],
            "type": pretty_metric(player["type"] or "-"),
            "combat": fmt_int(player["combat_level"]),
            "total_level": fmt_int(overall["level"] if overall else None),
            "exp": fmt_int(player["exp"]),
            "ehp": fmt_hours(player["ehp"]),
            "ehb": fmt_hours(player["ehb"]),
            "updated": fmt_ago(database.last_change(player["id"])),
            "snapshots": fmt_int(database.snapshot_count(player["id"])),
        })
    return rows


def player_detail(database, player, span):
    """One player's note, figures, and what moved, grouped by kind."""
    since = span.since
    bounds = database.snapshot_bounds(player["id"], since, span.until)

    groups = []
    for kind, title in METRIC_GROUPS:
        gains = database.metric_gains(player["id"], since, kind, bounds=bounds)
        rows = []
        end = bounds[1]
        readings = (database.snapshot_metrics(end, kind) if end
                    else database.latest_snapshot_metrics(player["id"], kind))
        for row in readings:
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

    # No recap here. This page answers "what are the figures", and an account's
    # written notes answer "how has it been going" - which is the Maxing page's
    # question, where the same account's row opens onto them. Two pages both
    # showing the note invited the reader to expect them to say the same thing.
    return {"player": player["display_name"], "period": span.label,
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
    if (measured - opened).total_seconds() <= periods.coverage_slack(asked):
        return {"short": False}
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
            for row in database.snapshot_metrics(end, kind):
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


def winner_calendar(database, players, palette, when=None,
                    board="maxing"):
    """Two months of squares, each one the colour of who won that day.

    Last month beside this one, which is as much as fits side by side and as
    far back as anyone asks. Each month is headed in the colour of whoever
    took it, so the two answers - the day and the month - read together.

    The round-up's own pick is honoured only when every tracked player is
    included: it judged the whole group, and against three of six it is
    answering a different question from the one on screen.
    """
    from .. import winners
    from .today import is_whole_group

    # The same test the standings beside this make, from the same helper: two
    # copies of it is two chances for the squares and the tally to answer
    # different questions on one card.
    whole_group = is_whole_group(database, players)
    by_name = {p["username"]: p["display_name"] for p in players}

    months = []
    for back in (1, 0):
        start, end = winners.month_range(when, back=back)
        won = winners.daily_winners(database, players, start, end, board=board,
                                    whole_group=whole_group)
        took = winners.month_winner(database, players, start, end, board=board,
                                    whole_group=whole_group)
        months.append({
            "label": start.strftime("%B %Y"),
            "color": palette.get(took, theme.MUTED),
            "winner": by_name.get(took),
            # Monday-first, so the columns line up with the weekly round-ups.
            "lead": start.weekday(),
            "days": [_day_cell(day, won, by_name, palette, len(players))
                     for day, _ in winners.days_in(start, end)],
        })
    # No legend: the sidebar beside this lists every player against the same
    # swatch, and each square names its winner on hover.
    return {"months": months, "whole_group": whole_group, "rule": WINNER_RULE}


WINNER_RULE = (
    "A day goes to whoever reached a 99 in it; two 99s beat one. Where nobody "
    "reached one, it goes on experience counted only up to level 99 in each "
    "skill - past that a skill stops levelling, so an account with everything "
    "maxed does not take a day off people still climbing. Where somebody did "
    "reach a 99, accounts level on 99s are separated by their raw experience "
    "instead.\n\n"
    "A day is left blank unless the tracker actually looked at everyone that "
    "day, and every included account was on file through it. Wise Old Man "
    "records a reading when the hiscores move, so silence means \"played "
    "nothing\" only if somebody asked - and where nobody asked, the one "
    "account that submits its own readings would take the day against five "
    "that were never looked at. Days nobody gained anything on are blank too."
    "\n\n"
    "Today is coloured for whoever leads it, dashed, and counts for nothing "
    "until it is over.\n\n"
    "A month goes to the best average across the days that counted, not to "
    "one measurement across the whole of it: a single 99 on the 3rd would "
    "otherwise take the month whatever anybody did on the other thirty. Each "
    "day is worth points by placing, a win counting for as much as the field "
    "it was won against.\n\n"
    "A month with fewer than two weeks of counted days is not awarded at "
    "all. A month decided on the two days at the end of it is really a "
    "winner of those two days, and a month's name in colour claims more "
    "than that.\n\n"
    "Each day is measured between the two readings bracketing it, taking "
    "whichever of them sits nearer the boundary - the same rule the written "
    "round-ups use, so the two never disagree. Wise Old Man stamps a reading "
    "when the hiscores move, so an evening's last hour often arrives seconds "
    "into the next day: taking the nearer reading is what keeps that work "
    "with the evening it was done in."
)



def _day_cell(day, won, by_name, palette, included):
    """One square: who took the day, or why it is blank."""
    found = won.get(day.strftime("%Y-%m-%d"))
    ahead = day > datetime.now(day.tzinfo)
    if ahead:
        return {"day": day.day, "date": day.strftime("%d %b %Y"), "winner": None,
                "color": None, "ahead": True, "live": False, "note": "not yet"}
    if found is None or found["winner"] is None:
        return {"day": day.day, "date": day.strftime("%d %b %Y"), "winner": None,
                "color": None, "ahead": False, "live": False,
                "note": (found or {}).get("reason") or "nothing recorded"}
    name = by_name.get(found["winner"], found["winner"])
    if found["live"]:
        note = name + " - leading so far, the day is not over"
    elif found["written"]:
        note = name + " - named by the round-up"
    else:
        note = name
    return {"day": day.day, "date": day.strftime("%d %b %Y"), "winner": name,
            "color": palette.get(found["winner"]), "ahead": False,
            "live": found["live"], "note": note}
