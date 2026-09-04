"""The written round-ups: the feed, the tree, and the verdict beside them."""
from conftest import calendar_seed, round_ups, seed


def test_recaps_lead_with_the_day_and_the_month_and_nothing_else(client, app):
    """The recap is the Maxing Leaderboard's feed, and the leaderboard
    colours days and awards months. A weekly or yearly one described a window
    with no result to put beside it."""
    from wom import periods
    database = seed(app)
    for key in periods.SUMMARY_PERIODS:
        database.save_group_summary(periods.latest_window(key),
                                    "A {} recap.".format(key), "hash")
    body = client.get("/recaps").get_data(as_text=True)
    for key in periods.GROUP_PERIODS:
        assert "A {} recap.".format(key) in body, key
    for key in set(periods.SUMMARY_PERIODS) - set(periods.GROUP_PERIODS):
        assert "A {} recap.".format(key) not in body, key


def test_the_players_page_carries_figures_and_no_prose(client, app):
    """Two pages showing the same note invited the reader to expect them to
    say the same thing. This page answers what the figures are; how it has
    been going is the Maxing page's question."""
    from wom import periods
    database = seed(app)
    database.save_summary(1, periods.latest_window("day"),
                          "A note about yesterday.", "hash")
    body = client.get("/api/player/zezima?period=Day").get_json()
    assert "note" not in body
    assert body["groups"], "the figures are still all here"


def test_an_accounts_recaps_are_all_five_windows_in_the_tree(client, app):
    """All five, where the group recap has two: these are about one account's
    progress, which a quarter still says something about."""
    from wom import periods
    from wom.web.views import recap_tree
    database = seed(app)
    for key in periods.SUMMARY_PERIODS:
        database.save_summary(1, periods.latest_window(key),
                              "A {} note.".format(key), "hash")
    branches = {branch["username"]: branch
                for branch in recap_tree(database, database.players(),
                                         {"zezima": "#fff"})}
    folders = {f["period"]: f for f in branches["zezima"]["folders"]}
    assert set(folders) == set(periods.SUMMARY_PERIODS)
    assert folders["quarter"]["entries"][0]["paragraphs"] == ["A quarter note."]


def test_the_maxing_row_opens_onto_skills_and_nothing_else(client, app):
    """The day's figures are what the row was opened for; a fold of prose
    above them would bury it."""
    from wom import periods
    database = seed(app)
    database.save_summary(1, periods.latest_window("day"), "A note.", "hash")
    body = client.get("/api/maxing/player/zezima").get_json()
    assert "recaps" not in body
    assert "rows" in body and "total" in body


def test_a_players_own_note_carries_no_leaderboard_verdict(app):
    """It is not the calendar's feed, and "no verdict" beside one would
    invent a question it was never answering."""
    from wom import periods
    from wom.web.views import player_recaps
    database = seed(app)
    database.save_summary(1, periods.latest_window("day"), "A note.", "hash")
    entry = player_recaps(database, database.players()[0])[0]["entries"][0]
    assert entry["judged"] is False


def test_a_recap_carries_what_the_leaderboard_decided(app, client):
    """The recap is the calendar's feed, so each entry says what the calendar
    said. The prose judges on its own reading and the squares judge on the
    rule; where they differ, the squares are what the page was coloured by."""
    from datetime import datetime, timezone

    from wom import periods
    from wom.web.views import recap_feed

    database = calendar_seed(app)
    window = periods.latest_window("day", datetime(2026, 8, 31, 12,
                                                   tzinfo=timezone.utc))
    database.save_group_summary(window, "A day.", "hash", winner="zezima")

    players = database.players()
    palette = {p["username"]: "#123456" for p in players}
    feed = {entry["period"]: entry for entry in recap_feed(database, players, palette)}
    assert "day" in feed
    assert feed["day"]["winner"], "the leaderboard's verdict rides along"
    assert feed["day"]["color"] == "#123456"


def test_a_month_short_of_a_fortnight_says_so_rather_than_leaving_a_blank(app):
    from datetime import datetime, timezone

    from wom import periods
    from wom.web.views import recap_tree

    database = calendar_seed(app)
    window = periods.latest_window("month", datetime(2026, 9, 15, 12,
                                                     tzinfo=timezone.utc))
    database.save_group_summary(window, "A month.", "hash")
    players = database.players()
    tree = recap_tree(database, players,
                      {p["username"]: "#fff" for p in players})
    group = [branch for branch in tree if branch["username"] == "__maxing__"][0]
    folders = {f["period"]: f for f in group["folders"]}
    entry = folders["month"]["entries"][0]
    assert entry["unawarded"], "August was four days watched, not a month"
    assert entry["winner"] is None


def test_the_tree_holds_the_group_and_every_account(app, client):
    """Two shapes under one tree: the group's two windows with the verdict
    each was given, and every account's five without one."""
    from wom import periods
    database = seed(app)
    # No apostrophes: Jinja escapes them, and a test that greps the rendered
    # page has to grep what was rendered.
    database.save_summary(1, periods.latest_window("quarter"),
                          "A note from Zezima.", "hash")
    database.save_group_summary(periods.latest_window("day"),
                                "A recap for the group.", "hash")
    body = client.get("/recaps").get_data(as_text=True)
    assert "A recap for the group." in body
    assert "A note from Zezima." in body, "an account's own notes are here too"
    assert body.index("A recap for the group.") < body.index("A note from Zezima."), (
        "the group leads, the accounts follow")


