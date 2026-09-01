"""The HTTP surface: what is public, what is not, and what is refused."""

import json

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
    for path in ("/", "/milestones", "/summaries", "/players", "/export"):
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
    import wom.web.data  # noqa: F401  - importing attaches the builders

    missing = [s.key for s in catalog.SUMMARY_CHARTS if s.build is None]
    assert missing == [], "described but never built: {}".format(missing)


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
    body = client.get("/summaries").get_data(as_text=True)
    assert "Everyone had a quiet day." in body
    assert "round-up" in body


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


def test_round_ups_lead_with_one_of_each_length(client, app):
    from wom import periods
    database = seed(app)
    for key in periods.SUMMARY_PERIODS:
        database.save_group_summary(periods.latest_window(key),
                                    "A {} round-up.".format(key), "hash")
    body = client.get("/summaries").get_data(as_text=True)
    for key in periods.SUMMARY_PERIODS:
        assert "A {} round-up.".format(key) in body, key


def test_a_player_note_is_named_by_its_own_window(client, app):
    """The page's period and the note's window are different spans."""
    from wom import periods
    database = seed(app)
    window = periods.latest_window("day")
    database.save_summary(1, window, "A note about yesterday.", "hash")
    body = client.get("/api/player/zezima?period=Day").get_json()
    assert body["period"] == "Day", "the figures are the rolling last 24 hours"
    assert body["note"]["label"] == window.label, "the note names its own window"
    assert body["note"]["label"] != body["period"]


def test_a_player_with_no_note_for_that_period_says_nothing(client, app):
    seed(app)
    assert client.get("/api/player/zezima?period=Year").get_json()["note"] is None


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

def test_every_tab_but_round_ups_carries_the_same_window_controls(client, app):
    seed(app)
    for path in ("/", "/milestones", "/players", "/export"):
        body = client.get(path).get_data(as_text=True)
        for control in ('id="period"', 'id="from"', 'id="to"', 'id="all-none"'):
            assert control in body, "{} is missing {}".format(path, control)
    # Round-ups are written per calendar window, so a date range means nothing
    # there - and a control that cannot work should not be shown.
    round_ups = client.get("/summaries").get_data(as_text=True)
    assert 'id="all-none"' in round_ups, "the ticks still apply"
    assert 'id="from"' not in round_ups
    assert 'id="period"' not in round_ups


def test_all_time_opens_at_the_first_reading_held(client, app):
    """Not an unbounded window: the gains baseline needs a real start."""
    seed(app)
    span = client.get("/api/table?period=All time&tzoffset=0").get_json()["span"]
    assert span["from"] == "2026-08-25", span
    assert span["choice"] == "All time"
    assert span["custom"] is False, "a named window is not a custom range"


def test_all_time_is_not_mangled_into_a_period(client, app):
    """"All time".title() is "All Time", which used to fall back to Week."""
    seed(app)
    body = client.get("/api/table?period=All+time&tzoffset=0").get_json()
    assert body["span"]["label"] == "All time"


def test_a_custom_range_names_no_note(client, app):
    """A note is filed under a calendar window; a range names none, and one
    from some other span must not be passed off as this one's."""
    database = seed(app)
    from wom import periods
    window = periods.latest_window("week")
    database.save_summary(1, window, "A week's work.", "hash")

    weekly = client.get("/api/player/zezima?period=Week").get_json()
    assert weekly["note"], "the weekly note is offered for the weekly window"

    custom = client.get("/api/player/zezima?period=Custom&from=2026-08-01"
                        "&to=2026-08-20&tzoffset=0").get_json()
    assert custom["note"] is None
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
    for path in ("/", "/players", "/export", "/milestones", "/summaries"):
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


def test_round_ups_ticks_reload_the_page_rather_than_doing_nothing(client, app):
    """It has no JSON endpoint, so its ticks have to reload or they are inert."""
    seed(app)
    body = client.get("/summaries").get_data(as_text=True)
    assert 'data-reload="1"' in body
    for path in ("/", "/players", "/export", "/milestones"):
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
    """An update lands while the page is open; the reader has to see it."""
    seed(app)
    for path in ("/api/chart/standings", "/api/table", "/api/players",
                 "/api/milestones", "/api/history?kind=skill&metric=attack"):
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
        run = database.start_run("test")
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
    body = client.get("/summaries").get_data(as_text=True)
    assert "Maxing Leaderboard" in body
    assert body.count('class="month"') == 2, "last month and this one"


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


def test_today_stands_beside_the_grid_in_the_same_card(app, client):
    """The squares are finished days; the table is the one still running."""
    _calendar_seed(app)
    body = client.get("/summaries").get_data(as_text=True)
    calendar = body[body.index('class="calendar"'):body.index("Maxing", body.index('class="calendar"') + 10)] \
        if "Maxing" in body[body.index('class="calendar"'):] else body[body.index('class="calendar"'):]
    assert 'class="months"' in calendar
    assert "Today so far" in calendar
    # Both live inside the one card, which is what puts them side by side.
    assert calendar.index('class="months"') < calendar.index("Today so far")


def test_today_is_ordered_by_the_same_rule_as_the_squares(app):
    from wom import winners
    from wom.web.views import winner_calendar
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
    rows = winner_calendar(database, players, palette)["today"]["rows"]
    # One experience point and a 99 outranks nine million spent past one.
    assert [row["name"] for row in rows] == ["Climber", "Maxed"]
    assert rows[0]["nines"] == 1
    assert rows[1]["moved"] is False, "all of it above 99 counts for nothing"


def test_the_day_in_progress_leads_but_has_not_won(app):
    """Leading at four in the afternoon is not a day won, and must not count
    toward the month either."""
    from wom import winners
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
    assert calendar["today"]["rows"][0]["won"] == 0
    square = [d for m in calendar["months"] for d in m["days"]
              if d["winner"] and d["live"]]
    assert len(square) == 1, "one square is live, and it is coloured"
    assert "the day is not over" in square[0]["note"]
