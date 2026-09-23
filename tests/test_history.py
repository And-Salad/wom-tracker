"""Importing whole histories, and keeping a celebrity's small.

A friend's import walks back a few pages a run; a celebrity's goes all the
way in one, thinned to a reading a day past the recent month as it lands.
"""

from datetime import datetime, timedelta, timezone

from conftest import before_migration, snapshot
from test_api_client import FakeResponse, _client
from test_updater import FakeClient

from wom import updater
from wom.store.migrations import STEPS
from wom.updater import backfill_player

REOPEN_HISTORY_IMPORTS = next(n for n, step in STEPS
                              if step.__name__ == "_reopen_history_imports")


def stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def old_days(days, per_day, start=60):
    """`per_day` readings a day for `days` days ending `start` days ago,
    oldest first - all well past the window compaction leaves alone."""
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                             microsecond=0)
    out = []
    for day in range(start + days - 1, start - 1, -1):
        for n in range(per_day):
            when = now - timedelta(days=day) + timedelta(hours=1 + n)
            out.append(snapshot(stamp(when), bosses={"zulrah": day * 100 + n}))
    return out


def stored(db, player_id=1):
    return db.query_one("SELECT COUNT(*) AS n FROM snapshots WHERE player_id=?",
                        (player_id,))["n"]


def test_an_import_walks_back_a_few_pages_a_run_until_there_is_no_more(
        db, player, monkeypatch):
    """One pass used to stop at five thousand snapshots for good - seven
    weeks of a celebrity. Now it stops for the run, and carries on."""
    monkeypatch.setattr(updater, "SNAPSHOT_PAGE_SIZE", 3)
    client = FakeClient(snapshots=old_days(4, 3))          # twelve readings
    backfill_player(client, db, "zezima", 1, pages=2)
    assert db.needs_backfill(1), "two pages is not all of it"
    first = stored(db)
    assert 0 < first < 12

    for _run in range(5):
        backfill_player(client, db, "zezima", 1, pages=2)
    assert stored(db) == 12, "every reading, eventually"
    assert not db.needs_backfill(1), "and then it stops asking"


def test_a_celebritys_history_is_thinned_as_it_lands(db, player, monkeypatch):
    """Theirs arrive as archive readings, which compaction would otherwise
    keep for ever. Past the recent month, one a day is plenty."""
    monkeypatch.setattr(updater, "SNAPSHOT_PAGE_SIZE", 4)
    client = FakeClient(snapshots=old_days(5, 6))
    backfill_player(client, db, "zezima", 1, thin=True, pages=None)
    assert stored(db) == 5, "one reading for each of the five days"
    assert not db.needs_backfill(1)


def test_a_friends_history_is_kept_whole(db, player, monkeypatch):
    """The archive readings are theirs to keep: they place sessions."""
    monkeypatch.setattr(updater, "SNAPSHOT_PAGE_SIZE", 4)
    backfill_player(FakeClient(snapshots=old_days(5, 6)), db, "zezima", 1,
                    pages=None)
    assert stored(db) == 30


def test_a_celebritys_import_goes_all_the_way_in_one_pass(db, player,
                                                         monkeypatch):
    """A friend's is a page or two and capped per run; a celebrity's is the
    long one, and trickling it in ten pages a run took most of a day."""
    monkeypatch.setattr(updater, "SNAPSHOT_PAGE_SIZE", 3)
    client = FakeClient(snapshots=old_days(20, 3))         # twenty pages
    backfill_player(client, db, "zezima", 1, thin=True, pages=2)
    assert not db.needs_backfill(1), "finished in the one pass"
    assert stored(db) == 20


def test_a_busy_day_cannot_hold_an_import_in_place(db, player, monkeypatch):
    """An import cut short resumes next run. Resuming from the oldest reading
    held, a day busier than a page was thinned back to its last reading and
    fetched again, for ever. The resume point is kept apart for this."""
    from wom.api import WomError

    monkeypatch.setattr(updater, "SNAPSHOT_PAGE_SIZE", 3)

    class Flaky(FakeClient):
        """Refuses the second page of every pass, where there is one."""
        def iter_snapshots_before(self, username, **kwargs):
            pages = super().iter_snapshots_before(username, **kwargs)
            yield next(pages)
            if next(pages, None) is not None:
                raise WomError("server error 502", 502)

    client = Flaky(snapshots=old_days(2, 10))              # ten a day
    for _run in range(20):
        backfill_player(client, db, "zezima", 1, thin=True)
        if not db.needs_backfill(1):
            break
    assert not db.needs_backfill(1), "stuck on the busy day"
    assert stored(db) == 2


def test_the_nightly_pass_thins_celebrities_and_nobody_else(db):
    for pid, name in ((1, "friend"), (2, "celeb")):
        db.save_player_details({"id": pid, "username": name, "displayName": name})
        for reading in old_days(3, 4):
            db.save_snapshot(pid, reading)
    db.compact_snapshots(keep_days=30, thin=[2])
    assert stored(db, 1) == 12, "a friend's archive readings are kept"
    assert stored(db, 2) == 3, "a celebrity's are one a day"


