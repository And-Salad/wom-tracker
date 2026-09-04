"""The admin half: the boundary, the settings, and the prompts."""

import os

from conftest import seed

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


def test_signing_in_goes_where_the_guard_sent_you(client):
    response = client.post("/admin/login?next=/admin/prompts",
                           data={"password": "test-password"})
    assert response.headers["Location"] == "/admin/prompts"


def test_signing_in_will_not_be_redirected_off_the_site(client):
    """`next` is somewhere on this site or it is nowhere.

    Unchecked it makes the login page the first hop of a phishing chain: the
    link wears this site's address, the password goes to this site, and the
    admin lands on somebody else's page believing they arrived here.
    """
    for target in ("https://evil.example.com/x", "//evil.example.com/x",
                   "/\\evil.example.com/x", "http://evil.example.com"):
        response = client.post("/admin/login?next=" + target,
                               data={"password": "test-password"})
        assert response.headers["Location"] == "/admin", target


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
    from datetime import datetime
    from datetime import timezone as utc
    window = periods.latest_window("day",
                                   datetime(2026, 9, 1, 12, tzinfo=utc.utc))
    assert window.start.utcoffset().total_seconds() == 8 * 3600


def test_admin_disappears_entirely_without_a_password(monkeypatch):
    """Fail closed: no password must mean no routes, not open ones."""
    from wom.web import create_app

    monkeypatch.delenv("WOM_ADMIN_PASSWORD", raising=False)
    application = create_app()
    assert application.config["ADMIN"] is False
    with application.test_client() as bare:
        assert bare.get("/admin").status_code == 404
        assert bare.post("/admin/run/update").status_code == 404
        assert bare.get("/").status_code == 200


def _prompt_files():
    """Every prompt file currently on disk, by name."""
    from wom.config import data_dir
    return {name for name in os.listdir(data_dir()) if name.endswith(".txt")}


def _base_prompt(kind="player"):
    """The base prompt file, brought into being the way the app brings it.

    It is written from the built-in default on first use rather than shipped,
    so a test that only reads it has to ask for it first. These tests used to
    find it already there, left by whichever earlier test had happened to open
    the prompts page - which is not a thing a test may rely on.
    """
    from wom import summaries as core
    core.load_prompt(kind=kind)
    return core.base_prompt_path(kind=kind)


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
    base = _base_prompt("player")
    with open(base, encoding="utf-8") as handle:
        before = handle.read()
    override = core.period_prompt_path("quarter", kind="player")
    try:
        signed_in.post("/admin/prompts", data={
            "kind": "player", "period": "quarter", "text": "Only for quarters."})
        assert os.path.exists(override), "the override was written"
        with open(override, encoding="utf-8") as handle:
            assert handle.read().strip() == "Only for quarters."
        with open(base, encoding="utf-8") as handle:
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
        with open(override, encoding="utf-8") as handle:
            assert handle.read().strip(), "and not left empty"

        signed_in.post("/admin/prompts", data={
            "kind": "group", "period": "day", "delete": "1", "text": "ignored"})
        assert not os.path.exists(override), "removed, falling back to the base"
    finally:
        if os.path.exists(override):
            os.remove(override)


def test_a_prompt_cannot_be_saved_empty(signed_in, app):
    """An empty system prompt is not an edit, it is a broken round-up."""
    base = _base_prompt("player")
    with open(base, encoding="utf-8") as handle:
        before = handle.read()
    signed_in.post("/admin/prompts", data={"kind": "player", "text": "   "})
    with open(base, encoding="utf-8") as handle:
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
