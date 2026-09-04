"""The three commands beside the app: backup, deploy, and the icon fetch.

Four hundred and thirty lines that nothing had ever run. Two of them touch
the live deployment and one of them decides whether a backup counts as a
backup, which is the code you least want to find out about on the day you
need it.

Nothing here reaches the network or Fly: the parts that do are handed a stand
-in, and what is tested is the judgement around them - what makes a copy
unacceptable, what gets deleted, and what stops a deploy.
"""

import os
import sqlite3
import subprocess

import pytest

import backup
import deploy
import fetch_icons

# -- backup: whether a copy is worth keeping ------------------------------

def _database(path, players=1, corrupt=False):
    """A file shaped like the one Fly hands back."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE players (id INTEGER PRIMARY KEY, username TEXT);
        CREATE TABLE snapshots (id INTEGER PRIMARY KEY);
        CREATE TABLE metrics (player_id INTEGER);
        CREATE TABLE summaries (player_id INTEGER);
    """)
    for n in range(players):
        conn.execute("INSERT INTO players (id, username) VALUES (?,?)",
                     (n + 1, "player{}".format(n)))
    # Pad it past the 4096-byte floor a real database always clears.
    conn.execute("CREATE TABLE padding (blob BLOB)")
    conn.execute("INSERT INTO padding VALUES (?)", (b"x" * 8192,))
    conn.commit()
    conn.close()
    if corrupt:
        with open(path, "r+b") as handle:
            handle.seek(4096)
            handle.write(b"\x00" * 2048)
    return path


def test_a_copy_that_never_arrived_is_not_a_backup(tmp_path):
    with pytest.raises(SystemExit):
        backup.verify(str(tmp_path / "nothing.db"))


def test_an_empty_file_is_not_a_backup(tmp_path):
    """A failed transfer leaves a plausible-looking file of nothing."""
    path = tmp_path / "empty.db"
    path.write_bytes(b"")
    with pytest.raises(SystemExit):
        backup.verify(str(path))


def test_a_database_with_no_players_is_refused(tmp_path):
    """It would restore cleanly and lose everything, which is the worst way
    for a backup to fail."""
    _database(str(tmp_path / "bare.db"), players=0)
    with pytest.raises(SystemExit) as stopped:
        backup.verify(str(tmp_path / "bare.db"))
    assert "no players" in str(stopped.value)


def test_a_good_copy_is_counted_rather_than_just_accepted(tmp_path):
    path = _database(str(tmp_path / "good.db"), players=3)
    counts = backup.verify(path)
    assert counts["players"] == 3
    assert set(counts) == set(backup.EXPECTED_TABLES)


def test_every_table_the_backup_checks_for_is_one_the_app_creates():
    """A table renamed in the schema and not here would make verify() throw
    on a copy that was perfectly good."""
    from wom.store.schema import SCHEMA
    for table in backup.EXPECTED_TABLES:
        assert "CREATE TABLE IF NOT EXISTS {} ".format(table) in SCHEMA, table


# -- backup: what gets deleted --------------------------------------------

def _copies(folder, names):
    """A backup folder of its own: tmp_path already holds this test's data/."""
    folder = folder / "backups"
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / name).write_text("x")
    return folder


def test_rotation_keeps_the_newest_and_removes_the_rest(tmp_path, capsys):
    folder = _copies(tmp_path, ["wom-2026-08-{:02d}.db".format(d)
                                for d in range(1, 6)])
    backup.rotate(str(folder), keep=2)
    assert sorted(os.listdir(folder)) == ["wom-2026-08-04.db",
                                          "wom-2026-08-05.db"]


def test_rotation_leaves_alone_anything_that_is_not_a_copy(tmp_path):
    """The folder is a directory on someone's machine, not ours to tidy."""
    folder = _copies(tmp_path, ["wom-2026-08-01.db", "wom-2026-08-02.db"])
    (folder / "notes.txt").write_text("mine")
    backup.rotate(str(folder), keep=1)
    assert "notes.txt" in os.listdir(folder)


