"""Which commit is running, and how the admin page says so.

The point of all of it is answering "did my deploy land" by reading rather
than by inferring, so what matters here is that the answer is the baked-in
commit when there is one and an honest blank when there is not - never a
stale or invented value.
"""

from datetime import datetime, timedelta, timezone

import pytest
from conftest import seed

from wom import build


@pytest.fixture(autouse=True)
def _forget_build():
    """Nobody inherits a commit resolved by an earlier test."""
    build.forget()
    yield
    build.forget()


def test_the_baked_in_commit_wins(monkeypatch):
    monkeypatch.setenv(build.BUILD_ENV, "0123456789abcdef0123456789abcdef01234567")
    assert build.sha() == "0123456789abcdef0123456789abcdef01234567"
    assert build.info()["short"] == "0123456"


def test_a_blank_variable_falls_back_rather_than_reporting_nothing(monkeypatch):
    """An image built without the build-arg sets it to "", not to nothing.

    Read as "the commit is the empty string" that would print a blank build
    for ever; the fallback has to treat it the same as absent.
    """
    monkeypatch.setenv(build.BUILD_ENV, "   ")
    # Running from a clone, git answers; in an image it does not. Either is a
    # correct outcome here - what must not happen is the blank being kept.
    assert build.sha() != "   "


def test_no_commit_anywhere_is_an_empty_string_not_a_crash(monkeypatch):
    monkeypatch.delenv(build.BUILD_ENV, raising=False)
    monkeypatch.setattr(build, "_from_git", lambda: "")
    assert build.sha() == ""
    assert build.info()["short"] == ""


def test_the_answer_is_resolved_once(monkeypatch):
    """It is read on every admin render, and the fallback shells out."""
    calls = []
    monkeypatch.delenv(build.BUILD_ENV, raising=False)
    monkeypatch.setattr(build, "_from_git", lambda: calls.append(1) or "abc123")
    build.sha()
    build.sha()
    assert len(calls) == 1


def test_it_says_how_long_this_process_has_been_up():
    began = datetime.now(timezone.utc) - timedelta(hours=3)
    assert build.info(started_at=began)["ago"] == "3h ago"


def test_the_admin_page_prints_the_build(signed_in, app, monkeypatch):
    seed(app)
    monkeypatch.setenv(build.BUILD_ENV, "abcdef1234567890abcdef1234567890abcdef12")
    build.forget()
    page = signed_in.get("/admin").get_data(as_text=True)
    assert "abcdef1" in page
    assert "this process started" in page


def test_an_unknown_build_says_so_rather_than_showing_a_blank(signed_in, app,
                                                              monkeypatch):
    """A bare "Build" with nothing after it reads as a rendering fault."""
    seed(app)
    monkeypatch.delenv(build.BUILD_ENV, raising=False)
    monkeypatch.setattr(build, "_from_git", lambda: "")
    build.forget()
    page = signed_in.get("/admin").get_data(as_text=True)
    assert "no commit was baked in" in page


def test_the_build_is_not_on_the_public_pages(client, app, monkeypatch):
    """It is one more thing about the deployment that a share link need not say."""
    seed(app)
    monkeypatch.setenv(build.BUILD_ENV, "feedface1234567890feedface1234567890feed")
    build.forget()
    for path in ("/", "/players", "/recaps", "/leaderboards"):
        assert "feedfac" not in client.get(path).get_data(as_text=True), path
