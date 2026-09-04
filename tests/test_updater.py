"""One update pass, against a stand-in for the API."""

from conftest import snapshot

from wom.api import WomError
from wom.updater import update_all, update_one


class FakeClient:
    """Answers like the Wise Old Man client, and records what was asked."""

    def __init__(self, details=None, fail_update=False, fail_fetch=False,
                 snapshots=None, achievements=None, recent=None,
                 fail_recent=False):
        self.details = details or {
            "id": 1, "username": "zezima", "displayName": "Zezima",
            "type": "regular", "exp": 500,
            "latestSnapshot": snapshot("2026-08-31T00:00:00.000Z",
                                       skills={"attack": (500, 40)}),
        }
        self.fail_update = fail_update
        self.fail_fetch = fail_fetch
        self.snapshots = snapshots or []
        self.recent = recent or []
        self.fail_recent = fail_recent
        self._achievements = achievements or []
        self.calls = []

    def update_player(self, username):
        self.calls.append(("update", username))
        if self.fail_update:
            raise WomError("rate limited", 429)
        return self.details

    def get_player(self, username):
        self.calls.append(("get", username))
        if self.fail_fetch:
            raise WomError("not found", 404)
        return self.details

    def get_achievements(self, username):
        self.calls.append(("achievements", username))
        return self._achievements

    def iter_snapshots(self, username, **kwargs):
        self.calls.append(("snapshots", username))
        return iter(self.snapshots)

    def get_snapshots(self, username, **kwargs):
        self.calls.append(("recent", username))
        if self.fail_recent:
            raise WomError("rate limited", 429)
        return self.recent


def test_a_successful_pass_stores_the_player_and_its_snapshot(db):
    result = update_one(FakeClient(), db, "zezima")
    assert result.ok
    assert db.player_by_username("zezima")["display_name"] == "Zezima"
    assert db.snapshot_count(1) == 1


def test_a_failed_refresh_falls_back_to_the_stored_profile(db):
    """A player updated moments ago still has current data worth keeping."""
    client = FakeClient(fail_update=True)
    result = update_one(client, db, "zezima")
    assert result.ok, "a refused refresh is not a failed update"
    assert "cached profile" in result.message
    assert [c[0] for c in client.calls][:2] == ["update", "get"]


def test_both_calls_failing_is_a_failure(db):
    result = update_one(FakeClient(fail_update=True, fail_fetch=True), db, "zezima")
    assert not result.ok


def test_history_is_imported_once(db):
    client = FakeClient(snapshots=[
        snapshot("2026-01-01T00:00:00.000Z", skills={"attack": (10, 1)}),
        snapshot("2026-02-01T00:00:00.000Z", skills={"attack": (20, 2)}),
    ])
    first = update_one(client, db, "zezima")
    assert first.imported == 2
    second = update_one(client, db, "zezima")
    assert second.imported == 0, "backfill runs once, not on every pass"
    assert [c for c in client.calls].count(("snapshots", "zezima")) == 1


def test_milestones_are_counted_only_when_new(db):
    client = FakeClient(achievements=[
        {"name": "99 Attack", "metric": "attack", "threshold": 13034431,
         "createdAt": "2026-08-01T00:00:00.000Z", "accuracy": 1},
    ])
    assert update_one(client, db, "zezima").milestones == 1
    assert update_one(client, db, "zezima").milestones == 0


def test_a_broken_achievements_call_does_not_fail_the_update(db):
    class NoAchievements(FakeClient):
        def get_achievements(self, username):
            raise WomError("boom", 500)

    assert update_one(NoAchievements(), db, "zezima").ok


