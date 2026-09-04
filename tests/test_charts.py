"""The chart builders, and the figures the cards and standings show."""

import pytest
from conftest import seed, snapshot


def test_every_described_chart_has_a_builder():
    """Describing a chart and forgetting to build it used to be silent."""
    from wom import catalog
    from wom.web import data as _data  # importing is what attaches them
    assert _data is not None

    missing = [s.key for s in catalog.SUMMARY_CHARTS if s.build is None]
    assert missing == [], "described but never built: {}".format(missing)


def test_the_group_tiles_total_what_the_per_player_split_adds_up_to(client, app):
    """Each tile is a headline with its own breakdown behind it, and a
    headline that does not equal its parts is worse than no headline."""
    database = seed(app)
    database.save_player_details({"id": 2, "username": "other",
                                  "displayName": "Other", "type": "regular"})
    for day, xp in (("2026-08-25", 500), ("2026-08-31", 2500)):
        database.save_snapshot(2, snapshot(day + "T12:00:00.000Z",
                                           skills={"attack": (xp, 30)},
                                           bosses={"zulrah": xp // 100}))

    body = client.get("/api/chart/group_totals?period=Week").get_json()

    assert "empty" not in body, body.get("empty")
    assert [t["key"] for t in body["tiles"]] == [
        "levels", "xp", "xp99", "kills", "collections", "clues"]
    for tile in body["tiles"]:
        assert tile["total"] == sum(r["value"] for r in tile["rows"]), tile["key"]
        # Read to find out who carried it, which display order buries.
        values = [r["value"] for r in tile["rows"]]
        assert values == sorted(values, reverse=True), tile["key"]


def test_experience_toward_99_is_capped_the_way_the_leaderboard_caps_it(client, app):
    """The tile sits beside one counting every point, and the gap between
    them is the whole reason both are shown. If it drifted from the Maxing
    rule the card would be quietly contradicting another page."""
    from wom.winners import NINETY_NINE

    database = app.config["DATABASE"]
    database.save_player_details({"id": 1, "username": "zezima",
                                  "displayName": "Zezima", "type": "regular"})
    # Opens a hair under 99 and finishes well past it: only the experience
    # below the cap counts, and the rest is what the leaderboard ignores.
    for day, xp in (("2026-08-25", NINETY_NINE - 1000),
                    ("2026-08-31", NINETY_NINE + 5000)):
        database.save_snapshot(1, snapshot(day + "T12:00:00.000Z",
                                           skills={"attack": (xp, 99)}))

    tiles = {t["key"]: t for t in
             client.get("/api/chart/group_totals?period=Week").get_json()["tiles"]}

    assert tiles["xp"]["total"] == 6000, "every point gained"
    assert tiles["xp99"]["total"] == 1000, "only the part below the cap"


def test_the_two_trend_cards_offer_both_readings():
    """The trend cards are the ones an account's absolute value makes
    unreadable - six accounts spanning 1,600 total levels put every month of
    progress inside a few pixels - so they are the two carrying a Gained
    mode. The stacked cards already plot a change and need no second reading.
    """
    from wom import catalog

    assert {s.key for s in catalog.SUMMARY_CHARTS if s.modes} == {
        "level_trend", "log_and_clues"}
    for spec in catalog.SUMMARY_CHARTS:
        if not spec.modes:
            continue
        # The first mode is what the card opens on, so it has to be the
        # reading the card had before it had any - otherwise this is a
        # redesign of two charts rather than an addition to them.
        assert spec.modes[0] == "Total"
        assert spec.as_dict()["modes"] == spec.modes


def test_a_trend_payload_names_the_axis_for_both_readings(client, app):
    """The browser subtracts to get the Gained series but cannot rename the
    axis for it - "Attack level" has to become "Levels gained" rather than
    growing a suffix - so both labels travel with the payload."""
    seed(app)
    body = client.get("/api/chart/level_trend"
                      "?from=2026-08-24&to=2026-09-01&tzoffset=0"
                      "&choice=Attack").get_json()

    assert "empty" not in body, body.get("empty")
    assert body["ylabel"] == "Attack level"
    assert body["ylabelGained"] == "Levels gained"
    assert body["tooltipGained"] == {"style": "count", "unit": "levels"}


def test_experience_gained_is_counted_from_the_start_of_the_window(client, app):
    """Every line starts at zero, so accounts spanning 6.7M to 265M total
    experience are comparable at all. The reading a gain is measured from
    sits before the window, which is exactly what makes the first plotted
    point non-zero if it is used as-is rather than subtracted."""
    database = app.config["DATABASE"]
    database.save_player_details({"id": 1, "username": "zezima",
                                  "displayName": "Zezima", "type": "regular"})
    # The opening reading sits before the window on purpose: it is the one a
    # gain is measured from, and using it as-is is the mistake under test.
    for day, xp in (("2026-08-25", 1000), ("2026-08-28", 3000),
                    ("2026-08-31", 5000)):
        database.save_snapshot(1, snapshot(day + "T12:00:00.000Z",
                                           skills={"overall": (xp, 40)}))

    body = client.get("/api/chart/xp_trend"
                      "?from=2026-08-26&to=2026-09-01&tzoffset=0").get_json()

    assert "empty" not in body, body.get("empty")
    points = body["series"][0]["points"]
    assert points[0][1] == 0, "the line has to open on zero"
    assert points[-1][1] == 4000, "5,000 measured from the 1,000 it opened on"
    assert body["ylabel"] == "XP gained"


def test_a_years_old_reading_does_not_become_this_period_s_gain(client, app):
    """Wise Old Man's history has holes, so the reading before a window can be
    years before it. Measured from there an account reports four years of
    experience as this month's - eighteen times the standings figure on the
    same page, and enough to reorder the group. The shape here is a real one:
    an account last read in 2022 and next read inside the window."""
    database = app.config["DATABASE"]
    database.save_player_details({"id": 1, "username": "zezima",
                                  "displayName": "Zezima", "type": "regular"})
    for day, xp in (("2022-05-21", 5830826), ("2026-08-06", 92203156),
                    ("2026-08-31", 97332768)):
        database.save_snapshot(1, snapshot(day + "T12:00:00.000Z",
                                           skills={"overall": (xp, 2021)}))

    body = client.get("/api/chart/xp_trend"
                      "?from=2026-08-02&to=2026-09-02&tzoffset=0").get_json()

    points = body["series"][0]["points"]
    assert points[0][1] == 0
    assert points[-1][1] == 5129612, "the month, not the four years before it"


def test_the_experience_line_ends_where_the_standings_row_says(client, app):
    """The two cards sit six inches apart on the same page answering the same
    question, so they measure from the same reading rather than each picking
    one. They agree by construction: both go through bounds_for."""
    database = app.config["DATABASE"]
    database.save_player_details({"id": 1, "username": "zezima",
                                  "displayName": "Zezima", "type": "regular"})
    for day, xp in (("2022-05-21", 5830826), ("2026-08-06", 92203156),
                    ("2026-08-31", 97332768)):
        database.save_snapshot(1, snapshot(day + "T12:00:00.000Z",
                                           skills={"overall": (xp, 2021),
                                                   "attack": (xp, 99)}))

    window = "?from=2026-08-02&to=2026-09-02&tzoffset=0"
    line = client.get("/api/chart/xp_trend" + window).get_json()
    standings = client.get("/api/chart/standings" + window).get_json()

    assert line["series"][0]["points"][-1][1] == standings["rows"][0]["xp"]


def test_the_experience_line_sits_under_the_bar_chart_it_explains(client, app):
    """Placement is the point of the card, not a detail of it: the columns
    say what the group trained and the line says when, so they are read as a
    pair. Display order is this tuple's order, so the position is the code."""
    from wom import catalog

    order = [s.key for s in catalog.SUMMARY_CHARTS]
    assert order.index("xp_trend") == order.index("skill_gains") + 1
    assert order.index("xp_trend") < order.index("boss_gains")


def test_a_builder_for_an_unknown_chart_is_refused():

    from wom.catalog import chart
    with pytest.raises(KeyError):
        chart("no_such_chart")(lambda ctx, choice: None)


def test_the_newest_round_up_is_readable_without_clicking(client, app):
    """The Claude spend buys this text; it was two clicks down a closed tree."""
    from wom import periods
    database = seed(app)
    window = periods.latest_window("day")
    database.save_group_summary(window, "Everyone had a quiet day.", "hash")
    body = client.get("/recaps").get_data(as_text=True)
    assert "Everyone had a quiet day." in body
    assert "Daily" in body


def test_standings_answer_who_won(client, app):
    seed(app)
    rows = client.get("/api/chart/standings?period=Week").get_json()["rows"]
    assert rows and "xp" in rows[0] and "kills" in rows[0] and "levels" in rows[0]
    assert rows == sorted(rows, key=lambda r: -r["xp"]), "the leader comes first"


def test_the_player_ticks_filter_the_players_page(client, app):
    """They filter every other page; this one used to ignore them."""
    database = seed(app)
    database.save_player_details({"id": 2, "username": "other",
                                  "displayName": "Other", "type": "regular"})
    from wom.config import Config
    settings = Config()
    settings["usernames"] = ["Zezima", "Other"]
    settings.save()

    both = client.get("/players").get_data(as_text=True)
    one = client.get("/players?player=zezima").get_data(as_text=True)
    assert both.count('class="player-row"') == 2
    assert one.count('class="player-row"') == 1


def test_the_standings_row_carries_what_the_group_tiles_do(client, app):
    """One card answers "what did we do" and the other "who did what", so a
    reader goes from a tile to the account that carried it without changing
    card. Both come from _player_totals, so they cannot answer differently
    about the same account over the same window."""
    database = seed(app)
    database.save_player_details({"id": 2, "username": "other",
                                  "displayName": "Other", "type": "regular"})
    for day, n in (("2026-08-25", 500), ("2026-08-31", 2500)):
        database.save_snapshot(2, snapshot(
            day + "T12:00:00.000Z", skills={"attack": (n, 30)},
            bosses={"zulrah": n // 100},
            activities={"collections_logged": n // 250,
                        "clue_scrolls_hard": n // 500}))

    rows = {r["username"]: r for r in
            client.get("/api/chart/standings?period=Week").get_json()["rows"]}
    tiles = {t["key"]: t for t in
             client.get("/api/chart/group_totals?period=Week").get_json()["tiles"]}

    for key in ("levels", "xp", "xp99", "kills", "collections", "clues"):
        assert key in rows["other"], "the row is missing {}".format(key)
        split = {r["username"]: r["value"] for r in tiles[key]["rows"]}
        for username, row in rows.items():
            assert row[key] == split[username], "{} for {}".format(key, username)
        # And the tile's headline is still those same rows added up.
        assert tiles[key]["total"] == sum(split.values()), key


def test_the_standings_are_still_sorted_by_experience_gained(client, app):
    """Six more columns must not move what the table ranks on: XP gained is
    the first column because it is the one the order means."""
    database = seed(app)
    database.save_player_details({"id": 2, "username": "other",
                                  "displayName": "Other", "type": "regular"})
    # Fewer kills, far more experience - so the two orders disagree.
    for day, xp in (("2026-08-25", 1_000_000), ("2026-08-31", 9_000_000)):
        database.save_snapshot(2, snapshot(day + "T12:00:00.000Z",
                                           skills={"attack": (xp, 80)},
                                           bosses={"zulrah": 1}))
    rows = client.get("/api/chart/standings?period=Week").get_json()["rows"]
    assert [r["username"] for r in rows] == ["other", "zezima"]
    assert rows[0]["kills"] < rows[1]["kills"], "and not by kills"
