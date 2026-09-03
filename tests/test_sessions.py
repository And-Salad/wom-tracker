"""Which stretch of time a gain was earned in, and what happens without Dink.

Every test here is the same shape: a gain was noticed at one reading and not
at the one before it, and the question is what span it should be attributed
to. The two that matter most are the four-hour session that crosses several
readings without moving any of them, and every case where nothing is known
and the answer has to be exactly what the app did before.
"""

from datetime import datetime, timedelta, timezone

from wom.sessions import INFERRED, MEASURED, Span, resolve


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


def test_events_outside_the_window_are_not_read(db):
    from wom.sessions import events_for
    db.record_session_event("zezima", "login", {"total_exp": None}, {},
                            when="2026-09-01T19:00:00.000000Z")
    events = events_for(db, "zezima", "2026-09-03T00:00:00.000Z",
                        "2026-09-04T00:00:00.000Z")
    assert events == []
