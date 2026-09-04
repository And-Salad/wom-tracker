"""How much moved in a window - the part that has been wrong most often.

Every test here is a bug that shipped. The unranked case reported a boss taken
from nothing to 286 kills as no kills at all; the baseline case measured "this
month" from a snapshot four years earlier.
"""


from conftest import snapshot

from wom import periods


def test_unranked_at_the_baseline_counts_from_zero(db, player):
    """The API sends -1 for unranked, which is stored as NULL.

    An inner join on both sides dropped those metrics entirely, so a boss the
    player was unranked on a year ago and has 300 kills on now showed as zero.
    """
    db.save_snapshot(player["id"], snapshot(
        "2025-01-01T00:00:00.000Z", bosses={"zulrah": -1, "vorkath": 100}))
    db.save_snapshot(player["id"], snapshot(
        "2026-08-30T00:00:00.000Z", bosses={"zulrah": 300, "vorkath": 180}))

    gains = db.metric_gains(player["id"], "2024-01-01T00:00:00.000Z", "boss")
    assert gains["zulrah"] == 300, "unranked baseline must count from zero"
    assert gains["vorkath"] == 80


def test_a_metric_that_did_not_exist_at_the_baseline_still_counts(db, player):
    """New bosses are released; their kills all belong to the window."""
    db.save_snapshot(player["id"], snapshot(
        "2025-01-01T00:00:00.000Z", bosses={"vorkath": 10}))
    db.save_snapshot(player["id"], snapshot(
        "2026-08-30T00:00:00.000Z", bosses={"vorkath": 10, "the_hueycoatl": 286}))

    gains = db.metric_gains(player["id"], "2024-01-01T00:00:00.000Z", "boss")
    assert gains["the_hueycoatl"] == 286


def test_gains_never_go_backwards(db, player):
    """Rank shuffles and hiscore corrections must not produce negatives."""
    db.save_snapshot(player["id"], snapshot(
        "2026-08-01T00:00:00.000Z", bosses={"zulrah": 100}))
    db.save_snapshot(player["id"], snapshot(
        "2026-08-30T00:00:00.000Z", bosses={"zulrah": 90}))

    gains = db.metric_gains(player["id"], "2026-07-01T00:00:00.000Z", "boss")
    assert gains.get("zulrah", 0) == 0


def test_baseline_picks_the_reading_nearer_the_window_edge(db, player):
    """Wise Old Man's history is sparse for accounts it has not watched long.

    Measuring from the last snapshot before the window opened reported four
    years of kills as "this month" when the previous reading was that old.
    """
    db.save_snapshot(player["id"], snapshot("2022-05-21T00:00:00.000Z",
                                            bosses={"zulrah": 0}))
    db.save_snapshot(player["id"], snapshot("2026-08-06T00:00:00.000Z",
                                            bosses={"zulrah": 200}))
    db.save_snapshot(player["id"], snapshot("2026-08-30T00:00:00.000Z",
                                            bosses={"zulrah": 300}))

    month = periods.get("month").start_iso()
    baseline = db.baseline_snapshot(player["id"], month)
    assert baseline["captured_at"].startswith("2026-08-06"), (
        "the 2022 reading is nearer in the wrong direction by years")

    year = periods.get("year").start_iso()
    assert db.baseline_snapshot(player["id"], year)["captured_at"].startswith(
        "2026-08-06"), "still the nearer of the two"


def test_windows_are_monotonic(db, player):
    """A longer window can never report less than a shorter one."""
    for day, kills in (("2026-08-01", 10), ("2026-08-20", 40),
                       ("2026-08-29", 80), ("2026-08-30", 100)):
        db.save_snapshot(player["id"],
                         snapshot(day + "T00:00:00.000Z", bosses={"zulrah": kills}))

    totals = [sum(db.metric_gains(player["id"], p.start_iso(), "boss").values())
              for p in periods.PERIODS]
    assert totals == sorted(totals), "gains shrank as the window grew: {}".format(
        dict(zip([p.label for p in periods.PERIODS], totals)))


def test_one_snapshot_measures_nothing(db, player):
    """With a single reading there is no pair to subtract."""
    db.save_snapshot(player["id"], snapshot("2026-08-30T00:00:00.000Z",
                                            bosses={"zulrah": 300}))
    assert db.metric_gains(player["id"], "2026-08-01T00:00:00.000Z", "boss") == {}


def test_no_snapshots_at_all(db, player):
    assert db.metric_gains(player["id"], "2026-08-01T00:00:00.000Z", "boss") == {}
    assert db.snapshot_bounds(player["id"], "2026-08-01T00:00:00.000Z") == (None, None)


# -- how late a baseline may be before the figures are called short --------

def test_a_reading_just_after_the_boundary_is_not_short():
    """Some lateness is the schedule working, not missing data.

    Updates land every ten minutes and a pass over a group takes a few more,
    so the first reading of a window is always a little inside it.
    """
    from wom.periods import coverage_slack
    assert coverage_slack(86400) > 20 * 60, (
        "a reading twenty minutes after midnight is an ordinary run")


def test_a_week_measured_from_most_of_a_day_in_is_short():
    """The regression this pins.

    The rule was a flat tenth of the window, written for six-hourly updates.
    On a Week that let a baseline sixteen hours late pass as full coverage,
    which is most of a day of the week silently missing.
    """
    from wom.periods import coverage_slack
    week = 7 * 86400
    assert coverage_slack(week) < 16 * 3600, "sixteen hours of a week is not slop"
    assert week * 0.1 > 16 * 3600, "which the old flat tenth allowed"


def test_long_windows_keep_a_proportional_allowance():
    """A day late into a year is not worth a warning on every chart."""
    from wom.periods import coverage_slack
    assert coverage_slack(365 * 86400) > 86400
    assert coverage_slack(365 * 86400) < 7 * 86400, "but a week late is"
