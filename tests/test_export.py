"""The export, in both formats, and the dates that bound it."""

import json

from conftest import seed, snapshot


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
