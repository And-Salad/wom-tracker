"""Celebrities: followed alongside the group, never part of its competition."""

from conftest import section, seed
from test_schedule import at, written

from wom import periods, summaries
from wom.config import Config, group_only, tracked_usernames


def famous(names=("Lynx Titan",), group=("Zezima",)):
    settings = Config()
    settings["usernames"] = list(group)
    settings["celebrities"] = list(names)
    settings.save()
    return settings


def add_celebrity(database, pid=9, name="Lynx Titan"):
    database.save_player_details({"id": pid, "username": name.lower(),
                                  "displayName": name, "type": "regular"})


def test_the_updater_follows_both_lists_once_each(config):
    config["usernames"] = ["Zezima", "Other"]
    config["celebrities"] = ["Lynx Titan", "zezima"]
    assert tracked_usernames(config) == ["Zezima", "Other", "Lynx Titan"]


def test_the_group_is_everyone_but_the_celebrities(config):
    config["celebrities"] = ["Lynx Titan"]
    rows = [{"username": "zezima"}, {"username": "lynx titan"}]
    assert group_only(rows, config) == [{"username": "zezima"}]


def test_a_celebrity_is_never_owed_a_note(db, player):
    """Counted, a celebrity would hold every window open for ever."""
    add_celebrity(db)
    now = at(2026, 9, 8, 6)
    written(db, player, now, periods.SUMMARY_PERIODS)
    assert summaries.due_periods(db, now), "without the list it is still owed"
    assert summaries.due_periods(
        db, now, config={"celebrities": ["Lynx Titan"]}) == []


def test_celebrities_are_saved_from_their_own_section(signed_in):
    signed_in.post("/admin/celebrities",
                   data={"celebrities": "Lynx Titan\n  \nlynx titan\nB0aty"})
    assert Config()["celebrities"] == ["Lynx Titan", "B0aty"]
    page = signed_in.get("/admin").get_data(as_text=True)
    assert "Save celebrities" in page and "B0aty" in page


def test_nobody_is_in_the_group_and_famous_at_once(signed_in):
    famous(names=("Lynx Titan", "Zezima"), group=())
    signed_in.post("/admin/settings", data={"usernames": "Zezima"})
    assert Config()["celebrities"] == ["Lynx Titan"]
    signed_in.post("/admin/celebrities", data={"celebrities": "zezima\nB0aty"})
    assert Config()["celebrities"] == ["B0aty"]


def test_pruning_keeps_the_celebrities(signed_in, app):
    database = seed(app)
    add_celebrity(database)
    famous()
    signed_in.post("/admin/prune")
    assert database.player_by_username("lynx titan") is not None
    assert database.player_by_username("zezima") is not None


def test_a_bare_link_leaves_the_celebrities_unticked(client, app):
    database = seed(app)
    add_celebrity(database)
    famous()
    page = client.get("/players").get_data(as_text=True)
    assert "<h2>Celebrities</h2>" in page
    box = page[page.index('value="lynx titan"'):]
    box = box[:box.index(">")]
    assert 'data-section="famous"' in box and "checked" not in box
    rows = client.get("/api/players?period=Week").get_json()["rows"]
    assert "Lynx Titan" not in [row["name"] for row in rows]


def test_a_ticked_celebrity_is_shown(client, app):
    database = seed(app)
    add_celebrity(database)
    famous()
    rows = client.get("/api/players?period=Week&picked=1"
                      "&player=zezima&player=lynx+titan").get_json()["rows"]
    assert "Lynx Titan" in [row["name"] for row in rows]


def test_no_celebrities_means_no_empty_section(client, app):
    seed(app)
    assert "Celebrities" not in client.get("/players").get_data(as_text=True)


def test_the_leaderboards_leave_the_celebrities_out(client, app):
    database = seed(app)
    add_celebrity(database)
    famous()
    page = client.get("/leaderboards").get_data(as_text=True)
    assert "Lynx Titan" not in section(page, "maxing")
    assert "Lynx Titan" not in section(page, "grinding")
