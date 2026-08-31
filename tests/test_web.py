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
