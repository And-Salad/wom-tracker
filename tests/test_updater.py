"""One update pass, against stand-ins for Wise Old Man and the hiscores."""

import json
import os

from conftest import snapshot

from wom.api import WomError
from wom.hiscores import HiscoresError, HiscoresNotFound
from wom.updater import update_all, update_one

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "hiscores")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


class FakeClient:
    """Answers like the Wise Old Man client, and records what was asked."""

    def __init__(self, details=None, fail_update=False, fail_fetch=False,
                 snapshots=None, unknown=False, achievements=None):
        self.details = details or {
            "id": 1, "username": "zezima", "displayName": "Zezima",
            "type": "regular", "exp": 500, "country": "GB",
            "latestSnapshot": snapshot("2026-08-31T00:00:00.000Z",
                                       skills={"attack": (500, 40)}),
        }
        self.fail_update = fail_update
        self.fail_fetch = fail_fetch
        # Wise Old Man has never heard of the player: a GET is a 404 until
        # something POSTs them into existence.
        self.unknown = unknown
        self.snapshots = snapshots or []
        self.achievements = achievements or []
        self.calls = []

    def update_player(self, username):
        self.calls.append(("update", username))
        if self.fail_update:
            raise WomError("rate limited", 429)
        self.unknown = False
        return self.details

    def get_player(self, username):
        self.calls.append(("get", username))
        if self.unknown:
            raise WomError("Player not found.", 404)
        if self.fail_fetch:
            raise WomError("server error 502", 502)
        return self.details

    def get_achievements(self, username):
        self.calls.append(("achievements", username))
        return self.achievements

    def get_efficiency_rates(self, algorithm_type, metric):
        self.calls.append(("rates", algorithm_type, metric))
        return fixture("rates_ironman_{}.json".format(metric))

    def iter_snapshots_before(self, username, before=None, max_pages=None):
        """Pages as the API hands them: newest first, `before` inclusive, and
        as long as the updater's page size, which a test can shrink."""
        from wom import updater

        self.calls.append(("snapshots", username))
        size = updater.SNAPSHOT_PAGE_SIZE
        page = 0
        while max_pages is None or page < max_pages:
            held = sorted((s for s in self.snapshots
                           if before is None or s["createdAt"] <= before),
                          key=lambda s: s["createdAt"], reverse=True)[:size]
            page += 1
            if not held:
                return
            yield held
            oldest = held[-1]["createdAt"]
            if len(held) < size or (before is not None and oldest >= before):
                return
            before = oldest


class FakeHiscores:
    """Answers like wom.hiscores.HiscoresClient from canned payloads."""

    def __init__(self, payload=None, error=None, tables=None):
        self.payload = payload or fixture("player_a.json")
        self.error = error
        self.tables = tables or {}
        self.asked = []

    def fetch(self, username, table="hiscore_oldschool"):
        self.asked.append((username, table))
        if self.error is not None:
            raise self.error
        if table != "hiscore_oldschool":
            if table not in self.tables:
                raise HiscoresNotFound("not on " + table, 404)
            return self.tables[table]
        return self.payload

    def account_type(self, username, overall_exp):
        from wom.hiscores import HiscoresClient
        return HiscoresClient.account_type(self, username, overall_exp)


def test_a_new_player_is_introduced_through_wise_old_man_once(db):
    """Their id, type and history come from there; nothing else does."""
    client = FakeClient()
    result = update_one(client, db, "zezima", reader=FakeHiscores())
    assert result.ok
    assert db.player_by_username("zezima")["display_name"] == "Zezima"
    update_one(client, db, "zezima", reader=FakeHiscores())
    assert ("update", "zezima") not in client.calls, "a known player is not POSTed"
    assert client.calls.count(("get", "zezima")) == 1, "introduced once"


def test_only_a_player_wise_old_man_has_never_seen_is_posted(db):
    client = FakeClient(unknown=True)
    assert update_one(client, db, "zezima", reader=FakeHiscores()).ok
    assert [c[0] for c in client.calls][:2] == ["get", "update"]


def test_a_new_players_milestones_are_wise_old_mans_back_catalogue(db):
    client = FakeClient(achievements=[
        {"name": "99 Attack", "metric": "attack", "measure": "experience",
         "threshold": 13034431, "createdAt": "2026-08-01T00:00:00.000Z",
         "accuracy": 1},
    ])
    update_one(client, db, "zezima", reader=FakeHiscores())
    update_one(client, db, "zezima", reader=FakeHiscores())
    assert db.count_achievements() == 1
    assert client.calls.count(("achievements", "zezima")) == 1


