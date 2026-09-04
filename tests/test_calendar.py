"""The winner calendar: which day belongs to whom, and which to nobody."""
from conftest import calendar_seed, record_runs, snapshot


def test_a_long_gap_is_not_counted_as_one_days_work(app):
    """Measured from the far side of a seven-week gap, an account that came
    back on the 30th would have all seven weeks folded into that day."""
    from datetime import datetime, timezone

    from wom import winners
    database = calendar_seed(app)
    players = database.players()
    start, end = winners.month_range(
        datetime(2026, 8, 15, tzinfo=timezone.utc), back=0)
    gains = winners.gains_by_day(database, players, start, end)
    on_the_day = gains["2026-08-30"]["scores"]
    # 9500 - 9000, not 9500 - 500: the nearer bracketing reading wins, which
    # is the rule baseline_snapshot follows everywhere else.
    assert on_the_day["other"]["raw"] == 500
    assert "other" in gains["2026-08-30"]["short"], "and it says it saw half a day"


def test_a_day_without_a_reading_is_a_quiet_day_not_an_unknown_one(app):
    """Wise Old Man records a snapshot when the hiscores move, so no reading
    means the account did not play - it must not drop out of the day."""
    from datetime import datetime, timezone

    from wom import winners
    database = calendar_seed(app)
    players = database.players()
    start, end = winners.month_range(
        datetime(2026, 8, 15, tzinfo=timezone.utc), back=0)
    gains = winners.gains_by_day(database, players, start, end)
    # Other has no reading at all on the 29th, but was being tracked by then.
    assert "other" in gains["2026-08-29"]["measured"]
    assert "other" not in gains["2026-08-29"]["scores"], "tracked, and gained nothing"


def test_the_round_up_overrules_the_figures_only_for_the_whole_group(app):
    from datetime import datetime, timezone

    from wom import periods, winners
    database = calendar_seed(app)
    players = database.players()
    window = periods.latest_window("day", datetime(2026, 8, 31, 12,
                                                   tzinfo=timezone.utc))
    database.save_group_summary(window, "A day.", "hash", winner="other")
    start, end = winners.month_range(
        datetime(2026, 8, 15, tzinfo=timezone.utc), back=0)

    whole = winners.daily_winners(database, players, start, end, whole_group=True)
    assert whole[window.key]["winner"] == "other"
    assert whole[window.key]["written"] is True

    # Narrowed to one account, the round-up is answering a different question.
    one = [p for p in players if p["username"] == "zezima"]
    narrowed = winners.daily_winners(database, one, start, end, whole_group=False)
    assert narrowed[window.key]["winner"] == "zezima"


def test_the_calendar_names_a_winner_for_the_month_too(app, client):
    calendar_seed(app)
    body = client.get("/maxing").get_data(as_text=True)
    # The heading is test_boards' business; this test is about the grid.
    assert body.count('class="month"') == 2, "last month and this one"


def test_adding_a_player_does_not_blank_the_days_before_they_arrived(app):
    """A run is evidence about the day it ran. Judged against today's roster,
    a seventh account would retire every day the other six were watched."""
    from datetime import datetime, timezone

    from wom import winners
    database = calendar_seed(app)
    start, end = winners.month_range(
        datetime(2026, 8, 15, tzinfo=timezone.utc), back=0)
    before = winners.polled_days(database, database.players(), start, end)
    assert before, "the fixture polls every day of August"

    database.save_player_details({"id": 3, "username": "newcomer",
                                  "displayName": "Newcomer", "type": "regular"})
    after = winners.polled_days(database, database.players(), start, end)
    assert after == before