def test_keeping_nothing_deletes_nothing_rather_than_everything(tmp_path):
    """`keep=0` reads as "no limit", and the slice that implements it would
    otherwise be [:-0], which is the whole list."""
    folder = _copies(tmp_path, ["wom-2026-08-01.db", "wom-2026-08-02.db"])
    backup.rotate(str(folder), keep=0)
    assert len(os.listdir(folder)) == 2


def test_the_settings_folders_rotate_on_the_same_rule(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    for day in range(1, 5):
        folder = root / "settings-2026-08-{:02d}".format(day)
        folder.mkdir()
        (folder / "config.json").write_text("{}")
    backup.rotate_settings(str(root), keep=2)
    assert sorted(os.listdir(root)) == ["settings-2026-08-03",
                                        "settings-2026-08-04"]


# -- deploy: the reasons not to ------------------------------------------

def _repo(tmp_path):
    """A real git repository, because check_git shells out to a real git."""
    def git(*args):
        subprocess.run(("git",) + args, cwd=str(tmp_path), check=True,
                       capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("one")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    return git


def test_a_dirty_tree_stops_the_deploy(tmp_path, monkeypatch):
    """Deploying uncommitted work puts code live that exists on one laptop."""
    _repo(tmp_path)
    (tmp_path / "a.txt").write_text("changed, and not committed")
    monkeypatch.setattr(deploy, "HERE", str(tmp_path))

    with pytest.raises(deploy.Stop) as stopped:
        deploy.check_git()
    assert "uncommitted" in str(stopped.value)


def test_a_clean_tree_with_no_upstream_is_allowed_through(tmp_path, monkeypatch):
    """`git fetch origin` fails when there is no origin, and a repository that
    has not been pushed anywhere yet is not a reason to refuse to deploy.

    The fetch used to sit outside the try that catches this, so it refused.
    """
    _repo(tmp_path)
    monkeypatch.setattr(deploy, "HERE", str(tmp_path))
    branch, ahead = deploy.check_git()
    assert branch == "main"
    assert ahead == 0


def test_an_unpushed_commit_is_counted_not_refused(tmp_path, monkeypatch):
    """It exists in git, so it is a note rather than a stop."""
    git = _repo(tmp_path)
    origin = tmp_path.parent / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True,
                   capture_output=True)
    git("remote", "add", "origin", str(origin))
    git("push", "-q", "-u", "origin", "main")
    (tmp_path / "a.txt").write_text("two")
    git("commit", "-q", "-am", "second")

    monkeypatch.setattr(deploy, "HERE", str(tmp_path))
    _branch, ahead = deploy.check_git()
    assert ahead == 1


def test_a_missing_flyctl_says_so_rather_than_failing_obscurely(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda _name: None)
    monkeypatch.setattr(deploy.os.path, "exists", lambda _path: False)
    with pytest.raises(deploy.Stop) as stopped:
        deploy.flyctl()
    assert "flyctl" in str(stopped.value)


def test_a_failing_check_stops_before_anything_is_deployed(tmp_path,
                                                           monkeypatch, capsys):
    """The whole point of the script: the deploy is downstream of the checks."""
    monkeypatch.setattr(deploy, "check_git",
                        lambda: (_ for _ in ()).throw(deploy.Stop("no")))
    deployed = []
    monkeypatch.setattr(deploy.subprocess, "run",
                        lambda *a, **k: deployed.append(a))

    assert deploy.main(["--skip-tests"]) == 1
    assert deployed == [], "nothing was shipped"


def test_a_dry_run_checks_and_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "check_git", lambda: ("main", 0))
    monkeypatch.setattr(deploy, "run", lambda *a, **k: "abc1234 a commit")
    deployed = []
    monkeypatch.setattr(deploy.subprocess, "run",
                        lambda *a, **k: deployed.append(a))

    assert deploy.main(["--dry-run", "--skip-tests"]) == 0
    assert deployed == []


