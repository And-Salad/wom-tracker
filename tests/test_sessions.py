"""Which stretch of time a gain was earned in, and what happens without Dink.

Every test here is the same shape: a gain was noticed at one reading and not
at the one before it, and the question is what span it should be attributed
to. The two that matter most are the four-hour session that crosses several
readings without moving any of them, and every case where nothing is known
and the answer has to be exactly what the app did before.
"""

from datetime import datetime, timedelta, timezone

from conftest import as_polled, snapshot
from wom.sessions import (
    INFERRED, MEASURED, Span, resolve)


def at(hour, minute=0):
    return datetime(2026, 9, 3, hour, minute, tzinfo=timezone.utc)


def login(hour, minute=0):
    return ("login", at(hour, minute))


def logout(hour, minute=0):
    return ("logout", at(hour, minute))


# -- with Dink ------------------------------------------------------------

def test_both_ends_come_from_the_plugin_when_it_reported_both():
    span = resolve(at(20, 50), at(21, 0), [login(20, 52), logout(20, 58)])
    assert span == Span(at(20, 52), at(20, 58), MEASURED, MEASURED)
    assert span.measured


def test_a_long_session_reaches_back_past_readings_that_saw_nothing():
    """The case the whole exercise exists for.

    Someone logs in at 19:00 and trains until 22:58. Our readings at 20:00,
    21:00 and 22:00 show no gain at all, because the hiscores do not move
    while they are logged in. The gain lands at 23:00, and without the login
    it would be attributed to the ten minutes before it.
    """
    span = resolve(at(22, 50), at(23, 0), [login(19), logout(22, 58)])
    assert span.start == at(19), "four hours, not ten minutes"
    assert span.end == at(22, 58)
    assert span.measured
    assert span.seconds == 14280, "three hours fifty-eight, measured at both ends"


def test_a_login_with_no_logout_yet_ends_at_our_reading():
    span = resolve(at(20, 50), at(21, 0), [login(20, 52)])
    assert span == Span(at(20, 52), at(21, 0), MEASURED, INFERRED)
    assert not span.measured, "one end is still only bracketed"


def test_a_logout_with_no_login_sharpens_only_the_end():
    """Half of Dink is better than none: the end stops being a bracket."""
    span = resolve(at(20, 50), at(21, 0), [logout(20, 58)])
    assert span == Span(at(20, 50), at(20, 58), INFERRED, MEASURED)


def test_two_sessions_in_one_interval_cover_both():
    """The gain includes both, so the span has to as well.

    Taking the later login would date four hours of training from the last
    twenty minutes of it.
    """
    span = resolve(at(18), at(21), [login(18, 10), logout(19), login(20),
                                    logout(20, 40)])
    assert span.start == at(18, 10), "the first login, not the last"
    assert span.end == at(20, 40), "the last logout, not the first"


def test_a_closed_session_before_the_window_is_not_reopened():
    """They logged out before the previous reading, so that gain was already
    counted. This one belongs to whatever happened after it."""
    span = resolve(at(20, 50), at(21, 0), [login(15), logout(16)])
    assert span == Span(at(20, 50), at(21, 0), INFERRED, INFERRED)


def test_a_login_after_the_reading_belongs_to_a_later_gain():
    span = resolve(at(20, 50), at(21, 0), [login(21, 30)])
    assert span == Span(at(20, 50), at(21, 0), INFERRED, INFERRED)


def test_a_client_left_running_is_not_a_sixteen_hour_session():
    """A login we never saw closed stops being evidence, or one gain would
    smear across two days."""
    span = resolve(at(20, 50), at(21, 0), [("login", at(21) - timedelta(hours=40))])
    assert span == Span(at(20, 50), at(21, 0), INFERRED, INFERRED)


def test_the_cutoff_is_a_boundary_not_a_vibe():
    events = [("login", at(21) - timedelta(hours=15, minutes=59))]
    assert resolve(at(20, 50), at(21), events).start != at(20, 50), "inside 16h"
    events = [("login", at(21) - timedelta(hours=16, minutes=1))]
    assert resolve(at(20, 50), at(21), events).start == at(20, 50), "outside 16h"


def test_events_may_arrive_in_any_order():
    jumbled = [logout(22, 58), login(19), logout(16), login(15)]
    assert resolve(at(22, 50), at(23), jumbled).start == at(19)