def test_the_standings_count_the_days_the_squares_colour(app):
    """One card, one answer. The squares honour a written round-up over the
    figures; the tally beside them was counting the figures regardless."""
    from datetime import datetime, timezone

    from wom import periods
    from wom.web.today import standings
    from wom.web.views import winner_calendar

    database = calendar_seed(app)
    players = database.players()
    palette = {p["username"]: "#123456" for p in players}
    when = datetime(2026, 8, 31, 18, tzinfo=timezone.utc)

    # The figures give the 30th to Other; the round-up for it says Zezima.
    window = periods.latest_window("day", datetime(2026, 8, 31, 12,
                                                   tzinfo=timezone.utc))
    assert window.key == "2026-08-30"
    database.save_group_summary(window, "A day.", "hash", winner="zezima")

    # The squares and the tally are built by different modules now, which is
    # the whole reason to keep asserting they still agree.
    calendar = winner_calendar(database, players, palette, when)
    august = calendar["months"][1]        # last month beside this one
    square = [day for day in august["days"] if day["date"] == "30 Aug 2026"][0]
    credited = {row["name"]: row["xp_wins"] + row["nine_wins"]
                for row in standings(database, players, palette, when)["rows"]}
    assert square["winner"] == "Zezima", "the square went to the round-up's pick"
    assert credited["Zezima"] >= 1, "and so must the tally beside it"
    assert credited.get("Other", 0) == 0, "not to whoever the figures preferred"


def test_a_month_watched_for_less_than_a_fortnight_is_not_awarded(app):
    """Four days at the end of August is not a month anybody competed over,
    and the winner it would name is really the winner of those four days."""
    from datetime import datetime, timezone

    from wom import periods, winners
    database = calendar_seed(app)
    players = database.players()
    start, end = winners.month_range(
        datetime(2026, 8, 15, tzinfo=timezone.utc), back=0)

    counted = winners.counted_days(database, players, start, end)
    assert 0 < counted < winners.MIN_MONTH_DAYS
    assert winners.month_winner(database, players, start, end) is None

    # The daily squares are untouched: those days were watched and stand.
    days = winners.daily_winners(database, players, start, end)
    assert days["2026-08-30"]["winner"], "a day still has a winner"

    # And the monthly round-up is told why, rather than quietly naming the
    # account that happened to be ahead over four days.
    window = periods.latest_window("month", datetime(2026, 9, 2, 12,
                                                     tzinfo=timezone.utc))
    ranked = winners.ranking(database, players, window)
    assert all(row["voided"] for row in ranked)
    from wom.summaries import _ranking_lines
    digest = "\n".join(_ranking_lines(ranked))
    assert "not awarded" in digest and "Winner: nobody" in digest


def test_a_week_is_not_held_to_the_month_s_fortnight(app):
    """A week has seven days in it; asking fourteen would void every one."""
    from datetime import datetime, timezone

    from wom import periods, winners
    database = calendar_seed(app)
    window = periods.latest_window("week", datetime(2026, 9, 2, 12,
                                                    tzinfo=timezone.utc))
    ranked = winners.ranking(database, database.players(), window)
    assert not any(row["voided"] for row in ranked)


def test_a_round_up_that_named_a_winner_stores_it_apart_from_its_prose(app):
    from wom.summaries import split_winner
    players = [{"username": "zezima", "display_name": "Zezima"}]
    assert split_winner("WINNER: Zezima\n\nThe prose.", players) == \
        ("zezima", "The prose.")
    # A name nothing matches is dropped rather than stored as a colour key.
    assert split_winner("WINNER: nobody at all\n\nQuiet.", players) == \
        (None, "Quiet.")
    # An older round-up with no line keeps every word of its text.
    assert split_winner("Just prose.", players) == (None, "Just prose.")


