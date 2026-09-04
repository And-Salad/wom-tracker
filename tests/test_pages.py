"""The public pages: what renders, what is refused, what the shell carries."""

import os

from conftest import round_ups, seed, snapshot


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


def test_icons_cannot_escape_their_directory(client):
    """A backslash survives routing on Windows, so this once served any .png."""
    for path in ("/icon/skill/..%5C..%5C..%5Csecret.png",
                 "/icon/skill/..%2F..%2Fsecret.png",
                 "/icon/nonsense/attack.png"):
        assert client.get(path).status_code == 404, path


def test_a_real_icon_is_still_served(client):
    assert client.get("/icon/skill/attack.png").status_code == 200


def test_responses_are_hardened(client):
    headers = client.get("/").headers
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in headers["Content-Security-Policy"].split(
        "script-src")[1].split(";")[0], "inline script must stay forbidden"


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
    assert database.last_change(1) == "2026-08-31T12:00:00.000Z", (
        "a reading that changed nothing is not a change")

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
    opens = body.index('id="dates"')
    dates = body[opens:body.index("</div>", opens)]
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


def test_the_tab_icon_is_linked_and_survives_its_file_being_absent(client, app):
    """The link is always in the head; the route 404s rather than 500s when
    nobody has put a favicon.png in assets yet."""
    seed(app)
    head = client.get("/").get_data(as_text=True)
    assert 'rel="icon" type="image/png" href="/assets/favicon.png"' in head

    from wom.web.pages import FAVICON
    expected = 200 if os.path.exists(FAVICON) else 404
    assert client.get("/favicon.ico").status_code == expected


def test_the_help_page_answers_the_questions_it_is_for(client):
    page = client.get("/help").get_data(as_text=True)
    assert page.count("<h4") >= 5, "one heading per question it answers"
    for phrase in ("Custom Metadata Handler",      # how to set Dink up
                   "ten minutes",                  # how polling works
                   "log out",                      # why numbers arrive late
                   "You do not need it",           # the WOM plugin
                   "Maxing", "Grinding",           # both competitions exist
                   "ninety-nine",                  # how a day is won
                   "average of its days"):         # how a month is won
        assert phrase in page, phrase
    assert 'href="https://wiseoldman.net"' in page


def test_the_help_page_is_linked_from_every_page_with_a_sidebar(client, app):
    seed(app)
    for path in ("/", "/maxing", "/milestones", "/gallery", "/recaps",
                 "/players", "/export"):
        page = client.get(path).get_data(as_text=True)
        assert 'class="side-help"' in page, path
        assert 'href="/help"' in page, path


def test_the_help_box_sits_under_the_sidebar_not_beside_the_page(client):
    """Both live in one column; without the wrapper the box becomes a third
    thing in a flex row and lands next to the charts."""
    page = client.get("/").get_data(as_text=True)
    assert 'class="side"' in page.split('class="rail"')[1].split('class="content"')[0]
    assert page.index('class="side-help"') < page.index('class="content"')


def test_the_help_page_carries_no_sidebar_of_its_own(client):
    """It is not about a selection of players or a window of time."""
    page = client.get("/help").get_data(as_text=True)
    assert 'class="rail"' not in page and 'id="filters"' not in page


def test_the_store_is_loaded_before_anything_that_reads_it(client, app):
    """Load-bearing ordering in a template, which is the kind of thing that
    gets tidied.

    store.js puts WOM.Remember on the page. Every script that restores
    something falls back to a silent no-op when it is not there yet, so
    loading it late does not break anything - it just quietly stops
    remembering, which is the worst way for this to fail.
    """
    seed(app)
    page = client.get("/").get_data(as_text=True)
    assert page.index("store.js") < page.index("sidebar.js")
    for reader in ("overview.js", "chartkit.js"):
        assert page.index("store.js") < page.index(reader), reader


def test_every_page_that_remembers_something_loads_the_store(client, app):
    seed(app)
    for path in ("/", "/maxing", "/grinding", "/recaps", "/milestones",
                 "/gallery", "/export"):
        assert "store.js" in client.get(path).get_data(as_text=True), path


def test_the_recaps_toggle_is_remembered(client, app):
    """It is a choice like every other control on the site."""
    round_ups(app.config["DATABASE"])
    seed(app)
    page = client.get("/recaps").get_data(as_text=True)
    assert "recaps.js" in page
    script = client.get("/static/recaps.js").get_data(as_text=True)
    assert "WOM.Remember" in script and "recaps.board" in script


def test_the_help_page_describes_both_leaderboards(client):
    """It explained one competition for as long as there was one. A second
    arriving is exactly the sort of thing help text does not notice."""
    from wom import winners
    page = client.get("/help").get_data(as_text=True)
    for board in winners.BOARDS:
        assert winners.BOARD_LABELS[board] in page, board


def test_the_day_breakdown_is_labelled_for_the_board_it_is_on(client):
    """One script serves both leaderboards, so a label naming one rule is
    wrong on the other half the time."""
    script = client.get("/static/board.js").get_data(as_text=True)
    assert "Gained today: " in script and "Toward 99 today: " in script
    assert 'board === "grinding"' in script


def test_no_page_still_claims_the_session_events_go_unread(signed_in):
    """They were collected and unread for about an hour. Then they were not."""
    page = signed_in.get("/admin").get_data(as_text=True)
    assert "Nothing on the dashboard reads this yet" not in page