def test_the_old_round_ups_link_still_arrives(client, app):
    """Links outlive renames."""
    seed(app)
    moved = client.get("/summaries?player=zezima&picked=1")
    assert moved.status_code == 301
    assert "/recaps" in moved.headers["Location"]
    assert "player=zezima" in moved.headers["Location"], "and keeps the ticks"


def test_the_leaderboard_ignores_the_ticks(app, client):
    """One competition with one answer.

    Narrowed to some of the accounts it silently becomes a different
    competition, and the squares recolour to a result nobody was playing for.
    """
    calendar_seed(app)
    everyone = client.get("/leaderboards").get_data(as_text=True)
    narrowed = client.get("/leaderboards?picked=1&player=zezima").get_data(as_text=True)

    def squares(body):
        start = body.index('class="months"')
        return body[start:body.index("Today so far", start)]

    assert squares(everyone) == squares(narrowed), "the calendar is unmoved"

    def standings(body):
        start = body.index("Today so far")
        return body[start:body.index("Experience toward 99", start)]

    assert standings(everyone) == standings(narrowed), (
        "and so is the table that tallies the same days")


def test_unticking_everyone_still_leaves_a_leaderboard(app, client):
    """A page whose whole subject is the group cannot be emptied by the ticks."""
    calendar_seed(app)
    body = client.get("/leaderboards?picked=1").get_data(as_text=True)
    assert "No players are ticked." not in body
    assert 'class="today-row' in body, "every account is still ranked"


def test_a_recap_verdict_is_the_leaderboards_whatever_is_ticked(app, client):
    """The chip quotes the calendar, so it has to be asked the calendar's
    question - not a narrower one that answers differently."""
    from datetime import datetime, timezone

    from wom import periods
    from wom.web.views import recap_feed

    database = calendar_seed(app)
    window = periods.latest_window("day", datetime(2026, 8, 31, 12,
                                                   tzinfo=timezone.utc))
    database.save_group_summary(window, "A day.", "hash")

    everyone = database.players()
    palette = {p["username"]: "#123456" for p in everyone}
    one = [p for p in everyone if p["username"] == "zezima"]
    assert (recap_feed(database, everyone, palette)[0]["winner"]
            == recap_feed(database, one, palette)[0]["winner"])


def test_a_week_now_gets_a_round_up_between_the_day_and_the_month():
    from wom import periods
    assert periods.GROUP_PERIODS == ("day", "week", "month")


def test_the_top_shows_one_board_at_a_time_defaulting_to_maxing(client, app):
    round_ups(app.config["DATABASE"])
    seed(app)
    page = client.get("/recaps").get_data(as_text=True)
    assert 'id="boards"' in page
    maxing = page.split('data-board="maxing"')[1][:60]
    grinding = page.split('data-board="grinding"')[1][:60]
    assert "hidden" not in maxing, "maxing is the one on show"
    assert "hidden" in grinding, "grinding is rendered but folded away"


def test_both_boards_round_ups_are_on_the_page_for_a_reader_without_scripts(
        client, app):
    round_ups(app.config["DATABASE"])
    seed(app)
    page = client.get("/recaps").get_data(as_text=True)
    assert "maxing day." in page and "grinding day." in page
    assert "maxing week." in page and "grinding month." in page


def test_a_weekly_round_up_carries_no_verdict(client, app):
    """A week is not awarded on either leaderboard, so a winner chip beside
    one would answer a question nobody asked."""
    from wom import periods
    from wom.web import views
    database = seed(app)
    window = periods.latest_window("week")
    database.save_group_summary(window, "A week.", "h", winner="zezima")
    feed = views.recap_feed(database, database.players(), {})
    weekly = [row for row in feed if row["period"] == "week"][0]
    assert weekly["judged"] is False


def test_the_tree_names_the_boards_rather_than_calling_them_group(client, app):
    round_ups(app.config["DATABASE"])
    seed(app)
    from wom.web import views
    database = app.config["DATABASE"]
    tree = views.recap_tree(database, database.players(), {})
    names = [branch["player"] for branch in tree]
    assert "Maxing" in names and "Grinding" in names
    assert "Group" not in names


def test_a_board_with_nothing_written_is_left_out_of_the_tree(app):
    from wom.web import views
    database = seed(app)
    round_ups(database, boards=("maxing",))
    tree = views.recap_tree(database, database.players(), {})
    names = [branch["player"] for branch in tree]
    assert "Maxing" in names and "Grinding" not in names


def test_a_weekly_digest_reviews_the_days_and_the_month_so_far(db, player):
    """A week is not judged in its own right, so its round-up is a review:
    who took each of its days, and where that leaves the month."""
    from wom import periods, summaries
    from wom.config import Config
    window = periods.latest_window("week")
    digest = summaries.build_group_digest(db, Config(), [player], window,
                                          board="grinding")
    assert "Grinding - judged on total experience" in digest
    assert "Days of this week, and who took each" in digest
    assert "The month so far" in digest


def test_a_daily_digest_says_which_competition_it_is(db, player):
    from wom import periods, summaries
    from wom.config import Config
    window = periods.latest_window("day")
    for board, phrase in (("maxing", "up to level 99"),
                          ("grinding", "all of it")):
        digest = summaries.build_group_digest(db, Config(), [player], window,
                                              board=board)
        assert phrase in digest, board