def test_a_day_is_blank_until_every_account_was_being_tracked(app):
    """An account nobody was watching yet cannot lose a day, so whoever was
    being watched would win it by default - a whole month of them."""
    from datetime import datetime, timezone

    from wom import winners
    database = calendar_seed(app)
    players = database.players()
    start, end = winners.month_range(
        datetime(2026, 8, 15, tzinfo=timezone.utc), back=0)
    won = winners.daily_winners(database, players, start, end)

    # Zezima alone had readings on the 1st; Other was not on file until July,
    # so both are tracked by August and the early days are answerable.
    assert won["2026-08-28"]["winner"] == "zezima"

    # Drop Other's history and the same days go blank rather than to Zezima.
    database.connect().execute("DELETE FROM metrics WHERE player_id=2")
    database.connect().execute("DELETE FROM snapshots WHERE player_id=2")
    database.connect().commit()
    thin = winners.daily_winners(database, players, start, end)
    assert thin["2026-08-28"]["winner"] is None
    assert thin["2026-08-28"]["reason"] == "1 of 2 accounts were being tracked"
    # And no month is handed to the only witness either.
    assert winners.month_winner(database, players, start, end) is None


def test_a_day_nobody_played_is_blank_not_a_win(app):
    """Nothing happened, and a colour would say something did."""
    from datetime import datetime, timezone

    from wom import winners
    database = calendar_seed(app)
    # Both on file, both flat: a genuinely quiet day with nobody excluded.
    for pid in (1, 2):
        for day in ("2026-08-24", "2026-08-25"):
            database.save_snapshot(pid, snapshot(day + "T02:00:00.000Z",
                                                 skills={"attack": (400, 30)}))
    players = database.players()
    start, end = winners.month_range(
        datetime(2026, 8, 15, tzinfo=timezone.utc), back=0)
    quiet = winners.daily_winners(database, players, start, end)["2026-08-24"]
    assert quiet["measured"] == 2, "both were being tracked"
    assert quiet["winner"] is None
    assert quiet["reason"] == "nobody gained anything"


def test_a_ninety_nine_takes_the_day_off_a_bigger_number(app):
    """Past 99 a skill stops levelling, so experience there cannot outrank
    somebody who actually reached one."""
    from datetime import datetime, timezone

    from wom import winners
    database = app.config["DATABASE"]
    for pid, name in ((1, "Climber"), (2, "Maxed")):
        database.save_player_details({"id": pid, "username": name.lower(),
                                      "displayName": name, "type": "regular"})
    edge = winners.NINETY_NINE
    # Climber crosses 99 in Attack by a single point.
    database.save_snapshot(1, snapshot("2026-08-19T23:00:00.000Z",
                                       skills={"attack": (edge - 1, 98)}))
    database.save_snapshot(1, snapshot("2026-08-20T23:00:00.000Z",
                                       skills={"attack": (edge, 99)}))
    # Maxed piles on ten million, all of it above 99.
    database.save_snapshot(2, snapshot("2026-08-19T23:00:00.000Z",
                                       skills={"attack": (edge * 2, 99)}))
    database.save_snapshot(2, snapshot("2026-08-20T23:00:00.000Z",
                                       skills={"attack": (edge * 2 + 10000000, 99)}))
    record_runs(database, 2, ["2026-08-20"])

    players = database.players()
    start, end = winners.month_range(
        datetime(2026, 8, 25, tzinfo=timezone.utc), back=0)
    day = winners.daily_winners(database, players, start, end)["2026-08-20"]
    assert day["winner"] == "climber", "one experience point, and a 99, beats 10m"

    found = winners.gains_by_day(database, players, start, end)["2026-08-20"]
    assert found["scores"]["climber"]["nines"] == 1
    # Ten million, all of it past 99, is not a score at all - and it must be
    # judged the same way here as in the standings, or the calendar crowns
    # somebody the round-up beside it calls an empty day.
    assert "maxed" not in found["scores"]
    assert "maxed" in found["measured"], "tracked, and scored nothing"


def test_a_day_spent_entirely_past_99_has_no_winner(app):
    from datetime import datetime, timezone

    from wom import winners
    database = app.config["DATABASE"]
    database.save_player_details({"id": 1, "username": "maxed",
                                  "displayName": "Maxed", "type": "regular"})
    edge = winners.NINETY_NINE
    database.save_snapshot(1, snapshot("2026-08-19T23:00:00.000Z",
                                       skills={"attack": (edge * 2, 99)}))
    database.save_snapshot(1, snapshot("2026-08-20T23:00:00.000Z",
                                       skills={"attack": (edge * 3, 99)}))
    record_runs(database, 1, ["2026-08-20"])
    players = database.players()
    start, end = winners.month_range(
        datetime(2026, 8, 25, tzinfo=timezone.utc), back=0)
    day = winners.daily_winners(database, players, start, end)["2026-08-20"]
    assert day["winner"] is None
    assert day["reason"] == "nobody gained anything"


