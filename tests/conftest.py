"""Shared fixtures.

Every test runs against a throwaway data directory. This has to be set before
anything imports wom.config, which reads WOM_DATA_DIR once at import time to
work out where the database, the config and the prompts live - so it happens
here, at the top of the first file pytest loads.
"""

import os
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="wom-tests-")
os.environ["WOM_DATA_DIR"] = _SANDBOX
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
def app(tmp_path, monkeypatch):
    """The Flask app, pointed at a database of its own."""
    from wom.web import app as web_app

    database = Database(str(tmp_path / "web.db"))
    monkeypatch.setattr(web_app, "Database", lambda _path: database)
    monkeypatch.setattr(web_app, "CONFIG_PATH_FOR_TESTS", str(tmp_path / "c.json"),
                        raising=False)
    application = web_app.create_app()
    application.config["TESTING"] = True
    application.config["DATABASE"] = database
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def signed_in(client):
    client.post("/admin/login", data={"password": "test-password"})
    return client
