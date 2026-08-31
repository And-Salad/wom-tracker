"""Calendar windows, and which summaries a given morning owes."""

from datetime import datetime, timedelta

import pytest
from conftest import snapshot

from wom import periods, summaries
from wom.scheduler import EASTERN


def at(year, month, day, hour):
    return datetime(year, month, day, hour, 0, tzinfo=EASTERN)


def written(db, player, now, keys):
    """Pretend those periods were fully written for `now`'s windows."""
    for key in keys:
        window = periods.latest_window(key, now)
        db.save_summary(player["id"], window, "text", "hash")
        db.save_group_summary(window, "text", "hash")


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


def test_nothing_is_owed_before_the_morning_slot(db):
    assert summaries.due_periods(db, at(2026, 9, 8, 5)) == []


def test_a_fresh_install_owes_every_period(db):
    assert summaries.due_periods(db, at(2026, 9, 8, 6)) == [
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
    now = at(2026, 9, 8, 6)
    window = periods.latest_window("day", now)
    db.save_summary(player["id"], window, "text", "hash")
    assert "day" in summaries.due_periods(db, now)
    db.save_group_summary(window, "text", "hash")
    assert "day" not in summaries.due_periods(db, now)


def test_a_year_of_mornings_owes_about_one_window_a_day(db, player):
    day = at(2026, 1, 1, 6)
    owed = 0
    for _ in range(365):
        keys = summaries.due_periods(db, day)
        owed += len(keys)
        written(db, player, day, keys)
        day += timedelta(days=1)
    assert 420 <= owed <= 450, owed


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