def test_a_day_nobody_was_polled_on_is_blank(app):
    """Wise Old Man records a reading when the hiscores move, so no reading
    means "played nothing" only if somebody asked. Where nobody asked, the one
    account that submits its own readings would take the day unopposed."""
    from datetime import datetime, timezone

    from wom import winners
    database = calendar_seed(app, polled=False)
    players = database.players()
    start, end = winners.month_range(
        datetime(2026, 8, 15, tzinfo=timezone.utc), back=0)

    unwatched = winners.daily_winners(database, players, start, end)
    assert unwatched["2026-08-28"]["winner"] is None
    assert unwatched["2026-08-28"]["reason"] == "the tracker was not watching that day"
    assert winners.month_winner(database, players, start, end) is None

    # A run that came back with a result for every player is the evidence.
    record_runs(database, len(players), ["2026-08-28"])
    watched = winners.daily_winners(database, players, start, end)
    assert watched["2026-08-28"]["winner"] == "zezima"
    # And only that day: the others still have nobody vouching for them.
    assert watched["2026-08-29"]["reason"] == "the tracker was not watching that day"


def test_today_follows_the_grid_in_a_card_of_its_own(app, client):
    """The squares are finished days; the table is the one still running.

    Two questions, so two cards - and this order, because the running day
    only makes sense once you have seen what a finished one looks like.
    """
    calendar_seed(app)
    body = client.get("/maxing").get_data(as_text=True)
    assert body.index('class="months"') < body.index("Today so far")
    assert body.index("Today so far") < body.index("Experience toward 99 today")
    # Its own card, not a panel inside the calendar's.
    assert 'class="card standing"' in body


def test_today_is_ordered_by_the_same_rule_as_the_squares(app):
    from datetime import datetime, timedelta

    from wom import winners
    from wom.web.today import standings
    database = app.config["DATABASE"]
    for pid, name in ((1, "Climber"), (2, "Maxed")):
        database.save_player_details({"id": pid, "username": name.lower(),
                                      "displayName": name, "type": "regular"})
    edge = winners.NINETY_NINE
    today = winners.today_key()
    # Inside the lookback the states query uses, or neither has a baseline
    # to be measured from and both score nothing.
    yesterday = (datetime.strptime(today, "%Y-%m-%d")
                 - timedelta(days=1)).strftime("%Y-%m-%d")
    database.save_snapshot(1, snapshot(yesterday + "T12:00:00.000Z",
                                       skills={"attack": (edge - 1, 98)}))
    database.save_snapshot(2, snapshot(yesterday + "T12:00:00.000Z",
                                       skills={"attack": (edge * 2, 99)}))
    database.save_snapshot(1, snapshot(today + "T23:59:00.000Z",
                                       skills={"attack": (edge, 99)}))
    database.save_snapshot(2, snapshot(today + "T23:59:00.000Z",
                                       skills={"attack": (edge * 2 + 9000000, 99)}))

    players = database.players()
    palette = {p["username"]: "#fff" for p in players}
    rows = standings(database, players, palette)["rows"]
    # One experience point and a 99 outranks nine million spent past one.
    assert [row["name"] for row in rows] == ["Climber", "Maxed"]
    assert rows[0]["nines"] == 1
    assert rows[1]["moved"] is False, "all of it above 99 counts for nothing"


