"""The milestone feed and the gallery."""
from conftest import seed


def test_the_milestones_page_offers_a_filter_for_each_kind(client, app):
    seed(app)
    page = client.get("/milestones").get_data(as_text=True)
    for label in ("Milestones", "Collection log", "Quests", "Diaries",
                  "Combat tasks", "Pets"):
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


def test_milestones_crossed_in_one_gap_read_as_the_run_they_were(app):
    """Wise Old Man dates everything it found between two snapshots to the
    same instant, so a tie is the common case here, not the edge one.

    Ordering those by name sorts "1000" above "500", which is a progression
    told backwards. The threshold is the number that was actually passed.
    """
    from wom.web import views
    database = seed(app)
    same = "2026-08-30T10:00:00.000Z"
    database.save_achievements(1, [
        {"name": "{} Zulrah kills".format(n), "metric": "zulrah",
         "measure": "kills", "threshold": n, "createdAt": same,
         "accuracy": 3600000}
        for n in (500, 50, 1000, 100)])
    feed = views.milestone_feed(database, [{"id": 1}], {})["rows"]
    assert [row["name"] for row in feed] == [
        "50 Zulrah kills", "100 Zulrah kills",
        "500 Zulrah kills", "1000 Zulrah kills"]


def test_a_rough_date_says_how_rough_it_is(app):
    """The ~ says a date is an estimate. Within two days and within four
    years are not the same claim, and only one of them is a date."""
    from wom.web import views
    database = seed(app)
    database.save_achievements(1, [
        {"name": "99 Attack", "metric": "attack", "measure": "experience",
         "threshold": 13034431, "createdAt": "2026-08-30T10:00:00.000Z",
         "accuracy": 3600000},
        {"name": "99 Magic", "metric": "magic", "measure": "experience",
         "threshold": 13034431, "createdAt": "2026-08-29T10:00:00.000Z",
         "accuracy": 132841642470},
        {"name": "Base 60 Stats", "metric": "overall", "measure": "levels",
         "threshold": 6569808, "createdAt": None, "accuracy": -1}])
    rows = {row["name"]: row
            for row in views.milestone_feed(database, [{"id": 1}], {})["rows"]}

    assert rows["99 Attack"]["precision"] == views.EXACT
    assert not rows["99 Attack"]["within"], "an exact date explains nothing"
    assert not rows["99 Attack"]["when"].startswith("~")

    assert rows["99 Magic"]["precision"] == views.APPROXIMATE
    assert rows["99 Magic"]["when"].startswith("~")
    assert "years" in rows["99 Magic"]["within"]

    assert rows["Base 60 Stats"]["precision"] == views.UNKNOWN
    assert rows["Base 60 Stats"]["when"] == "unknown"


def test_every_row_says_which_source_it_came_from(client, app):
    """One is a threshold Wise Old Man reconstructed, the other a moment a
    client stamped. Nothing distinguishes them once they are merged."""
    from wom import gameplay
    database = seed(app)
    database.save_achievements(1, [{
        "name": "99 Attack", "metric": "attack", "measure": "experience",
        "threshold": 13034431, "createdAt": "2026-08-30T10:00:00.000Z",
        "accuracy": 3600000}])
    gameplay.store(database, "zezima", "quest", "2026-08-30T21:15:00.000000Z",
                   {"type": "QUEST", "extra": {"questName": "Dragon Slayer I"}})
    feed = client.get("/api/milestones?period=Year").get_json()["feed"]
    assert {row["source"] for row in feed} == {"wom", "dink"}


def _shot(app, kind="pet", caption="Ikkle hydra", extra=b"clickable"):
    from wom import gallery
    return gallery.store(app.config["DATABASE"], "zezima", kind,
                         "2026-09-03T21:00:00.000000Z",
                         b"\x89PNG\r\n\x1a\n" + extra, caption=caption)


def test_a_gallery_picture_is_a_button_not_a_bare_image(client, app):
    """Something you can do should answer the keyboard and say so, without
    any help from us."""
    seed(app)
    _shot(app)
    page = client.get("/gallery").get_data(as_text=True)
    shots = page.split('class="shots"')[1].split("</section>")[0]
    assert '<button type="button" class="shot"' in shots
    assert shots.count("<img") == shots.count("data-full="), (
        "every picture needs the full-size URL the viewer opens")
    assert 'id="viewer"' in page and 'id="viewer-image"' in page


def test_the_viewer_caption_names_who_and_when(client, app):
    seed(app)
    _shot(app, kind="death", caption="lost 42 gp", extra=b"captioned")
    page = client.get("/gallery").get_data(as_text=True)
    caption = page.split("data-caption=")[1][:140]
    assert "lost 42 gp" in caption and "Zezima" in caption


def test_a_picture_with_no_player_of_ours_is_not_shown(client, app):
    """The page is scoped to the sidebar's selection like every other."""
    from wom import gallery
    seed(app)
    gallery.store(app.config["DATABASE"], "somebody-else", "pet",
                  "2026-09-03T21:00:00.000000Z",
                  b"\x89PNG\r\n\x1a\n" + b"stranger", caption="not ours")
    assert "not ours" not in client.get("/gallery").get_data(as_text=True)