def test_update_all_records_a_run_and_reports_progress(db):
    seen = []
    results = update_all(FakeClient(), db, ["zezima"],
                         progress=lambda i, n, r: seen.append(r.username))
    assert len(results) == 1 and seen == ["zezima"]
    run = db.query_one("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    assert run["ok_count"] == 1 and run["fail_count"] == 0


def test_update_all_can_be_cancelled(db):
    results = update_all(FakeClient(), db, ["a", "b", "c"], cancelled=lambda: True)
    assert results == []


def test_a_callback_that_raises_does_not_break_the_run(db):
    def explode(*_args):
        raise RuntimeError("the UI is on fire")

    results = update_all(FakeClient(), db, ["zezima"], progress=explode)
    assert results[0].ok


def test_the_roster_spelling_of_a_name_is_the_one_shown():
    """Wise Old Man holds some names in lower case; the roster is where a
    person wrote them out properly."""
    from wom.updater import _spelled_as_asked
    assert _spelled_as_asked({"displayName": "lynx titan"}, "Lynx Titan") \
        ["displayName"] == "Lynx Titan"
    # Neither source is authoritative, so it upgrades the other way too
    # rather than flattening a name the API had spelled properly.
    assert _spelled_as_asked({"displayName": "SirPugger"}, "sirpugger") \
        ["displayName"] == "SirPugger"
    # A name that genuinely changed is a different name, not a reshaped one.
    assert _spelled_as_asked({"displayName": "New Name"}, "Old Name") \
        ["displayName"] == "New Name"
    # And nothing is invented where the API said nothing.
    assert _spelled_as_asked({}, "Lynx Titan") == {}


def test_a_rejected_api_key_is_dropped_rather_than_taking_the_tracker_down():
    """A key the API refuses answers 403 to every request while the same
    request without it is served, so the tracker goes quiet holding a key."""
    import wom.api as api

    class Reply:
        def __init__(self, code, body):
            self.status_code = code
            self.headers = {}
            self._body = body

        def json(self):
            return self._body

        @property
        def text(self):
            import json as _json
            return _json.dumps(self._body)

    class Session:
        def __init__(self):
            self.keys_sent = []

        def request(self, method, url, params=None, headers=None, timeout=None):
            self.keys_sent.append(headers.get("x-api-key"))
            if headers.get("x-api-key"):
                return Reply(403, {"message": "Invalid API Key. Please check ..."})
            return Reply(200, {"displayName": "Zezima"})

    session = Session()
    client = api.WomClient("not-a-real-key", session=session)
    assert client.get_player("zezima")["displayName"] == "Zezima"
    assert session.keys_sent == ["not-a-real-key", None], \
        "tried once with it, then dropped it"
    assert client.api_key == "", "and stops sending it for the rest of the run"


def test_an_update_run_places_the_sessions_it_just_learned_about(db, monkeypatch):
    """The correction has to ride on the update, not on a command somebody
    remembers to run - the same reason compaction was moved onto the schedule."""
    from conftest import snapshot
    from wom import updater

    db.save_player_details({"id": 1, "username": "zezima",
                            "displayName": "Zezima", "type": "regular"})
    db.save_snapshot(1, snapshot("2026-09-04T00:50:00.000Z",
                                 skills={"attack": (1000, 40)}))
    db.save_snapshot(1, snapshot("2026-09-04T05:10:00.000Z",
                                 skills={"attack": (401000, 60)}))
    for kind, when in (("login", "2026-09-04T01:00:00.000000Z"),
                       ("logout", "2026-09-04T05:00:00.000000Z")):
        db.record_session_event("zezima", kind, {"total_exp": None}, {}, when=when)

    monkeypatch.setattr(updater, "SESSION_LOOKBACK_DAYS", 3650)
    assert updater._place_sessions(db, ["zezima"]) > 0
    # Two: what had been earned by midnight, and the whole of it at the
    # moment the session closed.
    assert db.query_one("SELECT COUNT(*) AS n FROM snapshots"
                        " WHERE origin='derived'")["n"] == 2


def test_a_failure_to_place_sessions_never_breaks_the_run(db, monkeypatch):
    from wom import updater

    db.save_player_details({"id": 1, "username": "zezima",
                            "displayName": "Zezima", "type": "regular"})

    def explode(*_args, **_kwargs):
        raise RuntimeError("no")
    monkeypatch.setattr(updater.sessions, "attribute", explode)
    assert updater._place_sessions(db, ["zezima"]) == 0, "swallowed, not raised"


def test_placing_sessions_skips_a_name_we_have_never_stored(db):
    from wom import updater
    assert updater._place_sessions(db, ["nobody-here"]) == 0


# -- readings our own polling never sees ----------------------------------

def test_a_reading_wise_old_man_took_between_ours_is_recovered(db):
    """update_player hands back the latest snapshot and nothing else, so a
    push it recorded in between is invisible however often we ask."""
    push = snapshot("2026-08-30T23:55:00.000Z", skills={"attack": (900, 42)})
    client = FakeClient(recent=[push])
    result = update_one(client, db, "zezima")
    assert result.ok
    assert ("recent", "zezima") in client.calls
    held = [r["captured_at"] for r in db.query(
        "SELECT captured_at FROM snapshots ORDER BY captured_at")]
    assert "2026-08-30T23:55:00.000Z" in held
    assert "reading we had missed" in result.message


def test_a_recovered_reading_is_marked_as_one_we_did_not_cause(db):
    """It is stamped when Wise Old Man took it, not when we collected it, so
    it must not look like a poll - compaction keeps one and thins the other."""
    push = snapshot("2026-08-30T23:55:00.000Z", skills={"attack": (900, 42)})
    update_one(FakeClient(recent=[push]), db, "zezima")
    row = db.query_one("SELECT origin FROM snapshots WHERE captured_at=?",
                       ("2026-08-30T23:55:00.000Z",))
    assert row["origin"] == "archive"


def test_readings_we_already_hold_are_not_stored_twice(db):
    push = snapshot("2026-08-30T23:55:00.000Z", skills={"attack": (900, 42)})
    client = FakeClient(recent=[push])
    update_one(client, db, "zezima")
    before = db.snapshot_count(1)
    update_one(client, db, "zezima")
    assert db.snapshot_count(1) == before


def test_a_refused_history_call_does_not_fail_the_update(db):
    """The update itself is the thing worth having."""
    result = update_one(FakeClient(fail_recent=True), db, "zezima")
    assert result.ok
    assert "we had missed" not in result.message
    assert db.player_by_username("zezima") is not None


def test_a_client_too_old_to_answer_that_call_is_survivable(db):
    """Guards the shape of the failure, not the failure itself: a bare
    AttributeError here used to be swallowed silently and the whole feature
    did nothing while every test passed."""
    class Older(FakeClient):
        get_snapshots = None

        def __getattr__(self, name):
            raise AttributeError(name)

    result = update_one(Older(), db, "zezima")
    assert result.ok
