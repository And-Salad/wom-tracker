"""The two leaderboards, and what each of them counts."""
from conftest import seed


def test_grinding_judges_on_all_experience_and_maxing_only_up_to_99():
    """The whole difference between the two boards, in one place."""
    from wom import winners
    maxed = {"nines": 0, "raw": 900000.0, "capped": 0.0}
    climbing = {"nines": 0, "raw": 100000.0, "capped": 100000.0}
    assert winners.key(climbing, winners.MAXING) > winners.key(maxed, winners.MAXING)
    assert (winners.key(maxed, winners.GRINDING)
            > winners.key(climbing, winners.GRINDING))


def test_a_ninety_nine_wins_a_maxing_day_and_counts_for_nothing_extra_grinding():
    from wom import winners
    reached = {"nines": 1, "raw": 10.0, "capped": 10.0}
    busy = {"nines": 0, "raw": 5000000.0, "capped": 5000000.0}
    assert winners.key(reached, winners.MAXING) > winners.key(busy, winners.MAXING)
    assert winners.key(busy, winners.GRINDING) > winners.key(reached, winners.GRINDING)


def test_an_account_past_99_everywhere_can_win_grinding_but_not_maxing():
    from wom import winners
    maxed = {"nines": 0, "raw": 4000000.0, "capped": 0.0}
    assert not winners.moved(maxed, winners.MAXING)
    assert winners.moved(maxed, winners.GRINDING)


def test_both_leaderboard_pages_render(client, app):
    seed(app)
    for path, heading in (("/maxing", "Maxing Leaderboard"),
                          ("/grinding", "Grinding Leaderboard")):
        page = client.get(path).get_data(as_text=True)
        assert heading in page, path


def test_the_grinding_table_says_what_it_counts(client, app):
    seed(app)
    page = client.get("/grinding").get_data(as_text=True)
    assert "XP Gained" in page and "XP Towards 99" not in page
    assert "99 Wins" not in page, (
        "a 99 never takes a grinding day, so that column could only read zero")
    assert "Levels Today" in page and "99s Today" not in page, (
        "a 99 is not what this board is about; levels are what moves")


def test_levels_today_counts_from_midnight(db, player):
    """Read at the two edges, and a missing edge is no answer rather than a
    guess - treated as zero it would report the account's whole total level."""
    from datetime import timedelta, timezone

    from conftest import snapshot as snap

    from wom.web import today

    opens = today.winners.today_range()[0]
    assert today._levels_today(db, player, opens) == 0, "nothing read yet"

    def at(offset):
        return (opens + offset).astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")

    # One reading before the day opened and one after it, or there is no
    # midnight between them to count across.
    db.save_snapshot(player["id"], snap(at(timedelta(hours=-2)),
                                        skills={"overall": (1000, 100)}))
    db.save_snapshot(player["id"], snap(at(timedelta(hours=2)),
                                        skills={"overall": (9000, 104)}))
    assert today._levels_today(db, player, opens) == 4


def test_the_maxing_table_is_unchanged(client, app):
    seed(app)
    page = client.get("/maxing").get_data(as_text=True)
    assert "XP Towards 99" in page and "99 Wins" in page and "XP Wins" in page


def test_each_board_has_its_own_endpoints(client, app):
    seed(app)
    for board in ("maxing", "grinding"):
        assert client.get("/api/{}/trend".format(board)).status_code == 200
        assert client.get("/api/{}/player/zezima".format(board)).status_code == 200
    assert client.get("/api/nonsense/trend").status_code == 404
    assert client.get("/api/nonsense/player/zezima").status_code == 404


def test_a_grinding_day_counts_experience_past_99(db, player):
    """Maxing sets experience past 99 aside as 'beyond' and does not judge on
    it. Grinding has no such rule, so reporting one would print a caveat
    about a rule this board does not have.

    The account has to actually be past 99 for this to bite - with a skill
    below it there is no `beyond` either way and the test would pass on a
    board that ignored the distinction entirely.
    """
    from datetime import timedelta, timezone

    from conftest import snapshot as snap

    from wom import winners
    from wom.web import today

    # Anchored to the day the app is actually judging, not to a UTC hour:
    # picking 01:00 and 02:00 put both readings on the previous local day for
    # anyone running this between midnight and 04:00, and the test failed for
    # a reason that had nothing to do with the code.
    opens = winners.today_range()[0]

    def at(offset):
        return (opens + offset).astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")

    db.save_snapshot(player["id"], snap(at(timedelta(hours=1)),
                                        skills={"attack": (14000000, 99)}))
    db.save_snapshot(player["id"], snap(at(timedelta(hours=2)),
                                        skills={"attack": (14500000, 99)}))

    maxed = today.breakdown(db, player, board=winners.MAXING)
    ground = today.breakdown(db, player, board=winners.GRINDING)
    assert maxed["beyond"] == 500000, "all of it is past 99 on maxing"
    assert maxed["total"] == 0, "and none of it counts there"
    assert ground["beyond"] == 0, "grinding has nothing to set aside"
    assert ground["total"] == 500000, "it all counts here"


def test_a_round_up_only_overrules_its_own_board(db):
    """The two judge the same days by different rules, so a grinding verdict
    has no business colouring a maxing square."""
    from wom import periods, winners
    window = periods.latest_window("day")
    db.save_group_summary(window, "text", "hash", winner="zezima",
                          board=winners.GRINDING)

    assert winners._written_winners(db, "day", winners.GRINDING) == {
        window.key: "zezima"}
    assert winners._written_winners(db, "day", winners.MAXING) == {}, (
        "the other board must not see it")


def test_the_same_window_can_be_written_for_both_boards(db):
    """They are different verdicts about the same days, not a conflict."""
    from wom import periods, winners
    window = periods.latest_window("day")
    db.save_group_summary(window, "maxing text", "h1", winner="zezima",
                          board=winners.MAXING)
    db.save_group_summary(window, "grinding text", "h2", winner="addy",
                          board=winners.GRINDING)
    assert db.group_summary(window.period, window.key,
                            winners.MAXING)["winner"] == "zezima"
    assert db.group_summary(window.period, window.key,
                            winners.GRINDING)["winner"] == "addy"


def test_round_ups_written_before_there_were_two_boards_are_maxing(tmp_path):
    """Everything already written was about the leaderboard that existed."""
    import sqlite3

    from wom.db import Database
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE group_summaries (
            period TEXT NOT NULL, window_key TEXT NOT NULL, winner TEXT,
            period_start TEXT NOT NULL, period_end TEXT NOT NULL,
            label TEXT NOT NULL, text TEXT NOT NULL, digest_hash TEXT NOT NULL,
            model TEXT, input_tokens INTEGER, output_tokens INTEGER,
            generated_at TEXT NOT NULL, PRIMARY KEY (period, window_key));
        INSERT INTO group_summaries VALUES
            ('day', '2026-09-01', 'zezima', 'a', 'b', 'Tuesday', 'words',
             'hash', NULL, NULL, NULL, 'now');
    """)
    conn.commit()
    conn.close()

    database = Database(path)
    rows = database.group_summaries(period="day", board="maxing")
    assert len(rows) == 1 and rows[0]["winner"] == "zezima"
    assert database.group_summaries(period="day", board="grinding") == []
