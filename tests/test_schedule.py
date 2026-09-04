"""Calendar windows, and which summaries a given morning owes."""

from datetime import datetime, timedelta

import pytest
from conftest import as_polled, snapshot

from wom import periods, summaries, winners
from wom.scheduler import zone


def at(year, month, day, hour):
    return datetime(year, month, day, hour, 0, tzinfo=zone())


def written(db, player, now, keys):
    """Pretend those periods were fully written for `now`'s windows.

    Group recaps only for the windows the leaderboard judges, because that is
    all the scheduler ever writes and all the migration leaves behind. Writing
    one per period made every test here run against a database shape that
    cannot exist - and that is not a tidiness point: `_missing` asked for a
    weekly group recap, never found one, and so re-owed every player's weekly
    note on every run. The fixture had manufactured the very rows whose
    absence was the bug.
    """
    for key in keys:
        window = periods.latest_window(key, now)
        db.save_summary(player["id"], window, "text", "hash")
        if key in periods.GROUP_PERIODS:
            # One per board, which is what a run writes: a window with only
            # one of them written is still owed, and a fixture that wrote one
            # would be testing a database shape that cannot occur.
            for board in winners.BOARDS:
                db.save_group_summary(window, "text", "hash", board=board)


def test_windows_are_the_last_complete_one(db):
    tuesday = at(2026, 9, 8, 6)
    assert periods.latest_window("day", tuesday).label == "Monday 07 September 2026"
    # Weeks run Monday to Sunday, so on a Tuesday the last whole one ended
    # yesterday morning.
    assert periods.latest_window("week", tuesday).key == "2026-08-31"
    assert periods.latest_window("month", tuesday).label == "August 2026"


def test_a_window_offset_steps_further_back(db):
    now = at(2026, 9, 8, 6)
    assert periods.latest_window("day", now, offset=1).label.startswith("Sunday 06")


def test_quarters_and_years_are_calendar_windows():
    now = at(2026, 8, 31, 6)
    quarter = periods.latest_window("quarter", now)
    assert quarter.label == "Q2 2026"
    assert (quarter.start.month, quarter.end.month) == (4, 7), "April to June"

    year = periods.latest_window("year", now)
    assert year.label == "2025"
    assert year.start.year == 2025 and year.end.year == 2026


def test_quarter_and_year_offsets_cross_boundaries():
    now = at(2026, 8, 31, 6)
    assert [periods.latest_window("quarter", now, offset=n).label
            for n in range(3)] == ["Q2 2026", "Q1 2026", "Q4 2025"]
    assert [periods.latest_window("year", now, offset=n).label
            for n in range(3)] == ["2025", "2024", "2023"]


def test_a_quarter_is_found_from_any_month_inside_it():
    for month, expected in ((1, "Q4 2025"), (3, "Q4 2025"), (4, "Q1 2026"),
                            (6, "Q1 2026"), (7, "Q2 2026"), (12, "Q3 2026")):
        got = periods.latest_window("quarter", at(2026, month, 15, 6)).label
        assert got == expected, "{} -> {} (wanted {})".format(month, got, expected)


def test_an_unknown_period_still_has_no_window():
    with pytest.raises(ValueError):
        periods.latest_window("fortnight")


def test_a_window_is_owed_from_the_moment_it_closes(db):
    """These used to wait for six in the morning, which was the first update
    slot after midnight when there were four a day. There are now one hundred
    and forty four, so the first one after the window closes is minutes old."""
    assert summaries.due_periods(db, at(2026, 9, 8, 0)) == [
        "day", "week", "month", "quarter", "year"]
    assert summaries.due_periods(db, at(2026, 9, 8, 5)) == [
        "day", "week", "month", "quarter", "year"]


def test_a_settled_morning_owes_nothing(db, player):
    now = at(2026, 9, 8, 6)
    written(db, player, now, periods.SUMMARY_PERIODS)
    assert summaries.due_periods(db, at(2026, 9, 8, 8)) == []


def test_a_quarter_is_owed_once_and_not_again(db, player):
    """It is written on the first morning after the quarter closes."""
    now = at(2026, 8, 31, 6)
    assert "quarter" in summaries.due_periods(db, now)
    written(db, player, now, ("quarter",))
    assert "quarter" not in summaries.due_periods(db, now)
    # ...and stays written until the next quarter closes.
    assert "quarter" not in summaries.due_periods(db, at(2026, 9, 30, 6))
    assert "quarter" in summaries.due_periods(db, at(2026, 10, 1, 6))


def test_a_year_is_owed_once_and_not_again(db, player):
    now = at(2026, 8, 31, 6)
    assert "year" in summaries.due_periods(db, now)
    written(db, player, now, ("year",))
    assert "year" not in summaries.due_periods(db, now)
    assert "year" in summaries.due_periods(db, at(2027, 1, 1, 6))


