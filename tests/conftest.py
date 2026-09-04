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


def before_migration(db, number):
    """Wind this file back to just before migration `number`, so it runs again.

    A database records which migrations it has had, in SQLite's own
    user_version, and a step that has run is never asked again. So a test that
    stages what an older file looked like has to say that here too - otherwise
    the file it built claims to be current, and the step the test is about is
    quite correctly skipped.

    Reopening the database is what applies it, exactly as a deploy would.
    """
    conn = db.connect()
    with conn:
        conn.execute("PRAGMA user_version = {:d}".format(number - 1))


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


# -- seeding the web tests ------------------------------------------------
#
# These built the fixtures for one 2174-line test file, defined at the point
# in it where they were first wanted - one at line 830, another at 1779 - so
# the file's shape was whatever you could hold in your head while scrolling
# it. They are shared by several files now, which is what they always were.

def calendar_seed(app, polled=True):
    """Two accounts, one of which is only ever seen mid-afternoon.

    `polled` records an update run for each day, which is the evidence that
    an account with no reading that day played nothing rather than going
    unwatched. Tests about that rule itself pass False.
    """
    database = app.config["DATABASE"]
    for pid, name in ((1, "Zezima"), (2, "Other")):
        database.save_player_details({"id": pid, "username": name.lower(),
                                      "displayName": name, "type": "regular"})
    # Zezima is read four times a day, every day.
    for day, xp in (("2026-08-28", 1000), ("2026-08-29", 2000),
                    ("2026-08-30", 3000), ("2026-08-31", 3100)):
        for hour in ("02", "23"):
            database.save_snapshot(1, snapshot(
                day + "T" + hour + ":00:00.000Z",
                skills={"attack": (xp + (50 if hour == "23" else 0), 50)}))
    # Other is seen once in July and then not again until the 30th.
    database.save_snapshot(2, snapshot("2026-07-02T12:00:00.000Z",
                                       skills={"attack": (500, 40)}))
    for hour, xp in (("21", 9000), ("23", 9500)):
        database.save_snapshot(2, snapshot("2026-08-30T" + hour + ":00:00.000Z",
                                           skills={"attack": (xp, 60)}))
    if polled:
        record_runs(database, 2, ["2026-08-{:02d}".format(day) for day in range(1, 32)])
    return database


def round_ups(db, boards=("maxing", "grinding"), keys=("day", "week", "month")):
    from wom import periods
    for key in keys:
        window = periods.latest_window(key)
        for board in boards:
            db.save_group_summary(window, "{} {}.".format(board, key),
                                  "h-" + board + key, board=board)
    return db


def seed(app):
    database = app.config["DATABASE"]
    database.save_player_details({"id": 1, "username": "zezima",
                                  "displayName": "Zezima", "type": "regular"})
    for day, xp in (("2026-08-25", 1000), ("2026-08-31", 5000)):
        database.save_snapshot(1, snapshot(day + "T12:00:00.000Z",
                                           skills={"attack": (xp, 40)},
                                           bosses={"zulrah": xp // 100}))
    return database


def section(page, board):
    """One board's half of the leaderboards page.

    Both are rendered whichever is on screen, so anything asserted about one
    of them has to be looked for inside its own section or the other board
    answers for it.
    """
    start = page.index('data-board="{}"'.format(board))
    end = page.find("<section", start)
    return page[start:end if end != -1 else len(page)]


def record_runs(database, players, days):
    """Say the tracker looked at everyone on each of these days."""
    for day in days:
        run = database.start_run("test", roster=players)
        database.finish_run(run, ok_count=players, fail_count=0)
        database.connect().execute(
            "UPDATE runs SET started_at=? WHERE id=?",
            (day + "T12:00:00.000Z", run))
    database.connect().commit()