def test_an_event_with_no_timestamp_is_ignored_rather_than_fatal():
    span = resolve(at(20, 50), at(21), [("login", None), logout(20, 58)])
    assert span == Span(at(20, 50), at(20, 58), INFERRED, MEASURED)


# -- without Dink, which is most accounts for now -------------------------

def test_no_events_at_all_is_exactly_what_the_app_did_before():
    """The fallback has to be the old behaviour, not an approximation of it:
    accounts without the plugin must keep reporting what they reported."""
    span = resolve(at(20, 50), at(21, 0), [])
    assert span == Span(at(20, 50), at(21, 0), INFERRED, INFERRED)
    assert not span.measured


def test_a_span_never_starts_after_it_ends():
    """A logout recorded before the login that supposedly opened it - clock
    skew, or a retry landing out of order - must not invert the span."""
    span = resolve(at(20, 50), at(21, 0), [login(20, 59), logout(20, 52)])
    assert span.start < span.end
    assert span == Span(at(20, 50), at(20, 52), INFERRED, MEASURED)


def test_the_fallback_is_used_when_the_login_equals_the_end():
    span = resolve(at(20, 50), at(21, 0), [login(20, 58), logout(20, 58)])
    assert span == Span(at(20, 50), at(20, 58), INFERRED, MEASURED)


def test_a_span_says_what_it_is_when_a_test_fails():
    """pytest prints this on every failure in this file, so it has to name
    both ends and where each came from."""
    text = repr(resolve(at(20, 50), at(21), [logout(20, 58)]))
    assert "20:50" in text and "20:58" in text
    assert INFERRED in text and MEASURED in text


# -- reading the events back out of the database --------------------------

def test_events_are_read_back_in_a_shape_resolve_accepts(db):
    from wom.sessions import events_for
    for kind, stamp in (("login", "2026-09-03T19:00:00.000000Z"),
                        ("logout", "2026-09-03T22:58:00.000000Z")):
        db.record_session_event("zezima", kind, {"total_exp": None}, {},
                                when=stamp)
    events = events_for(db, "zezima", "2026-09-03T00:00:00.000Z",
                        "2026-09-04T00:00:00.000Z")
    span = resolve(at(22, 50), at(23), events)
    assert span.start == at(19), "the stored login has to survive the round trip"
    assert span.measured


def test_a_retried_delivery_is_placed_when_it_happened(db):
    """Dink retries what it could not deliver. A session resolved from the
    arrival time would then be shortened by however long the retry took."""
    from wom.sessions import events_for
    db.record_session_event("zezima", "login", {"total_exp": None}, {},
                            when="2026-09-03T19:40:00.000000Z",
                            happened_at="2026-09-03T19:00:00.000000Z")
    events = events_for(db, "zezima", "2026-09-03T00:00:00.000Z",
                        "2026-09-04T00:00:00.000Z")
    assert events == [("login", at(19))], "the moment, not the arrival"


def test_events_outside_the_window_are_not_read(db):
    from wom.sessions import events_for
    db.record_session_event("zezima", "login", {"total_exp": None}, {},
                            when="2026-09-01T19:00:00.000000Z")
    events = events_for(db, "zezima", "2026-09-03T00:00:00.000Z",
                        "2026-09-04T00:00:00.000Z")
    assert events == []


# -- crediting a gain to the day it was earned on --------------------------

def zone():
    from zoneinfo import ZoneInfo
    return ZoneInfo("America/New_York")


def test_a_session_that_stays_inside_one_day_crosses_nothing():
    from wom.sessions import boundary_in
    span = Span(at(14), at(17), MEASURED, MEASURED)
    assert boundary_in(span, zone()) is None


def test_a_session_across_local_midnight_finds_it():
    from wom.sessions import boundary_in, share_before
    # 21:00 to 01:00 New York is 01:00 to 05:00 UTC the next day
    span = Span(at(1), at(5), MEASURED, MEASURED)
    crossing = boundary_in(span, zone())
    assert crossing == at(4), "midnight in New York, in UTC"
    assert share_before(span, crossing) == 0.75