def test_monday_adds_the_week_and_the_first_adds_the_month(db, player):
    """Once everything is settled, only what has just closed comes due."""
    written(db, player, at(2026, 9, 13, 6), periods.SUMMARY_PERIODS)
    assert summaries.due_periods(db, at(2026, 9, 14, 6)) == ["day", "week"]

    written(db, player, at(2026, 9, 30, 6), periods.SUMMARY_PERIODS)
    # The 1st of October closes a quarter as well as a month.
    assert summaries.due_periods(db, at(2026, 10, 1, 6)) == [
        "day", "month", "quarter"]


def test_a_missed_monday_is_caught_up_on_tuesday(db, player):
    """The machine being asleep on Monday must not lose that week."""
    written(db, player, at(2026, 9, 13, 6), periods.SUMMARY_PERIODS)
    assert "week" in summaries.due_periods(db, at(2026, 9, 15, 6))


def test_a_missing_group_round_up_reopens_the_window(db, player):
    """And every board owes one. A window with one written and the other not
    is still owed, or the second board would never get a round-up at all."""
    now = at(2026, 9, 8, 6)
    window = periods.latest_window("day", now)
    db.save_summary(player["id"], window, "text", "hash")
    assert "day" in summaries.due_periods(db, now)

    db.save_group_summary(window, "text", "hash", board=winners.MAXING)
    assert "day" in summaries.due_periods(db, now), "grinding still owes one"

    db.save_group_summary(window, "text", "hash", board=winners.GRINDING)
    assert "day" not in summaries.due_periods(db, now)


def test_no_window_is_ever_owed_twice(db, player):
    """A year of mornings, each writing what it was asked for.

    The count used to be asserted as "between 420 and 450", which is a range
    wide enough to hide an off-by-one and does not say what it is protecting.
    What matters is that nothing comes due a second time: a window that stays
    owed after it has been written is re-billed on every run for ever.
    """
    day = at(2026, 1, 1, 6)
    seen = set()
    for _ in range(365):
        for key in summaries.due_periods(db, day):
            window = periods.latest_window(key, day)
            assert (key, window.key) not in seen, (
                "{} {} came due again after it was written".format(key, window.key))
            seen.add((key, window.key))
        written(db, player, day, summaries.due_periods(db, day))
        day += timedelta(days=1)
    # Spelled out per period rather than as one total: a single number says
    # nothing about which period drifted, and a range says nothing at all.
    counted = {}
    for key, _window in seen:
        counted[key] = counted.get(key, 0) + 1
    assert counted == {"day": 365, "week": 53, "month": 12,
                       "quarter": 4, "year": 1}, counted


def test_summaries_are_skipped_when_the_feature_is_off(db, config, player):
    config["summaries_enabled"] = False
    assert summaries.maybe_write_summaries(db, config, at(2026, 9, 8, 6)) == 0


def test_coverage_says_when_a_window_was_not_measured(db, config, player):
    """Zeroes from a window nobody watched must not read as inactivity."""
    window = periods.latest_window("day", at(2026, 9, 8, 6))
    digest = summaries.build_digest(db, config, player, window)
    assert "no pair of readings covers this period" in digest

    # The window is Eastern midnight to midnight, so these have to be inside
    # 04:00Z-04:00Z, not inside the UTC day.
    db.save_snapshot(player["id"], snapshot("2026-09-07T10:00:00.000Z",
                                            skills={"attack": (100, 1)}))
    db.save_snapshot(player["id"], snapshot("2026-09-07T23:00:00.000Z",
                                            skills={"attack": (500, 2)}))
    digest = summaries.build_digest(db, config, player, window)
    assert "Data coverage: 2 readings inside the period" in digest


def test_the_digest_hash_changes_only_when_the_figures_do(db, config, player):
    window = periods.latest_window("day", at(2026, 9, 8, 6))
    first = summaries.digest_hash(summaries.build_digest(db, config, player, window))
    again = summaries.digest_hash(summaries.build_digest(db, config, player, window))
    assert first == again, "an unchanged period must not be re-billed"


# -- a run every ten minutes ----------------------------------------------

def test_slots_sit_on_wall_clock_boundaries(monkeypatch):
    """Predictable across a restart, and a missed one is still recognisably
    missed - counting from whenever the process started is neither."""
    from datetime import datetime, timezone

    from wom import scheduler
    for minute, expected in ((0, 0), (7, 0), (9, 0), (10, 10), (59, 50)):
        now = datetime(2026, 9, 1, 15, minute, 30, tzinfo=timezone.utc)
        assert scheduler.previous_slot(now).minute == expected
        assert scheduler.next_slot(now) > now