def test_a_reading_comes_from_the_hiscores_and_keeps_what_they_cannot_say(db):
    """The hiscores know nothing about country or registration: a reading
    must not blank them the way an upsert of Wise Old Man's record would."""
    update_one(FakeClient(), db, "zezima", reader=FakeHiscores())
    row = db.player_by_username("zezima")
    assert row["country"] == "GB" and row["type"] == "regular"
    assert row["exp"] == fixture("player_a.json")["skills"][0]["xp"]
    assert row["combat_level"] and row["combat_level"] > 3
    assert db.snapshot_count(row["id"]) == 2, "Wise Old Man's, then ours"


def test_a_reading_that_changes_nothing_writes_no_rows(db):
    reader = FakeHiscores()
    update_one(FakeClient(), db, "zezima", reader=reader)
    rows = db.query_one("SELECT COUNT(*) AS n FROM metrics")["n"]
    update_one(FakeClient(), db, "zezima", reader=reader)
    assert db.query_one("SELECT COUNT(*) AS n FROM metrics")["n"] == rows


def test_efficiency_is_worked_out_from_the_stored_rates(db):
    client = FakeClient(details={
        "id": 1, "username": "zezima", "displayName": "Zezima",
        "type": "ironman"})
    update_all(client, db, ["zezima"], reader=FakeHiscores())
    expected = fixture("player_a.wom.json")
    row = db.player_by_username("zezima")
    assert abs(row["ehp"] - expected["ehp"]) < 1e-3
    assert abs(row["ehb"] - expected["ehb"]) < 1e-3
    assert ("rates", "ironman", "ehp") in client.calls


def test_rates_are_read_at_most_daily(db):
    client = FakeClient()
    update_all(client, db, ["zezima"], reader=FakeHiscores())
    update_all(client, db, ["zezima"], reader=FakeHiscores())
    assert sum(1 for c in client.calls if c[0] == "rates") == 2, \
        "ehp and ehb once, not again an hour later"


def test_a_milestone_crossed_between_readings_is_recorded(db):
    payload = fixture("player_a.json")
    lower = json.loads(json.dumps(payload))
    for row in lower["activities"]:
        if row["name"] == "Zulrah":
            row["score"] = 499
    update_one(FakeClient(), db, "zezima", reader=FakeHiscores(lower))
    result = update_one(FakeClient(), db, "zezima", reader=FakeHiscores(payload))
    assert result.milestones == 1
    row = db.query_one("SELECT * FROM achievements WHERE name='500 Zulrah kills'")
    assert row["metric"] == "zulrah" and row["measure"] == "kills"
    assert row["accuracy"] is not None and row["accuracy"] >= 0


def test_a_milestone_already_met_is_not_news(db):
    """A first reading crosses nothing; a back catalogue is Wise Old Man's."""
    reader = FakeHiscores()
    client = FakeClient(details={"id": 1, "username": "zezima",
                                 "displayName": "Zezima", "type": "regular"})
    assert update_one(client, db, "zezima", reader=reader).milestones == 0
    assert update_one(client, db, "zezima", reader=reader).milestones == 0


def test_a_name_off_the_hiscores_fails_rather_than_reading_stale_data(db):
    """A renamed account 404s. Wise Old Man's copy of the old name would
    read as current, so it is not used."""
    client = FakeClient()
    update_one(client, db, "zezima", reader=FakeHiscores())
    result = update_one(client, db, "zezima", reader=FakeHiscores(
        error=HiscoresNotFound("gone", 404)))
    assert not result.ok and "renamed" in result.message


def test_a_hiscores_outage_falls_back_to_wise_old_mans_copy(db):
    client = FakeClient()
    result = update_one(client, db, "zezima", reader=FakeHiscores(
        error=HiscoresError("the hiscores answered 503", 503)))
    assert result.ok and "Wise Old Man's copy" in result.message
    assert ("update", "zezima") not in client.calls, "a read, never an update"


def test_both_sources_failing_is_a_failure(db):
    client = FakeClient()
    update_one(client, db, "zezima", reader=FakeHiscores())
    client.fail_fetch = True
    result = update_one(client, db, "zezima", reader=FakeHiscores(
        error=HiscoresError("the hiscores answered 503", 503)))
    assert not result.ok


def test_the_daily_check_notices_an_account_type_change(db):
    payload = fixture("player_a.json")
    reader = FakeHiscores(tables={"hiscore_oldschool_ironman": payload})
    update_all(FakeClient(), db, ["zezima"], reader=reader, daily=True)
    assert db.player_by_username("zezima")["type"] == "ironman"


def test_an_iron_table_frozen_behind_the_account_is_not_believed(db):
    """A de-ironed account keeps its old row on the iron hiscores."""
    payload = fixture("player_a.json")
    frozen = json.loads(json.dumps(payload))
    frozen["skills"][0]["xp"] -= 1000
    reader = FakeHiscores(tables={"hiscore_oldschool_ironman": frozen})
    update_all(FakeClient(), db, ["zezima"], reader=reader, daily=True)
    assert db.player_by_username("zezima")["type"] == "regular"