def test_interpolation_only_reports_what_moved():
    from wom.sessions import interpolate
    out = interpolate({"attack": 100.0, "magic": 50.0},
                      {"attack": 500.0, "magic": 50.0}, 0.25)
    assert out == {"attack": 200.0}, "magic did not move, so it says nothing"


def test_whole_kinds_are_not_split_into_fractions():
    from wom.sessions import interpolate
    out = interpolate({"zulrah": 0.0}, {"zulrah": 3.0}, 0.5, whole=True)
    assert out == {"zulrah": 2.0}, "no two thirds of a boss kill"


def test_a_crossing_session_is_credited_to_both_days(db, player):
    """The case this all exists for.

    Three quarters of the session happened before midnight, so three quarters
    of the experience belongs to the day before.
    """
    from wom.sessions import attribute
    db.save_snapshot(player["id"], snapshot("2026-09-04T00:50:00.000Z",
                                            skills={"attack": (1000, 40)}))
    db.save_snapshot(player["id"], snapshot("2026-09-04T05:10:00.000Z",
                                            skills={"attack": (401000, 60)}))
    for kind, when in (("login", "2026-09-04T01:00:00.000000Z"),
                       ("logout", "2026-09-04T05:00:00.000000Z")):
        db.record_session_event(player["username"], kind, {"total_exp": None},
                                {}, when=when)

    assert attribute(db, zone(), player, "2026-09-01") > 0
    midnight = "2026-09-04T04:00:00.000000Z"          # local midnight, in UTC
    standing = {r["metric"]: r["value"]
                for r in db.state_at(player["id"], midnight, "skill")}
    assert standing["attack"] == 301000.0, "three quarters of 400,000 gained"


def test_an_account_without_the_plugin_is_left_completely_alone(db, player):
    """The fallback, asserted on the storage rather than the rule."""
    from wom.sessions import attribute
    db.save_snapshot(player["id"], snapshot("2026-09-04T00:50:00.000Z",
                                            skills={"attack": (1000, 40)}))
    db.save_snapshot(player["id"], snapshot("2026-09-04T05:10:00.000Z",
                                            skills={"attack": (401000, 60)}))
    before = db.query("SELECT * FROM metrics ORDER BY captured_at, metric")

    assert attribute(db, zone(), player, "2026-09-01") == 0
    after = db.query("SELECT * FROM metrics ORDER BY captured_at, metric")
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    assert db.query_one("SELECT COUNT(*) AS n FROM snapshots"
                        " WHERE origin='derived'")["n"] == 0


def test_running_it_twice_does_not_double_the_correction(db, player):
    from wom.sessions import attribute
    db.save_snapshot(player["id"], snapshot("2026-09-04T00:50:00.000Z",
                                            skills={"attack": (1000, 40)}))
    db.save_snapshot(player["id"], snapshot("2026-09-04T05:10:00.000Z",
                                            skills={"attack": (401000, 60)}))
    for kind, when in (("login", "2026-09-04T01:00:00.000000Z"),
                       ("logout", "2026-09-04T05:00:00.000000Z")):
        db.record_session_event(player["username"], kind, {"total_exp": None},
                                {}, when=when)
    attribute(db, zone(), player, "2026-09-01")
    first = db.query("SELECT * FROM metrics")
    attribute(db, zone(), player, "2026-09-01")
    assert len(db.query("SELECT * FROM metrics")) == len(first)


def test_a_late_logout_corrects_rather_than_accumulates(db, player):
    """The rule will change, and a correction nobody can withdraw is worse
    than no correction."""
    from wom.sessions import attribute
    db.save_snapshot(player["id"], snapshot("2026-09-04T00:50:00.000Z",
                                            skills={"attack": (1000, 40)}))
    db.save_snapshot(player["id"], snapshot("2026-09-04T05:10:00.000Z",
                                            skills={"attack": (401000, 60)}))
    db.record_session_event(player["username"], "login", {"total_exp": None},
                            {}, when="2026-09-04T01:00:00.000000Z")
    attribute(db, zone(), player, "2026-09-01")

    db.record_session_event(player["username"], "logout", {"total_exp": None},
                            {}, when="2026-09-04T05:00:00.000000Z")
    attribute(db, zone(), player, "2026-09-01")
    midnight = "2026-09-04T04:00:00.000000Z"
    standing = {r["metric"]: r["value"]
                for r in db.state_at(player["id"], midnight, "skill")}
    assert standing["attack"] == 301000.0, "recomputed against the real end"


