"""The milestone feed and the gallery."""
from conftest import seed


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