def test_update_all_records_a_run_and_reports_progress(db):
    seen = []
    results = update_all(FakeClient(), db, ["zezima"], reader=FakeHiscores(),
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

    results = update_all(FakeClient(), db, ["zezima"], progress=explode,
                         reader=FakeHiscores())
    assert results[0].ok


def test_history_is_imported_once(db):
    client = FakeClient(snapshots=[
        snapshot("2026-01-01T00:00:00.000Z", skills={"attack": (10, 1)}),
        snapshot("2026-02-01T00:00:00.000Z", skills={"attack": (20, 2)}),
    ])
    first = update_one(client, db, "zezima")
    assert first.imported == 2
    second = update_one(client, db, "zezima")
    assert second.imported == 0, "backfill runs once, not on every pass"
    assert list(client.calls).count(("snapshots", "zezima")) == 1


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
    assert updater.place_sessions(db, ["zezima"]) > 0
    # Two: what had been earned by midnight, and the whole of it at the
    # moment the session closed.
    assert db.query_one("SELECT COUNT(*) AS n FROM snapshots"
                        " WHERE origin='derived'")["n"] == 2


def test_a_failure_toplace_sessions_never_breaks_the_run(db, monkeypatch):
    from wom import updater

    db.save_player_details({"id": 1, "username": "zezima",
                            "displayName": "Zezima", "type": "regular"})

    def explode(*_args, **_kwargs):
        raise RuntimeError("no")
    monkeypatch.setattr(updater.sessions, "attribute", explode)
    assert updater.place_sessions(db, ["zezima"]) == 0, "swallowed, not raised"


def test_placing_sessions_skips_a_name_we_have_never_stored(db):
    from wom import updater
    assert updater.place_sessions(db, ["nobody-here"]) == 0


def history(count, bosses=None):
    """`count` monthly readings that never move, oldest first."""
    return [snapshot("2025-{:02d}-01T00:00:00.000Z".format(month),
                     skills={"attack": (10, 1)}, bosses=bosses or {"zulrah": 5})
            for month in range(1, count + 1)]


def test_an_import_skips_what_did_not_move(db, monkeypatch):
    """Stored newest first, no reading had one before it to be compared
    with, and every metric was written for every snapshot. Now only the
    oldest reading of each page starts from nothing."""
    from wom import updater
    monkeypatch.setattr(updater, "SNAPSHOT_PAGE_SIZE", 3)
    client = FakeClient(snapshots=history(6))
    assert update_one(client, db, "zezima").imported == 6
    zulrah = db.query_one("SELECT COUNT(*) AS n FROM metrics"
                          " WHERE metric='zulrah'")["n"]
    # Pages of three that overlap by one: one row for each page's oldest.
    assert zulrah == 3, "unchanged readings were written again"


def test_an_import_says_how_far_it_has_got(db, monkeypatch):
    """From the admin page a silent import looked hung. The last request,
    which only hands back the reading the page before ended on, says nothing:
    it got no further back."""
    from wom import updater
    monkeypatch.setattr(updater, "SNAPSHOT_PAGE_SIZE", 3)
    heard = []
    update_one(FakeClient(snapshots=history(5)), db, "zezima", say=heard.append)
    assert heard == ["zezima: importing history, back to 2025-03-01",
                     "zezima: importing history, back to 2025-01-01"]


def test_a_history_import_cut_short_keeps_its_pages_and_tries_again(db):
    """A page refused part way used to throw away the pages already read."""
    class CutShort(FakeClient):
        def iter_snapshots_before(self, username, **kwargs):
            self.calls.append(("snapshots", username))
            yield history(2)[::-1]
            raise WomError("server error 502", 502)

    result = update_one(CutShort(), db, "zezima")
    assert result.ok and "history unavailable" in result.message
    assert result.imported == 2
    assert db.needs_backfill(1), "not marked done, so the next pass resumes"


def test_storing_a_reading_does_not_read_the_whole_account(db, player):
    """Both lookups a save makes used to scan every row the account held,
    which made an import quadratic: a celebrity's history wedged the site."""
    conn = db.connect()
    plans = [
        conn.execute("EXPLAIN QUERY PLAN UPDATE snapshots SET payload=''"
                     " WHERE player_id=1 AND id<>2 AND payload<>''").fetchall(),
    ]
    from wom.store.snapshots import SnapshotStore

    captured = []
    real = conn.execute

    class Spy:
        def execute(self, sql, params=()):
            captured.append((sql, params))
            return real(sql, params)

    SnapshotStore._state_before(Spy(), 1, "2026-01-01T00:00:00.000Z",
                                [("skill", "attack"), ("boss", "zulrah")])
    sql, params = captured[0]
    plans.append(conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall())
    details = [row["detail"] for plan in plans for row in plan]
    assert any("idx_snapshots_payload" in d for d in details), details
    assert not any(d.startswith("SCAN m") or d.startswith("SCAN metrics")
                   for d in details), details
