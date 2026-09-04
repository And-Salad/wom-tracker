"""The table and the history plot on the Data page."""
from conftest import seed, snapshot


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
    opens = body.index('id="kind"')
    kind = body[opens:body.index("</select>", opens)]
    assert 'value=""' not in kind, "kind must always name one kind"
    assert kind.index('value="skill"') < kind.index('value="boss"'), (
        "skills first, so the page opens on them")


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
