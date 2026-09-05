"""One day, one answer: the row, its breakdown and the chart agreeing."""
from conftest import seed, snapshot


def _midnight_jump(app):
    """An account whose gain lands in the reading just after midnight.

    Wise Old Man stamps a reading when the hiscores move, so the work done in
    the last minutes of an evening arrives seconds into the next day. Which
    reading opens the day therefore decides whose day it counts toward.
    """
    from datetime import timedelta

    from wom import winners
    database = app.config["DATABASE"]
    database.save_player_details({"id": 1, "username": "zezima",
                                  "displayName": "Zezima", "type": "regular"})
    opens, _closes = winners.today_range()

    def at(delta, herblore):
        when = (opens + delta).astimezone(__import__("datetime").timezone.utc)
        database.save_snapshot(1, snapshot(
            when.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            skills={"herblore": (herblore, 70), "overall": (herblore, 70)}))

    at(timedelta(minutes=-10), 2_000_000)          # 23:50, before midnight
    at(timedelta(seconds=46), 2_180_000)           # 00:00:46 - last night's work
    at(timedelta(hours=8), 2_220_000)              # this morning's, 40,000 of it
    return database


def test_the_row_its_breakdown_and_the_chart_agree(app):
    """They are three views of one number and used to give two answers.

    The row measured from the nearer of the readings bracketing midnight; the
    breakdown and the chart always measured from the last one before it. A
    reading landing seconds after midnight carries the previous evening, so
    the row said 40,991 while the breakdown explaining it said 399,457.
    """
    from wom.web import today as today_mod
    database = _midnight_jump(app)
    players = database.players()
    palette = {"zezima": "#fff"}

    row = today_mod.standings(database, players, palette)["rows"][0]
    breakdown = today_mod.breakdown(database, players[0])
    line = today_mod.trend(database, players, lambda p: "#fff")["series"][0]

    assert row["capped"] == "40,000", "the day starts at the nearer reading"
    assert int(breakdown["total"]) == 40000
    assert line["points"][-1][1] == 40000, "the table is the chart's right-hand end"


def test_a_window_an_account_predates_still_produces_a_digest(app):
    """The landmark line queried a column the sparse-metrics migration had
    dropped, so every window an account was not tracked through raised - and
    one such account took the whole group's recap down with it."""
    from wom import periods, summaries
    database = app.config["DATABASE"]
    database.save_player_details({"id": 1, "username": "zezima",
                                  "displayName": "Zezima", "type": "regular"})
    # Tracked only from 2026, so the 2025 window has no reading inside it.
    database.save_snapshot(1, snapshot("2026-03-01T00:00:00.000Z",
                                       skills={"overall": (5_000_000, 900)}))
    window = periods.latest_window("year")
    text = summaries._nearest_reading(database, database.players()[0], window)
    assert "Nearest reading" in text
    assert "total level 900" in text


def test_one_uncovered_account_cannot_sink_the_group_recap(app):
    """It is written for everyone, so it must survive any one of them."""
    from wom import periods, summaries
    from wom.config import Config
    database = seed(app)
    database.save_player_details({"id": 9, "username": "newbie",
                                  "displayName": "Newbie", "type": "regular"})
    database.save_snapshot(9, snapshot("2026-03-01T00:00:00.000Z",
                                       skills={"overall": (1000, 30)}))
    digest = summaries.build_group_digest(database, Config(), database.players(),
                                          periods.latest_window("year"))
    assert "Newbie" in digest


def test_a_skill_that_was_unranked_still_counts_toward_the_day(app):
    """Unranked means below the hiscore cutoff, so it counts from zero - the
    same rule the Overview chart follows. Skipped, the day a new skill first
    ranks scores nothing here while the chart credits every point of it."""
    from wom import winners
    before = {"attack": 1_000_000}                      # sailing not yet ranked
    after = {"attack": 1_000_000, "sailing": 300_000}
    assert int(winners.measure(before, after)["capped"]) == 300_000
    assert int(winners.measure_by_skill(before, after)["sailing"]["capped"]) == 300_000


def test_efficient_hours_keep_their_decimal(app):
    """fmt_int threw the half away, and on an exact half rounded to even:
    500.5 showed as 500 while 500.6 showed as 501."""
    from wom.util import fmt_hours
    from wom.web import views
    assert fmt_hours(500.5) == "500.5"
    assert fmt_hours(100.25) == "100.2" or fmt_hours(100.25) == "100.3"
    assert fmt_hours(None) == "-"

    database = seed(app)
    database.save_player_details({"id": 1, "username": "zezima",
                                  "displayName": "Zezima", "type": "regular",
                                  "ehp": 500.5, "ehb": 20.4})
    row = views.player_rows(database, database.players(), {"zezima": "#fff"})[0]
    assert row["ehp"] == "500.5" and row["ehb"] == "20.4"


def test_a_digest_states_its_own_board_rule_and_not_the_other_ones(db, player):
    """One digest, one rule.

    The standings header spelled Maxing out whatever board was asking, so a
    Grinding digest named Grinding as the competition at the top and then
    explained its own order by a cap it does not have. Two contradictory
    statements of the rule in one prompt, and the model got to choose.
    """
    from wom import periods, summaries
    from wom.config import Config

    window = periods.latest_window("day")
    config = Config()
    players = [player]

    maxing = summaries.build_group_digest(db, config, players, window, "maxing")
    grinding = summaries.build_group_digest(db, config, players, window,
                                            "grinding")

    assert "Standings by the Maxing rule" in maxing
    assert "Standings by the Grinding rule" in grinding
    assert "Standings by the Maxing rule" not in grinding, (
        "the grinding digest explains itself by the other board's rule")
    # And the cap is the whole difference, so it must not be claimed here.
    assert "no cap at level 99" in grinding
    assert "only up to level" in maxing
