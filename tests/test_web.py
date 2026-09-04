"""The HTTP surface: what is public, what is not, and what is refused."""

import json
import os

from conftest import snapshot


def seed(app):
    database = app.config["DATABASE"]
    database.save_player_details({"id": 1, "username": "zezima",
                                  "displayName": "Zezima", "type": "regular"})
    for day, xp in (("2026-08-25", 1000), ("2026-08-31", 5000)):
        database.save_snapshot(1, snapshot(day + "T12:00:00.000Z",
                                           skills={"attack": (xp, 40)},
                                           bosses={"zulrah": xp // 100}))
    return database


# -- what anyone may see --------------------------------------------------

def test_public_pages_render(client, app):
    seed(app)
    for path in ("/", "/maxing", "/milestones", "/recaps", "/players", "/export"):
        assert client.get(path).status_code == 200, path


def test_chart_data_is_json(client, app):
    seed(app)
    body = client.get("/api/chart/skill_gains?period=Week").get_json()
    assert body["type"] == "stacked"
    assert body["series"][0]["name"] == "Zezima"


def test_an_unknown_chart_is_404(client):
    assert client.get("/api/chart/nonsense").status_code == 404


def test_unticking_everyone_says_so_rather_than_showing_everyone(client, app):
    seed(app)
    body = client.get("/api/chart/skill_gains?period=Week&picked=1").get_json()
    assert "empty" in body, "an explicit empty selection must not fall back to all"


def test_a_bare_link_still_shows_everyone(client, app):
    seed(app)
    body = client.get("/api/chart/skill_gains?period=Week").get_json()
    assert body.get("series"), "a link with no filters is not an empty selection"


# -- the admin boundary ---------------------------------------------------

ADMIN_GETS = ("/admin", "/admin/prompts", "/admin/status")
ADMIN_POSTS = ("/admin/settings", "/admin/colour", "/admin/prune",
               "/admin/resume", "/admin/run/update", "/admin/run/summarise")


def test_admin_is_closed_to_the_signed_out(client):
    for path in ADMIN_GETS:
        assert client.get(path).status_code == 302, path
    for path in ADMIN_POSTS:
        response = client.post(path)
        assert response.status_code == 302, path
        assert "/admin/login" in response.headers["Location"], path


def test_a_wrong_password_grants_nothing(client):
    client.post("/admin/login", data={"password": "not it"})
    assert client.get("/admin").status_code == 302


def test_signing_in_and_out(signed_in, app):
    seed(app)
    assert signed_in.get("/admin").status_code == 200
    signed_in.post("/admin/logout")
    assert signed_in.get("/admin").status_code == 302


def test_the_admin_page_never_echoes_a_key(signed_in, app):
    seed(app)
    from wom.config import Config
    settings = Config()
    settings["anthropic_api_key"] = "sk-ant-secret-value"
    settings.save()
    assert "sk-ant-secret-value" not in signed_in.get("/admin").get_data(as_text=True)


def test_a_stored_key_can_be_cleared_but_not_by_an_empty_box(signed_in, app):
    """A password box shows nothing, so leaving it empty has to mean "keep
    what is there" - which leaves the tick as the only way to say "drop it"."""
    from wom.config import Config
    seed(app)
    settings = Config()
    settings["api_key"] = "a-key-wise-old-man-refuses"
    settings.save()

    form = {"usernames": "zezima", "summary_model": "claude-sonnet-5"}
    signed_in.post("/admin/settings", data=form)
    assert Config().get("api_key") == "a-key-wise-old-man-refuses", "blank kept it"

    signed_in.post("/admin/settings", data=dict(form, clear_api_key="on"))
    assert Config().get("api_key") == "", "the tick cleared it"


def test_the_time_zone_is_a_setting_and_is_checked_before_it_is_stored(signed_in, app):
    """A zone this machine cannot resolve would quietly move every day
    boundary to UTC, which is a strange way to learn you typed it wrong."""
    from wom import periods, scheduler
    from wom.config import Config
    seed(app)
    form = {"usernames": "zezima", "summary_model": "claude-sonnet-5"}
    was = Config().get("timezone")
    try:
        signed_in.post("/admin/settings", data=dict(form, timezone="Australia/Perth"))
        assert Config().get("timezone") == "Australia/Perth"
        assert scheduler.zone().key == "Australia/Perth", "and takes effect at once"

        page = signed_in.post("/admin/settings",
                              data=dict(form, timezone="Mars/Olympus_Mons"),
                              follow_redirects=True)
        assert "not a time zone" in page.get_data(as_text=True)
        assert Config().get("timezone") == "Australia/Perth", "the good one stands"

        # And the day boundaries follow it: Perth is far enough east that its
        # midnight is the previous afternoon in UTC.
        from datetime import datetime, timezone as utc
        window = periods.latest_window("day",
                                       datetime(2026, 9, 1, 12, tzinfo=utc.utc))
        assert window.start.utcoffset().total_seconds() == 8 * 3600
    finally:
        # One settings file for the whole run, and one cached zone behind it.
        settings = Config()
        settings["timezone"] = was
        settings.save()
        scheduler.forget_zone()


def test_admin_disappears_entirely_without_a_password(monkeypatch, tmp_path):
    """Fail closed: no password must mean no routes, not open ones."""
    from wom.db import Database
    from wom.web import app as web_app

    monkeypatch.delenv("WOM_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(web_app, "Database", lambda _p: Database(str(tmp_path / "x.db")))
    application = web_app.create_app()
    assert application.config["ADMIN"] is False
    with application.test_client() as bare:
        assert bare.get("/admin").status_code == 404
        assert bare.post("/admin/run/update").status_code == 404
        assert bare.get("/").status_code == 200


# -- serving files --------------------------------------------------------

def test_icons_cannot_escape_their_directory(client):
    """A backslash survives routing on Windows, so this once served any .png."""
    for path in ("/icon/skill/..%5C..%5C..%5Csecret.png",
                 "/icon/skill/..%2F..%2Fsecret.png",
                 "/icon/nonsense/attack.png"):
        assert client.get(path).status_code == 404, path


def test_a_real_icon_is_still_served(client):
    assert client.get("/icon/skill/attack.png").status_code == 200


# -- headers --------------------------------------------------------------

def test_responses_are_hardened(client):
    headers = client.get("/").headers
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in headers["Content-Security-Policy"].split(
        "script-src")[1].split(";")[0], "inline script must stay forbidden"


# -- exporting ------------------------------------------------------------

def test_export_filters_by_player_kind_and_date(client, app):
    seed(app)
    rows = client.get("/export.csv?kind=skill").get_data(as_text=True).splitlines()
    assert rows[0].startswith("captured_at,player")
    assert all(",skill," in r for r in rows[1:])

    windowed = client.get(
        "/export.csv?from=2026-08-30").get_data(as_text=True).splitlines()[1:]
    assert all(r.split(",")[0] >= "2026-08-30" for r in windowed)


def test_an_unparseable_date_is_refused_not_ignored(client, app):
    """Ignoring it exported the whole history while looking filtered."""
    seed(app)
    response = client.get("/export.csv?from=31/08/2026")
    assert response.status_code == 400
    assert b"not a date" in response.data


def test_export_dates_follow_the_viewers_day(client, app):
    seed(app)
    utc = client.get("/export.csv?to=2026-08-30&tzoffset=0")
    east = client.get("/export.csv?to=2026-08-30&tzoffset=-240")
    assert utc.status_code == east.status_code == 200
    # Same named day, four more hours of it for a viewer west of Greenwich.
    assert len(east.get_data()) >= len(utc.get_data())


def test_export_json_is_valid_and_marks_unranked_as_null(client, app):
    database = seed(app)
    database.save_snapshot(1, snapshot("2026-09-01T12:00:00.000Z",
                                       bosses={"vorkath": -1}))
    rows = json.loads(client.get("/export.json?kind=boss").get_data())
    assert any(r["rank"] is None or r["value"] is None for r in rows)


def test_a_spreadsheet_formula_in_a_name_is_defused(app):
    from wom.web.exporting import safe_cell
    assert safe_cell("=cmd|calc") == "'=cmd|calc"
    assert safe_cell("Zezima") == "Zezima"


def test_every_described_chart_has_a_builder():
    """Describing a chart and forgetting to build it used to be silent."""
    from wom import catalog
    import wom.web.data  # noqa: F401  - importing is what attaches them

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

    import pytest
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


# -- the table on the Data page -------------------------------------------

def test_the_table_carries_every_metric_with_its_movement(client, app):
    """The page answers what the export used to be the only way to ask."""
    seed(app)
    body = client.get("/api/table?period=Week").get_json()
    rows = {(r["metric"], r["kind"]): r for r in body["rows"]}
    assert rows[("attack", "skill")]["value"] == 5000
    assert rows[("attack", "skill")]["gained"] == 4000
    assert rows[("zulrah", "boss")]["gained"] == 40
    assert body["span"]["label"] == "Week"


def test_the_table_honours_the_player_ticks(client, app):
    seed(app)
    body = client.get("/api/table?period=Week&picked=1").get_json()
    assert "empty" in body and not body["rows"]


def test_the_table_carries_the_colour_the_charts_use(client, app):
    """The swatch beside a name has to be the one that name is drawn in."""
    seed(app)
    row = client.get("/api/table?period=Week").get_json()["rows"][0]
    standing = client.get("/api/chart/standings?period=Week").get_json()["rows"][0]
    assert row["color"] == standing["color"]


def test_table_dates_snap_to_the_period_when_none_are_given(client, app):
    """The inputs have to show the window in force, in the viewer's days."""
    seed(app)
    span = client.get("/api/table?period=Week&tzoffset=0").get_json()["span"]
    assert span["from"] and span["to"], span
    assert span["from"] < span["to"]
    assert span["custom"] is False, "a preset is not a custom range"


def test_dates_override_the_period_and_close_the_window(client, app):
    """A range that ended in the past must not report its gains against
    today's totals: 'value' is where they stood at the end of the range."""
    database = seed(app)
    database.save_snapshot(1, snapshot("2026-08-28T12:00:00.000Z",
                                       skills={"attack": (3000, 50)},
                                       bosses={"zulrah": 30}))
    body = client.get("/api/table?period=Week&from=2026-08-24&to=2026-08-28"
                      "&tzoffset=0").get_json()
    row = [r for r in body["rows"] if r["metric"] == "attack"][0]
    assert row["value"] == 3000, "the reading inside the window, not the newest"
    assert row["gained"] == 2000, "measured from the 25th, not from today"
    assert body["span"]["from"] == "2026-08-24"
    assert body["span"]["to"] == "2026-08-28"
    assert body["span"]["custom"] is True


def test_a_typo_in_a_table_date_is_refused_not_ignored(client, app):
    """Ignoring it would widen the window while looking narrowed."""
    seed(app)
    response = client.get("/api/table?from=24/08/2026")
    assert response.status_code == 400


def test_the_table_filters_are_distinct_and_kind_is_always_one(client, app):
    """Kind, metric and the dates each get their own control, and kind has no
    "All": 666 rows of everything at once is not a view anybody asked for."""
    seed(app)
    body = client.get("/export").get_data(as_text=True)
    for control in ('id="kind"', 'id="metric"',
                    'id="from"', 'id="to"', 'id="unlock"'):
        assert control in body, control
    assert 'id="q"' not in body, "the free-text search is gone"
    assert 'id="moved"' not in body, "the moved-only tick is gone"
    # Who is on the page is the sidebar's job on every other page too, and a
    # second control here could disagree with the chart below the table.
    assert 'id="who-filter"' not in body, "the player dropdown is gone"
    assert 'name="player"' in body, "the sidebar ticks are the player control"
    kind = body[body.index('id="kind"'):body.index("</select>", body.index('id="kind"'))]
    assert 'value=""' not in kind, "kind must always name one kind"
    assert kind.index('value="skill"') < kind.index('value="boss"'),         "skills first, so the page opens on them"


def test_history_plots_one_line_per_player_for_one_metric(client, app):
    seed(app)
    body = client.get("/api/history?period=Week&kind=skill&metric=attack").get_json()
    assert body["type"] == "trend"
    assert [s["name"] for s in body["series"]] == ["Zezima"]
    assert len(body["series"][0]["points"]) >= 2


def test_history_follows_the_window_it_is_given(client, app):
    seed(app)
    body = client.get("/api/history?kind=skill&metric=attack"
                      "&from=2026-08-24&to=2026-08-26&tzoffset=0").get_json()
    assert body["until"] is not None, "a closed window has to stop the axis"
    assert body["until"] > body["since"]


def test_history_refuses_a_metric_name_it_could_not_have_stored(client, app):
    """The name reaches an icon lookup on the page; nothing else may."""
    seed(app)
    assert client.get("/api/history?kind=skill&metric=../../etc").status_code == 404
    assert client.get("/api/history?kind=nonsense&metric=attack").status_code == 404


def test_history_says_so_rather_than_drawing_nothing(client, app):
    seed(app)
    body = client.get("/api/history?kind=boss&metric=vorkath").get_json()
    assert "empty" in body


def test_the_data_page_offers_the_export_behind_a_button(client, app):
    seed(app)
    body = client.get("/export").get_data(as_text=True)
    assert 'id="open-export"' in body
    assert "<dialog" in body
    # The form still posts to the same places; only its housing moved.
    assert 'formaction="/export.csv"' in body
    assert 'formaction="/export.json"' in body


# -- one sidebar, five tabs -----------------------------------------------

def test_every_tab_but_the_calendar_ones_carries_the_window_controls(client, app):
    seed(app)
    for path in ("/", "/milestones", "/players", "/export"):
        body = client.get(path).get_data(as_text=True)
        for control in ('id="period"', 'id="from"', 'id="to"', 'id="all-none"'):
            assert control in body, "{} is missing {}".format(path, control)
    # Recaps and the leaderboard run on calendar windows, so a date range
    # means nothing on either - and a control that cannot work is not shown.
    for path in ("/recaps", "/maxing"):
        body = client.get(path).get_data(as_text=True)
        assert 'id="all-none"' in body, "{}: the ticks still apply".format(path)
        assert 'id="from"' not in body, path
        assert 'id="period"' not in body, path


def test_updated_is_when_they_last_moved_not_when_we_last_asked(client, app):
    """We poll every ten minutes, so Wise Old Man's `updatedAt` says "now"
    for an account nobody has logged into in a month."""
    from wom.util import fmt_ago
    database = seed(app)
    database.save_player_details({
        "id": 1, "username": "zezima", "displayName": "Zezima",
        "type": "regular", "updatedAt": "2099-01-01T00:00:00.000Z"})
    # Polled again a day later, with nothing to show for it: same numbers.
    database.save_snapshot(1, snapshot("2026-09-01T12:00:00.000Z",
                                       skills={"attack": (5000, 40)},
                                       bosses={"zulrah": 50}))
    assert database.last_change(1) == "2026-08-31T12:00:00.000Z",         "a reading that changed nothing is not a change"

    body = client.get("/api/players").get_json()
    assert body["rows"][0]["updated"] == fmt_ago("2026-08-31T12:00:00.000Z")


def test_all_time_opens_at_the_first_reading_held(client, app):
    """Not an unbounded window: the gains baseline needs a real start."""
    seed(app)
    span = client.get("/api/table?period=All time&tzoffset=0").get_json()["span"]
    assert span["from"] == "2026-08-25", span
    assert span["choice"] == "All time"
    assert span["custom"] is False, "a named window is not a custom range"


def test_today_opens_at_the_viewer_s_midnight_not_a_day_ago(client, app):
    """"Day" is the last twenty-four hours and so reaches back into
    yesterday; "Today" is the calendar day the viewer is standing in."""
    from datetime import datetime, timedelta, timezone
    seed(app)
    # An offset far enough east that its "today" is not UTC's, so a window
    # computed in UTC would fail this rather than passing by coincidence.
    span = client.get("/api/table?period=Today&tzoffset=600").get_json()["span"]
    theirs = (datetime.now(timezone.utc) + timedelta(minutes=600)).strftime("%Y-%m-%d")
    assert span["from"] == theirs == span["to"], span
    assert span["choice"] == "Today", "a preset, not a custom range"
    assert span["custom"] is False


def test_all_time_is_not_mangled_into_a_period(client, app):
    """"All time".title() is "All Time", which used to fall back to Week."""
    seed(app)
    body = client.get("/api/table?period=All+time&tzoffset=0").get_json()
    assert body["span"]["label"] == "All time"


def test_a_custom_range_names_itself_on_the_player_endpoint(client, app):
    """The figures are measured over whatever span was asked for, and the
    answer says which - a range reporting a period's name would be claiming
    to have measured something else."""
    seed(app)
    weekly = client.get("/api/player/zezima?period=Week").get_json()
    assert weekly["period"] == "Week"

    custom = client.get("/api/player/zezima?period=Custom&from=2026-08-01"
                        "&to=2026-08-20&tzoffset=0").get_json()
    assert custom["period"] == "01 Aug 2026 to 20 Aug 2026"


def test_the_milestone_feed_closes_at_the_end_of_the_window(client, app):
    database = seed(app)
    database.save_achievements(1, [
        {"name": "99 Attack", "metric": "attack", "measure": "levels",
         "threshold": 13034431, "createdAt": "2026-08-26T00:00:00.000Z",
         "accuracy": 1000},
        {"name": "99 Strength", "metric": "strength", "measure": "levels",
         "threshold": 13034431, "createdAt": "2026-08-30T00:00:00.000Z",
         "accuracy": 1000},
    ])
    body = client.get("/api/milestones?period=Custom&from=2026-08-25"
                      "&to=2026-08-27&tzoffset=0").get_json()
    assert [row["name"] for row in body["feed"]] == ["99 Attack"], \
        "the 30th falls outside the window and must not be listed"


def test_the_roster_can_be_refetched_without_a_reload(client, app):
    seed(app)
    body = client.get("/api/players?period=Week").get_json()
    assert [row["name"] for row in body["rows"]] == ["Zezima"]
    assert body["span"]["choice"] == "Week"


# -- what the review found ------------------------------------------------

def test_a_bad_date_is_refused_on_a_page_not_a_500(client, app):
    """The window is resolved before a page renders too, and a typo in the
    query string used to reach Flask as an unhandled exception."""
    seed(app)
    for path in ("/", "/players", "/export", "/milestones", "/recaps",
                 "/maxing"):
        response = client.get(path + "?from=notadate")
        assert response.status_code == 400, path
        assert b"not a date" in response.data


def test_a_backwards_range_is_refused_rather_than_read_as_quiet(client, app):
    """Every gain clamps to zero across an inverted window, so it read as a
    period where nobody did anything."""
    seed(app)
    response = client.get("/api/table?from=2026-08-30&to=2026-08-01&tzoffset=0")
    assert response.status_code == 400
    assert b"comes after" in response.data
    # A single day is a window, not a mistake.
    same = client.get("/api/table?from=2026-08-25&to=2026-08-25&tzoffset=0")
    assert same.status_code == 200


def test_unticking_everyone_survives_a_change_of_tab(client, app):
    """The page and the JSON behind it used to disagree about the same URL:
    one showed the whole roster back, re-ticked, the other showed nobody."""
    database = seed(app)
    database.save_player_details({"id": 2, "username": "other",
                                  "displayName": "Other", "type": "regular"})
    from wom.config import Config
    settings = Config()
    settings["usernames"] = ["Zezima", "Other"]
    settings.save()

    empty = "?picked=1&period=Week"
    page = client.get("/players" + empty).get_data(as_text=True)
    assert "checked" not in page, "no box may come back ticked"
    assert 'class="player-row"' not in page, "and no player may come back listed"
    assert client.get("/api/players" + empty).get_json()["rows"] == []


def test_recaps_ticks_reload_the_page_rather_than_doing_nothing(client, app):
    """It has no JSON endpoint, so its ticks have to reload or they are inert."""
    seed(app)
    assert 'data-reload="1"' in client.get("/recaps").get_data(as_text=True)
    # Maxing has nothing to reload: the calendar and the standings ignore the
    # ticks entirely, and the one thing that honours them - the chart - fetches
    # its own JSON.
    for path in ("/", "/players", "/export", "/milestones", "/maxing"):
        assert 'data-reload="0"' in client.get(path).get_data(as_text=True), path


def test_the_sidebar_dates_are_not_submitted_by_the_no_script_form(client, app):
    """They are pre-filled with whatever the preset resolved to, and the
    server honours any date it is given - so Apply turned Week into Custom."""
    seed(app)
    body = client.get("/").get_data(as_text=True)
    dates = body[body.index('id="dates"'):body.index("</div>", body.index('id="dates"'))]
    assert 'name="from"' not in dates and 'name="to"' not in dates
    # The controls that do work without JavaScript keep their names.
    assert 'id="period" name="period"' in body
    assert 'name="player"' in body


def test_every_data_endpoint_refuses_to_be_cached(client, app):
    """An update lands while the page is open; the reader has to see it.

    Both ways out of every endpoint, not just the one with rows in it. Five
    early returns handed back a bare jsonify() with no header on it, so a
    player's own figures were cacheable on their normal path and three
    endpoints were cacheable whenever nobody was ticked - which is when a
    reader is most likely to tick somebody and ask again.
    """
    seed(app)
    populated = ("/api/chart/standings", "/api/table", "/api/players",
                 "/api/milestones", "/api/history?kind=skill&metric=attack",
                 "/api/player/zezima", "/api/maxing/player/zezima",
                 "/api/maxing/trend")
    empty = ("/api/chart/standings?picked=1", "/api/table?picked=1",
             "/api/maxing/trend?picked=1",
             "/api/history?kind=skill&metric=attack&picked=1",
             "/api/history?kind=skill&metric=nosuchskill_here")
    for path in populated + empty:
        response = client.get(path)
        assert response.headers.get("Cache-Control") == "no-cache", path


def test_the_sidebar_dates_follow_the_viewers_cookie_on_a_first_paint(client, app):
    """A page is rendered before any script on it runs, so without the cookie
    the dates are worked out in UTC and can read a day ahead."""
    seed(app)

    def to_date(body):
        at = body.index('id="to"')
        return body[at:body.index(">", at)].split('value="')[1].rstrip('"')

    # Twelve hours west and thirteen east: twenty-five apart, so the two
    # cannot land on the same local day whatever the clock says right now.
    client.set_cookie("wom_tz", "-720")
    west = to_date(client.get("/").get_data(as_text=True))
    client.set_cookie("wom_tz", "780")
    east = to_date(client.get("/").get_data(as_text=True))
    assert west != east, "the cookie has to decide which day it is"

    # An explicit tzoffset still wins: the cookie is only the fallback.
    assert to_date(client.get("/?tzoffset=-720").get_data(as_text=True)) == west


# -- the winner calendar --------------------------------------------------

def _calendar_seed(app, polled=True):
    """Two accounts, one of which is only ever seen mid-afternoon.

    `polled` records an update run for each day, which is the evidence that
    an account with no reading that day played nothing rather than going
    unwatched. Tests about that rule itself pass False.
    """
    database = app.config["DATABASE"]
    for pid, name in ((1, "Zezima"), (2, "Other")):
        database.save_player_details({"id": pid, "username": name.lower(),
                                      "displayName": name, "type": "regular"})
    # Zezima is read four times a day, every day.
    for day, xp in (("2026-08-28", 1000), ("2026-08-29", 2000),
                    ("2026-08-30", 3000), ("2026-08-31", 3100)):
        for hour in ("02", "23"):
            database.save_snapshot(1, snapshot(
                day + "T" + hour + ":00:00.000Z",
                skills={"attack": (xp + (50 if hour == "23" else 0), 50)}))
    # Other is seen once in July and then not again until the 30th.
    database.save_snapshot(2, snapshot("2026-07-02T12:00:00.000Z",
                                       skills={"attack": (500, 40)}))
    for hour, xp in (("21", 9000), ("23", 9500)):
        database.save_snapshot(2, snapshot("2026-08-30T" + hour + ":00:00.000Z",
                                           skills={"attack": (xp, 60)}))
    if polled:
        _polled(database, 2, ["2026-08-{:02d}".format(day) for day in range(1, 32)])
    return database


def _polled(database, players, days):
    """Say the tracker looked at everyone on each of these days."""
    for day in days:
        run = database.start_run("test", roster=players)
        database.finish_run(run, ok_count=players, fail_count=0)
        database.connect().execute(
            "UPDATE runs SET started_at=? WHERE id=?",
            (day + "T12:00:00.000Z", run))
    database.connect().commit()


def test_a_long_gap_is_not_counted_as_one_days_work(app):
    """Measured from the far side of a seven-week gap, an account that came
    back on the 30th would have all seven weeks folded into that day."""
    from wom import winners
    from datetime import datetime, timezone
    database = _calendar_seed(app)
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
    from wom import winners
    from datetime import datetime, timezone
    database = _calendar_seed(app)
    players = database.players()
    start, end = winners.month_range(
        datetime(2026, 8, 15, tzinfo=timezone.utc), back=0)
    gains = winners.gains_by_day(database, players, start, end)
    # Other has no reading at all on the 29th, but was being tracked by then.
    assert "other" in gains["2026-08-29"]["measured"]
    assert "other" not in gains["2026-08-29"]["scores"], "tracked, and gained nothing"


def test_the_round_up_overrules_the_figures_only_for_the_whole_group(app):
    from wom import periods, winners
    from datetime import datetime, timezone
    database = _calendar_seed(app)
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
    _calendar_seed(app)
    body = client.get("/maxing").get_data(as_text=True)
    assert "Maxing Leaderboard" in body
    assert body.count('class="month"') == 2, "last month and this one"


def test_adding_a_player_does_not_blank_the_days_before_they_arrived(app):
    """A run is evidence about the day it ran. Judged against today's roster,
    a seventh account would retire every day the other six were watched."""
    from datetime import datetime, timezone

    from wom import winners
    database = _calendar_seed(app)
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

    database = _calendar_seed(app)
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
    database = _calendar_seed(app)
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
    database = _calendar_seed(app)
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
    from wom import winners
    from datetime import datetime, timezone
    database = _calendar_seed(app)
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
    from wom import winners
    from datetime import datetime, timezone
    database = _calendar_seed(app)
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
    from wom import winners
    from datetime import datetime, timezone
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
    _polled(database, 2, ["2026-08-20"])

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
    from wom import winners
    from datetime import datetime, timezone
    database = app.config["DATABASE"]
    database.save_player_details({"id": 1, "username": "maxed",
                                  "displayName": "Maxed", "type": "regular"})
    edge = winners.NINETY_NINE
    database.save_snapshot(1, snapshot("2026-08-19T23:00:00.000Z",
                                       skills={"attack": (edge * 2, 99)}))
    database.save_snapshot(1, snapshot("2026-08-20T23:00:00.000Z",
                                       skills={"attack": (edge * 3, 99)}))
    _polled(database, 1, ["2026-08-20"])
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
    from wom import winners
    from datetime import datetime, timezone
    database = _calendar_seed(app, polled=False)
    players = database.players()
    start, end = winners.month_range(
        datetime(2026, 8, 15, tzinfo=timezone.utc), back=0)

    unwatched = winners.daily_winners(database, players, start, end)
    assert unwatched["2026-08-28"]["winner"] is None
    assert unwatched["2026-08-28"]["reason"] == "the tracker was not watching that day"
    assert winners.month_winner(database, players, start, end) is None

    # A run that came back with a result for every player is the evidence.
    _polled(database, len(players), ["2026-08-28"])
    watched = winners.daily_winners(database, players, start, end)
    assert watched["2026-08-28"]["winner"] == "zezima"
    # And only that day: the others still have nobody vouching for them.
    assert watched["2026-08-29"]["reason"] == "the tracker was not watching that day"


def test_the_tab_icon_is_linked_and_survives_its_file_being_absent(client, app):
    """The link is always in the head; the route 404s rather than 500s when
    nobody has put a favicon.png in assets yet."""
    seed(app)
    head = client.get("/").get_data(as_text=True)
    assert 'rel="icon" type="image/png" href="/assets/favicon.png"' in head
    import os
    from wom.web.pages import FAVICON
    expected = 200 if os.path.exists(FAVICON) else 404
    assert client.get("/favicon.ico").status_code == expected


def test_today_follows_the_grid_in_a_card_of_its_own(app, client):
    """The squares are finished days; the table is the one still running.

    Two questions, so two cards - and this order, because the running day
    only makes sense once you have seen what a finished one looks like.
    """
    _calendar_seed(app)
    body = client.get("/maxing").get_data(as_text=True)
    assert body.index('class="months"') < body.index("Today so far")
    assert body.index("Today so far") < body.index("Experience toward 99 today")
    # Its own card, not a panel inside the calendar's.
    assert 'class="card standing"' in body


def test_today_is_ordered_by_the_same_rule_as_the_squares(app):
    from wom import winners
    from wom.web.today import standings
    from datetime import datetime, timedelta
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
    from wom import winners
    from wom.web.today import standings
    from wom.web.views import winner_calendar
    from datetime import datetime, timedelta
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
    _polled(database, 1, [yesterday, today])

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
    from wom import winners
    from wom.web.today import standings
    from datetime import datetime
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
    _polled(database, 2, ["2026-08-10", "2026-08-11"])

    players = database.players()
    palette = {p["username"]: "#fff" for p in players}
    rows = {row["name"]: row for row in
            standings(database, players, palette,
                      when=datetime(2026, 8, 15))["rows"]}
    assert rows["Climber"]["nine_wins"] == 1 and rows["Climber"]["xp_wins"] == 0
    assert rows["Grinder"]["nine_wins"] == 0 and rows["Grinder"]["xp_wins"] == 1


# -- the prompts page ------------------------------------------------------

def _prompt_files():
    """Every prompt file currently on disk, by name."""
    from wom.config import DATA_DIR
    return {name for name in os.listdir(DATA_DIR) if name.endswith(".txt")}


def test_every_prompt_that_drives_a_round_up_can_be_edited(signed_in, app):
    """A period override was reachable only over SSH.

    Per-period files are the supported way to ask a yearly note for something
    a daily one should not say. The page offered the two base prompts and
    nothing else, so the prompts actually driving the quarterly and yearly
    notes could not be read from it, let alone changed.
    """
    from wom import summaries as core
    path = core.period_prompt_path("year", kind="player")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("Say something only a year would want said.\n")
    try:
        body = signed_in.get("/admin/prompts").get_data(as_text=True)
        assert "Say something only a year would want said." in body
    finally:
        os.remove(path)


def test_a_group_prompt_is_offered_only_for_the_windows_it_writes(signed_in, app):
    """The group recap covers the day and the month, so a group prompt for a
    quarter would be a file nothing ever loads."""
    from wom import summaries as core
    body = signed_in.get("/admin/prompts").get_data(as_text=True)
    assert 'value="group:month"' in body
    assert 'value="group:quarter"' not in body
    assert 'value="player:quarter"' in body, "a player's own notes still cover it"

    signed_in.post("/admin/prompts", data={"seed": "1", "add": "group:year"})
    assert not os.path.exists(core.period_prompt_path("year", kind="group")), (
        "and asking for one anyway is refused")


def test_saving_an_override_writes_that_period_not_the_base(signed_in, app):
    """prompt_path answers "which file would be used", which falls back to the
    base - so saving through it would silently overwrite the base prompt."""
    from wom import summaries as core
    base = core.base_prompt_path(kind="player")
    with open(base, "r", encoding="utf-8") as handle:
        before = handle.read()
    override = core.period_prompt_path("quarter", kind="player")
    try:
        signed_in.post("/admin/prompts", data={
            "kind": "player", "period": "quarter", "text": "Only for quarters."})
        assert os.path.exists(override), "the override was written"
        with open(override, "r", encoding="utf-8") as handle:
            assert handle.read().strip() == "Only for quarters."
        with open(base, "r", encoding="utf-8") as handle:
            assert handle.read() == before, "and the base was left alone"
    finally:
        if os.path.exists(override):
            os.remove(override)


def test_an_override_can_be_seeded_and_then_removed(signed_in, app):
    from wom import summaries as core
    override = core.period_prompt_path("day", kind="group")
    assert not os.path.exists(override)
    try:
        signed_in.post("/admin/prompts", data={"seed": "1", "add": "group:day"})
        assert os.path.exists(override), "seeded from the base prompt"
        with open(override, "r", encoding="utf-8") as handle:
            assert handle.read().strip(), "and not left empty"

        signed_in.post("/admin/prompts", data={
            "kind": "group", "period": "day", "delete": "1", "text": "ignored"})
        assert not os.path.exists(override), "removed, falling back to the base"
    finally:
        if os.path.exists(override):
            os.remove(override)


def test_a_prompt_cannot_be_saved_empty(signed_in, app):
    """An empty system prompt is not an edit, it is a broken round-up."""
    from wom import summaries as core
    base = core.base_prompt_path(kind="player")
    with open(base, "r", encoding="utf-8") as handle:
        before = handle.read()
    signed_in.post("/admin/prompts", data={"kind": "player", "text": "   "})
    with open(base, "r", encoding="utf-8") as handle:
        assert handle.read() == before


def test_a_made_up_period_is_refused(signed_in, app):
    """The period names a file path, so nothing but a known period reaches it."""
    before = _prompt_files()
    signed_in.post("/admin/prompts", data={
        "kind": "player", "period": "../../etc/passwd", "text": "no"})
    assert _prompt_files() == before


def test_the_effort_setting_is_on_the_page_and_is_checked(signed_in, app):
    """It moves the bill on every round-up, and was reachable only by editing
    config.json on the volume - which for a hosted deployment is not at all."""
    from wom.config import Config
    seed(app)
    form = {"usernames": "zezima", "summary_model": "claude-sonnet-5"}
    assert 'name="summary_effort"' in signed_in.get("/admin").get_data(as_text=True)

    signed_in.post("/admin/settings", data=dict(form, summary_effort="high"))
    assert Config().get("summary_effort") == "high"

    signed_in.post("/admin/settings", data=dict(form, summary_effort="colossal"))
    assert Config().get("summary_effort") == "low", "an unknown effort falls back"


def test_the_admin_password_is_never_stored_as_an_api_key(signed_in, app):
    """Browsers ignore autocomplete="off" on a password field on purpose.

    So a password manager fills the admin password into the API key boxes,
    and saving stored it as the key - which Wise Old Man then answers 403 to
    on every request. The page keeps looking like the key was never cleared,
    because something keeps putting one back.
    """
    from wom.config import Config
    seed(app)
    form = {"usernames": "zezima", "summary_model": "claude-sonnet-5"}

    page = signed_in.post("/admin/settings",
                          data=dict(form, api_key="test-password"),
                          follow_redirects=True)
    assert Config().get("api_key") == "", "the autofilled password is refused"
    assert "your browser most likely filled it in" in page.get_data(as_text=True)

    # Refused means "nothing was stored", so whatever was there is untouched.
    was = Config().get("anthropic_api_key")
    signed_in.post("/admin/settings",
                   data=dict(form, anthropic_api_key="test-password"))
    assert Config().get("anthropic_api_key") == was

    # A key that is not the password still stores, or this guard would be
    # worse than the bug.
    signed_in.post("/admin/settings", data=dict(form, api_key="a-real-looking-key"))
    assert Config().get("api_key") == "a-real-looking-key"
    signed_in.post("/admin/settings", data=dict(form, clear_api_key="on"))
    assert Config().get("api_key") == ""


def test_the_key_boxes_ask_not_to_be_autofilled(signed_in, app):
    body = signed_in.get("/admin").get_data(as_text=True)
    assert 'autocomplete="off"' not in body, "which browsers ignore on passwords"
    assert body.count('autocomplete="new-password"') == 2


# -- the recap feed and its leaderboard verdict ---------------------------

def test_a_recap_carries_what_the_leaderboard_decided(app, client):
    """The recap is the calendar's feed, so each entry says what the calendar
    said. The prose judges on its own reading and the squares judge on the
    rule; where they differ, the squares are what the page was coloured by."""
    from datetime import datetime, timezone

    from wom import periods
    from wom.web.views import recap_feed

    database = _calendar_seed(app)
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

    database = _calendar_seed(app)
    window = periods.latest_window("month", datetime(2026, 9, 15, 12,
                                                     tzinfo=timezone.utc))
    database.save_group_summary(window, "A month.", "hash")
    players = database.players()
    tree = recap_tree(database, players,
                      {p["username"]: "#fff" for p in players})
    group = [branch for branch in tree if branch["username"] == "__group__"][0]
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
    _calendar_seed(app)
    everyone = client.get("/maxing").get_data(as_text=True)
    narrowed = client.get("/maxing?picked=1&player=zezima").get_data(as_text=True)

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
    _calendar_seed(app)
    body = client.get("/maxing?picked=1").get_data(as_text=True)
    assert "No players are ticked." not in body
    assert 'class="today-row' in body, "every account is still ranked"


def test_a_recap_verdict_is_the_leaderboards_whatever_is_ticked(app, client):
    """The chip quotes the calendar, so it has to be asked the calendar's
    question - not a narrower one that answers differently."""
    from datetime import datetime, timezone

    from wom import periods
    from wom.web.views import recap_feed

    database = _calendar_seed(app)
    window = periods.latest_window("day", datetime(2026, 8, 31, 12,
                                                   tzinfo=timezone.utc))
    database.save_group_summary(window, "A day.", "hash")

    everyone = database.players()
    palette = {p["username"]: "#123456" for p in everyone}
    one = [p for p in everyone if p["username"] == "zezima"]
    assert (recap_feed(database, everyone, palette)[0]["winner"]
            == recap_feed(database, one, palette)[0]["winner"])


# -- one day, one answer --------------------------------------------------

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


def test_the_milestones_page_offers_a_filter_for_each_kind(client, app):
    seed(app)
    page = client.get("/milestones").get_data(as_text=True)
    for label in ("Milestones", "Collection log", "Quests", "Diaries",
                  "Combat tasks"):
        assert label in page, label
    assert 'id="types"' in page


def test_every_feed_row_says_what_kind_it_is(client, app):
    """The filter hides by that attribute, so a row without one cannot be
    filtered - and would sit there ignoring every tick box."""
    from wom import gameplay
    database = seed(app)
    database.save_achievements(1, [{
        "name": "99 Attack", "metric": "attack", "measure": "experience",
        "threshold": 13034431, "createdAt": "2026-08-30T10:00:00.000Z",
        "accuracy": 3600000}])
    gameplay.store(database, "zezima", "quest", "2026-08-30T21:15:00.000000Z",
                   {"type": "QUEST", "extra": {"questName": "Dragon Slayer I",
                                               "completedQuests": 22,
                                               "totalQuests": 156}})
    page = client.get("/milestones?period=Year").get_data(as_text=True)
    rows = page.split('id="feed"')[1].split("</tbody>")[0]
    assert rows.count("<tr") == rows.count("data-category="), (
        "every rendered row needs a kind for the filter to act on")
    assert 'data-category="quest"' in rows
    assert 'data-category="milestone"' in rows


def test_the_json_feed_carries_the_kind_too(client, app):
    """The page is redrawn from this without reloading, so it has to carry
    everything the filter needs."""
    from wom import gameplay
    database = seed(app)
    gameplay.store(database, "zezima", "combat_task", "2026-08-30T21:15:00.000000Z",
                   {"type": "COMBAT_ACHIEVEMENT",
                    "extra": {"task": "Peach Conjurer", "tier": "GRANDMASTER",
                              "taskPoints": 6}})
    feed = client.get("/api/milestones?period=Year").get_json()["feed"]
    row = [r for r in feed if r["name"] == "Peach Conjurer"][0]
    assert row["category"] == "combat_task"
    assert row["detail"] == "Grandmaster"
