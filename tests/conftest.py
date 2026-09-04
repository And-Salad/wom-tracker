"""Shared fixtures.

Every test gets a data directory of its own. That directory is where the
database, the config, the prompts and the gallery all live, so pointing
WOM_DATA_DIR at a fresh one per test isolates the lot in a single move -
see the `data_dir` fixture below for why that used not to be possible.
"""

import os
import tempfile

# A floor, not the isolation: WOM_DATA_DIR is set again per test. This is here
# so that anything constructed at import time - a stray module-level Config,
# a collection error - still cannot reach a real installation's data.
os.environ["WOM_DATA_DIR"] = tempfile.mkdtemp(prefix="wom-tests-")
os.environ.setdefault("WOM_ADMIN_PASSWORD", "test-password")
os.environ.setdefault("WOM_SECRET_KEY", "test-key")
os.environ.setdefault("WOM_INSECURE_COOKIE", "1")
# A real key here would mean a test could spend money.
os.environ.pop("ANTHROPIC_API_KEY", None)

import pytest  # noqa: E402

from wom.config import Config  # noqa: E402
from wom.db import Database  # noqa: E402


def snapshot(when, skills=None, bosses=None, activities=None):
    """A snapshot payload shaped like the one the API returns.

    Values are given as the API gives them, -1 included, so tests exercise the
    same conversion the real path does.
    """
    def section(values, key):
        return {metric: {key: value, "rank": 1} for metric, value in values.items()}

    return {"createdAt": when, "data": {
        "skills": {m: {"experience": v, "level": lvl, "rank": 1}
                   for m, (v, lvl) in (skills or {}).items()},
        "bosses": section(bosses or {}, "kills"),
        "activities": section(activities or {}, "score"),
    }}


def as_polled(db):
    """Mark every stored reading as one we made ourselves.

    A test writes an old createdAt at the moment it runs, which is exactly
    what an imported reading looks like - so without this the compaction
    tests are describing archive rows, which are deliberately never thinned.
    Real polled history was stored as it happened and carries `poll`.
    """
    conn = db.connect()
    with conn:
        conn.execute("UPDATE snapshots SET origin='poll'")


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """A data directory per test: its config, its prompts, its database.

    Autouse, and every test gets its own, because the alternative was one
    directory for the whole session. The app reads its settings through
    `Config()` on each request, so a test that posted to /admin/settings wrote
    into a file the next test would read - and the suite passed only because
    the handful of tests that changed a setting remembered to put it back in a
    `finally`. One failure part-way through such a test left the setting
    changed for everything that ran after it, which is an order-dependent
    suite waiting to happen rather than an isolated one.

    The fixture that was supposed to prevent this monkeypatched a name -
    `CONFIG_PATH_FOR_TESTS` - that existed nowhere in the source, with
    `raising=False`, so it had never done anything at all.
    """
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setenv("WOM_DATA_DIR", str(folder))
    return folder


@pytest.fixture
def db(tmp_path):
    """An empty database, per test."""
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def player(db):
    """One stored player, with no snapshots yet."""
    db.save_player_details({"id": 1, "username": "zezima",
                            "displayName": "Zezima", "type": "regular"})
    return db.player_by_username("zezima")


@pytest.fixture
def config(tmp_path):
    return Config(str(tmp_path / "config.json"))


@pytest.fixture
def app(data_dir):
    """The Flask app, on this test's own data directory.

    Nothing is patched: the app finds its database and its settings under
    WOM_DATA_DIR the same way it does when it is really running.
    """
    from wom.web import create_app

    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def signed_in(client):
    client.post("/admin/login", data={"password": "test-password"})
    return client


@pytest.fixture(autouse=True)
def _forget_cached_zone():
    """Nobody inherits the time zone a previous test configured.

    zone() resolves the setting once and remembers it in a module global, so
    that a running server does not re-read a file on every date it formats.
    That cache outlives a test even now each test has a config file of its
    own, which is how a test that set Australia/Perth moved the day
    boundaries under every calendar test that ran after it.
    """
    from wom import scheduler
    scheduler.forget_zone()
    yield
    scheduler.forget_zone()


@pytest.fixture(autouse=True)
def _no_wrong_password_delay(monkeypatch):
    """Wrong guesses cost half a second in production, and nothing here.

    The delay is real and deliberate, but paying it in tests bought no
    coverage and cost a third of the suite's runtime - two tests alone spent
    six and a half seconds asleep.
    """
    from wom.web import admin
    monkeypatch.setattr(admin, "WRONG_PASSWORD_DELAY", 0)