def test_milestones_are_fetched_on_the_hour_not_every_run():
    """A request per player for something that moves rarely."""
    from datetime import datetime, timezone

    from wom import scheduler
    on_the_hour = [m for m in range(0, 60, scheduler.SLOT_MINUTES)
                   if scheduler.wants_achievements(
                       datetime(2026, 9, 1, 15, m, tzinfo=timezone.utc))]
    assert on_the_hour == [0], "once an hour, not six times"


def test_history_is_thinned_once_a_day_not_every_run(db, player, tmp_path):
    """Nothing had ever called compaction: it was a command somebody had to
    remember, which is a thing that does not happen."""
    from conftest import snapshot

    import web_app
    from wom.config import Config

    for hour in ("00", "06", "12", "18"):
        db.save_snapshot(player["id"], snapshot(
            "2020-01-01T{}:00:00.000Z".format(hour),
            skills={"attack": (int(hour) + 1, 40)}))
    as_polled(db)
    settings = Config()

    web_app._thin_history(db, settings)
    assert len(db.observations(player["id"], "2020-01-01", "2020-01-02")) == 1
    stamped = settings.get("last_compact")
    assert stamped, "the day it ran is remembered"

    # A second run the same day does nothing at all.
    db.save_snapshot(player["id"], snapshot("2020-01-02T06:00:00.000Z",
                                            skills={"attack": (99, 40)}))
    web_app._thin_history(db, Config())
    assert len(db.observations(player["id"], "2020-01-02", "2020-01-03")) == 1, \
        "still there: today's compaction already happened"


# -- the thing that decides whether an update happens at all ---------------
#
# Twenty-one tests above this line and not one of them touched the scheduler
# object. `due()` decides whether a pass runs; `claim()` is the flag that stops
# a scheduled run landing on top of a manual one - two passes over the same
# players, two sets of API calls, and two sets of paid-for Claude calls.

class _Settings(dict):
    """A Config stand-in: the scheduler only reads keys and calls save()."""

    def save(self):
        return self


def _scheduler(job=None, **settings):
    from wom.scheduler import SlotScheduler
    config = _Settings({"usernames": ["zezima"], "last_run": ""})
    config.update(settings)
    return SlotScheduler(config, job or (lambda trigger: None)), config


def test_nothing_is_due_before_anyone_is_tracked():
    """An empty roster is not an overdue run; it is nothing to run."""
    slots, _config = _scheduler(usernames=[])
    assert slots.due(at(2026, 9, 8, 6)) is False


def test_a_tracker_that_has_never_run_is_due_at_once():
    """Catching up beats waiting for the next boundary on a fresh install."""
    slots, _config = _scheduler()
    assert slots.due(at(2026, 9, 8, 6)) is True


def test_a_run_inside_this_slot_is_not_due_again():
    from wom import scheduler
    now = at(2026, 9, 8, 6)
    slots, _config = _scheduler(
        last_run=scheduler.previous_slot(now).isoformat(timespec="seconds"))
    assert slots.due(now) is False, "this slot has already been served"
    assert slots.due(now + timedelta(minutes=scheduler.SLOT_MINUTES)) is True


def test_a_slot_missed_while_the_machine_was_off_is_still_due():
    """The whole point of anchoring to the wall clock: a gap is recognisably
    a gap rather than being counted from whenever the process restarted."""
    slots, _config = _scheduler(last_run=at(2026, 9, 8, 6).isoformat())
    assert slots.due(at(2026, 9, 9, 6)) is True


def test_the_busy_flag_is_taken_once_and_given_back():
    slots, _config = _scheduler()
    assert slots.claim() is True
    assert slots.claim() is False, "a second caller must not get it too"
    slots.release()
    assert slots.claim() is True, "and it is available again afterwards"


def test_a_manual_run_is_refused_while_one_is_already_going():
    """The admin page's buttons take this same flag. Without it a scheduled
    slot fires into the middle of a manual update."""
    slots, _config = _scheduler()
    slots.claim()
    assert slots.run_now("manual") is False
    slots.release()


def test_a_finished_run_stamps_last_run_and_frees_the_flag():
    seen = []
    slots, config = _scheduler(job=lambda trigger: seen.append(trigger))
    slots._run_job("scheduled")
    assert seen == ["scheduled"]
    assert config["last_run"], "the slot is recorded so it is not run twice"
    assert slots.claim() is True, "the flag is given back"


def test_a_job_that_raises_still_gives_the_flag_back():
    """Otherwise one failure wedges the scheduler for the life of the process."""
    def explode(trigger):
        raise RuntimeError("the API fell over")

    slots, config = _scheduler(job=explode)
    slots.claim()
    slots._run_job("scheduled")
    assert slots.claim() is True, "released even though the job raised"
    assert not config["last_run"], "and a failed pass is not recorded as done"