def test_a_thinned_celebrity_still_reads_what_each_day_ended_on(db, player):
    for reading in old_days(3, 4):
        db.save_snapshot(1, reading)
    last = [r["captured_at"] for r in db.query(
        "SELECT MAX(captured_at) AS captured_at FROM snapshots"
        " GROUP BY substr(captured_at, 1, 10)")]
    before = [{r["metric"]: r["value"] for r in db.state_at(1, at, kind="boss")}
              for at in last]
    db.compact_snapshots(keep_days=30, thin=[1])
    after = [{r["metric"]: r["value"] for r in db.state_at(1, at, kind="boss")}
             for at in last]
    assert after == before


def test_finished_imports_are_reopened_from_the_oldest_reading_held(db, player):
    """They stopped at the old cap. Reopened from where they are, so an
    account that is already whole finishes on its first request."""
    db.save_snapshot(1, snapshot("2026-01-05T03:00:00.000Z",
                                 skills={"attack": (100, 40)}))
    db.save_snapshot(1, snapshot("2026-03-05T03:00:00.000Z",
                                 skills={"attack": (200, 41)}))
    db.mark_backfilled(1)
    before_migration(db, REOPEN_HISTORY_IMPORTS)
    from wom.db import Database
    reopened = Database(db.path)
    assert reopened.needs_backfill(1)
    assert reopened.backfill_before(1) == "2026-01-05T03:00:00.000Z"


def page(*stamps):
    return FakeResponse(payload=[{"createdAt": s, "data": {}} for s in stamps])


def test_the_client_pages_back_by_date(monkeypatch):
    """By date, because an offset counts from the newest reading and moves
    every time one arrives. endDate is inclusive, so each page after the
    first asks from the one the last ended on."""
    monkeypatch.setattr("wom.api.SNAPSHOT_PAGE_SIZE", 2)
    client = _client([page("2026-03-03T00:00:00.000Z", "2026-03-02T00:00:00.000Z"),
                      page("2026-03-02T00:00:00.000Z", "2026-03-01T00:00:00.000Z"),
                      page("2026-03-01T00:00:00.000Z")])
    got = list(client.iter_snapshots_before("zezima",
                                            before="2026-03-03T00:00:00.000Z"))
    assert len(got) == 3
    asked = [call["params"]["endDate"] for call in client.session.calls]
    assert asked == ["2026-03-03T00:00:00.000Z", "2026-03-02T00:00:00.000Z",
                     "2026-03-01T00:00:00.000Z"]


def test_the_client_stops_where_a_page_gets_no_further_back(monkeypatch):
    """A full page that ends where it started would be asked for again, for
    ever. Two readings at one moment cannot happen, but a loop that relies
    on it not happening is a loop."""
    monkeypatch.setattr("wom.api.SNAPSHOT_PAGE_SIZE", 2)
    same = "2026-03-01T00:00:00.000Z"
    client = _client([page(same, same), page(same, same)])
    assert len(list(client.iter_snapshots_before("zezima", before=same))) == 1
    assert len(client.session.calls) == 1


def test_the_client_stops_after_its_pages(monkeypatch):
    monkeypatch.setattr("wom.api.SNAPSHOT_PAGE_SIZE", 1)
    client = _client([page("2026-03-0{}T00:00:00.000Z".format(9 - n))
                      for n in range(5)])
    assert len(list(client.iter_snapshots_before("zezima", max_pages=2))) == 2


def test_a_celebrity_cut_short_by_the_old_cap_carries_on_from_there(
        db, player, monkeypatch):
    """What the live database holds: the newest stretch of a celebrity's
    history, dense, marked done by the import that stopped at the cap.
    Reopened, the import carries on from the oldest reading held rather
    than starting again - and what was already held is thinned as well."""
    monkeypatch.setattr(updater, "SNAPSHOT_PAGE_SIZE", 4)
    everything = old_days(6, 5)                 # six days, five a day
    for reading in everything[-10:]:            # the newest two days, whole
        db.save_snapshot(1, reading)
    db.mark_backfilled(1)
    before_migration(db, REOPEN_HISTORY_IMPORTS)
    from wom.db import Database
    live = Database(db.path)

    client = FakeClient(snapshots=everything)
    asked = []
    real = client.iter_snapshots_before

    def watched(username, before=None, **kwargs):
        asked.append(before)
        return real(username, before=before, **kwargs)
    client.iter_snapshots_before = watched

    backfill_player(client, live, "zezima", 1, thin=True)
    assert asked == [everything[-10]["createdAt"]], "resumed, not restarted"
    assert not live.needs_backfill(1)
    assert stored(live) == 6, "all six days, one reading each"
