"""Chart figures remembered between requests, and never past the data behind them."""

from datetime import timedelta, timezone

from conftest import snapshot, this_week

from wom.config import Config
from wom.memo import Memo

CHARTS = ("group_totals", "standings", "skill_gains", "xp_trend", "boss_gains",
          "level_trend", "log_and_clues")


def group(app):
    """Three friends with a week of readings each."""
    database = app.config["DATABASE"]
    settings = Config()
    settings["usernames"] = ["Zezima", "Other", "Third"]
    settings.save()
    for pid, name in ((1, "zezima"), (2, "other"), (3, "third")):
        database.save_player_details({"id": pid, "username": name,
                                      "displayName": name.title()})
        for day, n in zip(this_week(), (1, 3), strict=True):
            for hour in (6, 18):
                database.save_snapshot(pid, snapshot(
                    "{}T{:02d}:00:00.000Z".format(day, hour),
                    skills={"attack": (pid * 1000 * n + hour, 40 + n),
                            "overall": (pid * 5000 * n + hour, 500 + n)},
                    bosses={"zulrah": pid * n + hour // 6},
                    activities={"collections_logged": pid + n}))
    return database


def charts(client, who):
    query = "period=Week&picked=1&" + "&".join("player=" + p for p in who)
    return {key: client.get("/api/chart/{}?{}".format(key, query)).get_json()
            for key in CHARTS}


def test_every_chart_is_the_same_remembered_or_not(client, app):
    """The whole point is that nobody can tell: the same answer, sooner."""
    group(app)
    memo = app.config["MEMO"]
    selections = (["zezima", "other", "third"], ["zezima", "other"],
                  ["other", "third"], ["zezima", "other", "third"])
    remembered = [charts(client, who) for who in selections]
    assert memo._entries, "nothing was remembered"

    app.config["MEMO"] = None
    fresh = [charts(client, who) for who in selections]
    assert remembered == fresh


def test_a_new_reading_is_seen_at_once(client, app):
    """Asked of the experience, which comes from the remembered gains. The
    row's levels are read from the bracketing snapshots on every request, so
    a test of the whole row passed with nothing remembered correctly."""
    database = group(app)

    def xp():
        rows = charts(client, ["zezima"])["standings"]["rows"]
        return rows[0]["xp"]

    before = xp()
    last = this_week()[1]
    database.save_snapshot(1, snapshot(last + "T23:00:00.000Z",
                                       skills={"attack": (999999, 90),
                                               "overall": (999999, 900)}))
    assert xp() > before, "a remembered figure outlived the data behind it"


def test_a_caller_cannot_change_what_the_next_one_is_given(app):
    """gains() hands out a dict, and a builder that added to it would have
    added to every later request's answer too."""
    from wom.context import ViewContext
    from wom.web.timespan import Timespan

    database = group(app)
    memo = Memo()
    span = Timespan(this_week()[0] + "T00:00:00.000Z", None, "Week", key="week")
    first = ViewContext(database, Config(), selected=[], span=span, memo=memo)
    got = first.gains(1, "skill")
    got["attack"] = -1
    second = ViewContext(database, Config(), selected=[], span=span, memo=memo)
    assert second.gains(1, "skill")["attack"] != -1


def test_a_new_version_forgets_everything():
    memo = Memo()
    assert memo.get("v1", "k", lambda: 1) == 1
    assert memo.get("v1", "k", lambda: 2) == 1, "remembered"
    assert memo.get("v2", "k", lambda: 3) == 3, "the data moved"


def test_a_remembered_figure_expires_whatever_the_version_says():
    """The version is a few cheap reads, not a proof; the clock backs it."""
    now = [0.0]
    memo = Memo(ttl=10, clock=lambda: now[0])
    memo.get("v", "k", lambda: 1)
    now[0] = 9
    assert memo.get("v", "k", lambda: 2) == 1
    now[0] = 21
    assert memo.get("v", "k", lambda: 3) == 3


def test_the_memo_keeps_only_so_many():
    memo = Memo(size=3)
    for n in range(5):
        memo.get("v", n, lambda n=n: n)
    assert list(memo._entries) == [2, 3, 4], "the least recently used went"


def test_a_rolling_window_opens_on_the_update_slot(app):
    """Opening this second, every request asked a different question and
    none of the answers could be kept for the next one."""
    from wom.scheduler import previous_slot
    from wom.util import api_stamp
    from wom.web.timespan import current_timespan

    with app.test_request_context("/?period=Week"):
        span = current_timespan()
    slot = previous_slot().astimezone(timezone.utc)
    assert span.since == api_stamp(slot - timedelta(days=7))