def test_compaction_keeps_an_interpolated_reading(db, player):
    """Thinning it would silently undo the correction."""
    db.record_derived_state(player["id"], "2026-01-05T05:00:00.000Z",
                            [("skill", "attack", 500.0)])
    db.save_snapshot(player["id"], snapshot("2026-01-05T23:00:00.000Z",
                                            skills={"attack": (900, 40)}))
    as_polled(db)
    conn = db.connect()
    with conn:
        conn.execute("UPDATE snapshots SET origin='derived'"
                     " WHERE captured_at LIKE '2026-01-05T05%'")
    db.compact_snapshots(keep_days=30)
    kept = [r["captured_at"] for r in db.query(
        "SELECT captured_at FROM snapshots WHERE captured_at < '2026-02-01'"
        " ORDER BY captured_at")]
    assert "2026-01-05T05:00:00.000Z" in kept


def test_a_zero_length_span_does_not_divide_by_it():
    from wom.sessions import share_before
    assert share_before(Span(at(3), at(3), MEASURED, MEASURED), at(3)) == 0.0


def test_a_metric_with_no_value_is_skipped():
    from wom.sessions import interpolate
    assert interpolate({"attack": 1.0}, {"attack": None, "magic": None}, 0.5) == {}


def _played(db, player, gain_at, events):
    db.save_snapshot(player["id"], snapshot("2026-09-04T00:50:00.000Z",
                                            skills={"attack": (1000, 40)}))
    db.save_snapshot(player["id"], snapshot("2026-09-04T02:00:00.000Z",
                                            skills={"attack": (1000, 40)}))
    db.save_snapshot(player["id"], snapshot(gain_at, skills={"attack": (401000, 60)}))
    for kind, when in events:
        db.record_session_event(player["username"], kind, {"total_exp": None},
                                {}, when=when)


def test_a_reading_that_moved_nothing_is_stepped_over(db, player):
    """The 02:00 reading repeats 00:50 exactly, so there is nothing to place."""
    from wom.sessions import attribute
    _played(db, player, "2026-09-04T05:10:00.000Z",
            [("login", "2026-09-04T01:00:00.000000Z"),
             ("logout", "2026-09-04T05:00:00.000000Z")])
    assert attribute(db, zone(), player, "2026-09-01") > 0


def test_a_gain_with_no_events_near_it_is_left_alone(db, player):
    """The account runs Dink, but nothing was reported around this gain.

    It must fall back rather than half-guess, or an account would be corrected
    only sometimes and nobody could say when.
    """
    from wom.sessions import attribute
    _played(db, player, "2026-09-04T05:10:00.000Z",
            [("login", "2026-08-20T01:00:00.000000Z")])
    assert attribute(db, zone(), player, "2026-09-01") == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM snapshots"
                        " WHERE origin='derived'")["n"] == 0


def test_a_session_inside_one_day_is_not_split(db, player):
    """Nothing crosses, so nothing is written - the gain already sits on the
    right day."""
    from wom.sessions import attribute
    _played(db, player, "2026-09-04T20:10:00.000Z",
            [("login", "2026-09-04T17:00:00.000000Z"),
             ("logout", "2026-09-04T20:00:00.000000Z")])
    assert attribute(db, zone(), player, "2026-09-01") == 0


def test_a_crossing_session_that_gained_nothing_writes_nothing(db, player):
    """A reading can move a metric we do not interpolate. There is then a
    boundary and no correction to make, and an empty derived reading would be
    a row saying nothing."""
    from wom.sessions import attribute
    db.save_snapshot(player["id"], snapshot("2026-09-04T00:50:00.000Z",
                                            skills={"attack": (1000, 40)}))
    db.save_snapshot(player["id"], snapshot("2026-09-04T05:10:00.000Z",
                                            skills={"attack": (1000, 41)}))
    for kind, when in (("login", "2026-09-04T01:00:00.000000Z"),
                       ("logout", "2026-09-04T05:00:00.000000Z")):
        db.record_session_event(player["username"], kind, {"total_exp": None},
                                {}, when=when)
    assert attribute(db, zone(), player, "2026-09-01") == 0