def test_everything_the_image_is_built_from_is_on_the_shipped_list():
    """SHIPPED is what deploy checks is committed. A file the Dockerfile
    copies but this list forgets is one that can be deployed uncommitted,
    which is the exact failure the script exists to prevent."""
    with open("Dockerfile", encoding="utf-8") as handle:
        dockerfile = handle.read()
    copied = set()
    for line in dockerfile.splitlines():
        if line.startswith("COPY "):
            copied.update(part.rstrip("/") for part in line.split()[1:-1])
    assert copied <= set(deploy.SHIPPED), sorted(copied - set(deploy.SHIPPED))


# -- fetch_icons ----------------------------------------------------------

class _Response:
    def __init__(self, ok=True, content=b"\x89PNG\r\n\x1a\n", kind="image/png",
                 status=200):
        self.ok = ok
        self.content = content
        self.headers = {"content-type": kind}
        self.status_code = status


class _Session:
    def __init__(self, response=None):
        self.asked = []
        self._response = response or _Response()

    def get(self, url, **_kwargs):
        self.asked.append(url)
        return self._response


def test_an_icon_is_written_only_when_the_answer_is_an_image(tmp_path):
    """A 404 page served with a 200 would otherwise be saved as a PNG."""
    path = str(tmp_path / "attack.png")
    html = _Session(_Response(kind="text/html"))
    assert fetch_icons._download(html, "http://x/attack.png", path, "attack") is False
    assert not os.path.exists(path)

    good = _Session()
    assert fetch_icons._download(good, "http://x/attack.png", path, "attack") is True
    with open(path, "rb") as handle:
        assert handle.read().startswith(b"\x89PNG")


def test_a_refused_download_is_reported_and_not_written(tmp_path):
    path = str(tmp_path / "zulrah.png")
    session = _Session(_Response(ok=False, status=404, kind="text/html"))
    assert fetch_icons._download(session, "http://x/z.png", path, "zulrah") is False
    assert not os.path.exists(path)


def test_boss_icons_are_asked_for_by_metric_name(tmp_path, monkeypatch):
    session = _Session()
    monkeypatch.setattr(fetch_icons.requests, "Session", lambda: session)
    monkeypatch.setattr(fetch_icons.time, "sleep", lambda _s: None)
    monkeypatch.setattr(fetch_icons, "BOSS_ICON_DIR", str(tmp_path))

    saved = fetch_icons.fetch_boss_icons(["zulrah", "vorkath"])
    assert saved == 2
    assert session.asked == [fetch_icons.WOM_ICON_URL.format("vorkath"),
                             fetch_icons.WOM_ICON_URL.format("zulrah")]


def test_the_metric_list_is_empty_rather_than_a_crash_without_a_database(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WOM_DATA_DIR", str(tmp_path / "nothing"))
    assert fetch_icons.known_boss_metrics() == []
    assert "run an update first" in capsys.readouterr().out


def test_the_metric_list_comes_from_what_has_actually_been_stored(
        tmp_path, monkeypatch):
    monkeypatch.setenv("WOM_DATA_DIR", str(tmp_path))
    from conftest import snapshot

    from wom.config import db_path
    from wom.db import Database

    database = Database(db_path())
    database.save_player_details({"id": 1, "username": "z", "displayName": "Z"})
    database.save_snapshot(1, snapshot("2026-08-01T00:00:00.000Z",
                                       skills={"attack": (100, 40)},
                                       bosses={"zulrah": 7}))
    assert fetch_icons.known_boss_metrics() == ["zulrah"]


def test_every_script_runs_on_the_interpreter_the_app_demands(tmp_path):
    """They are commands people type, so they parse under the floor or they
    are not usable at the floor."""
    import py_compile
    for name in ("backup.py", "deploy.py", "fetch_icons.py"):
        py_compile.compile(name, doraise=True,
                           cfile=str(tmp_path / (name + "c")))


def test_the_scripts_have_a_main_that_returns_a_status(capsys):
    """Each is wired to sys.exit(main()), so a main that returns None would
    exit 0 whatever happened."""
    for module in (backup, deploy, fetch_icons):
        assert callable(module.main)
        with open(module.__file__, encoding="utf-8") as handle:
            assert "sys.exit(main())" in handle.read()