def test_the_day_in_progress_leads_but_has_not_won(app):
    """Leading at four in the afternoon is not a day won, and must not count
    toward the month either."""
    from datetime import datetime, timedelta

    from wom import winners
    from wom.web.today import standings
    from wom.web.views import winner_calendar
    database = app.config["DATABASE"]
    database.save_player_details({"id": 1, "username": "zezima",
                                  "displayName": "Zezima", "type": "regular"})
    today = winners.today_key()
    yesterday = (datetime.strptime(today, "%Y-%m-%d")
                 - timedelta(days=1)).strftime("%Y-%m-%d")
    database.save_snapshot(1, snapshot(yesterday + "T05:00:00.000Z",
                                       skills={"attack": (1000, 40)}))
    # One reading just after the day opened and one late in it: a day with a
    # single reading is measured from that reading and so gains nothing, which
    # is the same rule every other page follows.
    database.save_snapshot(1, snapshot(today + "T05:00:00.000Z",
                                       skills={"attack": (1000, 40)}))
    database.save_snapshot(1, snapshot(today + "T23:00:00.000Z",
                                       skills={"attack": (500000, 60)}))
    record_runs(database, 1, [yesterday, today])

    players = database.players()
    start, end = winners.month_range(back=0)
    found = winners.daily_winners(database, players, start, end)[today]
    assert found["winner"] == "zezima", "somebody is ahead"
    assert found["live"] is True

    # Ahead, but it buys no month points and no tally mark.
    assert winners.month_points(database, players, start, end).get("zezima", 0) == 0
    palette = {"zezima": "#fff"}
    calendar = winner_calendar(database, players, palette)
    leader = standings(database, players, palette)["rows"][0]
    assert leader["place"] == 1
    assert leader["nine_wins"] == 0 and leader["xp_wins"] == 0
    square = [d for m in calendar["months"] for d in m["days"]
              if d["winner"] and d["live"]]
    assert len(square) == 1, "one square is live, and it is coloured"
    assert "the day is not over" in square[0]["note"]


def test_wins_are_split_by_how_the_day_was_taken(app):
    """A day is taken either by reaching a 99 or, where nobody did, on
    experience - so the tallies are kept apart."""
    from datetime import datetime

    from wom import winners
    from wom.web.today import standings
    database = app.config["DATABASE"]
    for pid, name in ((1, "Climber"), (2, "Grinder")):
        database.save_player_details({"id": pid, "username": name.lower(),
                                      "displayName": name, "type": "regular"})
    edge = winners.NINETY_NINE

    def reading(pid, day, hour, attack, level):
        database.save_snapshot(pid, snapshot(
            "{}T{}:00:00.000Z".format(day, hour),
            skills={"attack": (attack, level)}))

    # Experience only ever goes up, so each reading carries the last one
    # forward - a dip would look like a second crossing of 99.
    reading(1, "2026-08-09", "05", edge - 500000, 90)
    reading(2, "2026-08-09", "05", 100000, 90)
    reading(1, "2026-08-10", "05", edge - 500000, 90)
    reading(2, "2026-08-10", "05", 100000, 90)
    # The 10th: Climber crosses 99, so it is a 99 win.
    reading(1, "2026-08-10", "23", edge, 99)
    reading(2, "2026-08-10", "23", 100000, 90)
    # The 11th: nobody crosses - Climber is already past it - and Grinder
    # simply gains the most, so it is an experience win.
    reading(1, "2026-08-11", "05", edge, 99)
    reading(2, "2026-08-11", "05", 100000, 90)
    reading(1, "2026-08-11", "23", edge + 10, 99)
    reading(2, "2026-08-11", "23", 900000, 95)
    record_runs(database, 2, ["2026-08-10", "2026-08-11"])

    players = database.players()
    palette = {p["username"]: "#fff" for p in players}
    rows = {row["name"]: row for row in
            standings(database, players, palette,
                      when=datetime(2026, 8, 15))["rows"]}
    assert rows["Climber"]["nine_wins"] == 1 and rows["Climber"]["xp_wins"] == 0
    assert rows["Grinder"]["nine_wins"] == 0 and rows["Grinder"]["xp_wins"] == 1
