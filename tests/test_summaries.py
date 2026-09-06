"""What happens when the Claude call goes wrong, and what a colour has to be.

The generate() path is where money is spent and where every failure mode a
person actually meets lives - a rejected key, a rate limit, a model that says
no. All six branches turn an SDK exception into a sentence somebody can act
on, and none of them was covered.
"""

import types

import pytest

from wom.summaries import SummaryError, generate


class _Usage:
    input_tokens = 120
    output_tokens = 340


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Reply:
    def __init__(self, text="A quiet week.", stop_reason="end_turn"):
        self.content = [_Block(text)] if text is not None else []
        self.stop_reason = stop_reason
        self.usage = _Usage()


def _fake_anthropic(raises=None, reply=None):
    """A stand-in for the SDK carrying the real exception classes."""
    import anthropic

    class _Messages:
        def create(self, **_kwargs):
            if raises is not None:
                raise raises
            return reply or _Reply()

    client = types.SimpleNamespace(messages=_Messages())
    return client, anthropic


def _run(monkeypatch, raises=None, reply=None):
    client, _anthropic = _fake_anthropic(raises, reply)
    monkeypatch.setattr("wom.summaries._client", lambda _config: client)
    return generate({"summary_model": "claude-sonnet-5"}, "system", "digest")


def _sdk_error(name):
    """Build one of the SDK's own exceptions without making a request."""
    import anthropic
    cls = getattr(anthropic, name)
    response = types.SimpleNamespace(status_code=400, headers={}, request=None)
    try:
        return cls("boom", response=response, body=None)
    except TypeError:
        return cls("boom")


def test_a_good_reply_comes_back_as_text_and_usage(monkeypatch):
    text, usage = _run(monkeypatch, reply=_Reply("A quiet week."))
    assert text == "A quiet week."
    assert usage["input_tokens"] == 120 and usage["output_tokens"] == 340
    assert usage["model"] == "claude-sonnet-5"


@pytest.mark.parametrize("name,expected", [
    ("AuthenticationError", "key was rejected"),
    ("PermissionDeniedError", "not allowed to use this model"),
    ("NotFoundError", "unknown model"),
    ("RateLimitError", "rate limited"),
])
def test_every_sdk_failure_becomes_a_sentence_worth_reading(monkeypatch, name,
                                                            expected):
    """A traceback in the job log tells the reader nothing they can act on."""
    with pytest.raises(SummaryError) as raised:
        _run(monkeypatch, raises=_sdk_error(name))
    assert expected in str(raised.value)


def test_an_unreachable_api_says_so(monkeypatch):
    import anthropic
    with pytest.raises(SummaryError) as raised:
        _run(monkeypatch, raises=anthropic.APIConnectionError(request=None))
    assert "could not reach" in str(raised.value)


def test_a_refusal_is_not_stored_as_a_summary(monkeypatch):
    with pytest.raises(SummaryError) as raised:
        _run(monkeypatch, reply=_Reply("", stop_reason="refusal"))
    assert "declined" in str(raised.value)


def test_an_empty_answer_is_not_stored_as_a_summary(monkeypatch):
    """Billed for and worthless - and it would be filed as that window's note."""
    with pytest.raises(SummaryError) as raised:
        _run(monkeypatch, reply=_Reply("   "))
    assert "returned nothing" in str(raised.value)


def test_no_key_anywhere_is_refused_before_anything_is_spent(monkeypatch):
    from wom.summaries import _client
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(SummaryError) as raised:
        _client({"anthropic_api_key": ""})
    assert "no Anthropic API key" in str(raised.value)


# -- colours, which arrive from a form ------------------------------------

def test_a_short_hex_is_expanded_and_a_bad_one_refused():
    """The admin colour box takes whatever is typed into it."""
    from wom.colors import normalise
    assert normalise("#abc") == "#aabbcc"
    assert normalise("AABBCC") == "#aabbcc"
    assert normalise("  #A1B2C3  ") == "#a1b2c3"
    for bad in ("", None, "red", "#12345", "#gggggg", "#1234567"):
        assert normalise(bad) is None, bad


def test_an_override_is_stored_lowercased_and_can_be_cleared(config):
    from wom.colors import player_color, set_player_color
    set_player_color(config, "Zezima", "#ABC")
    assert config.get("player_colors") == {"zezima": "#aabbcc"}
    assert player_color(config, "zezima") == "#aabbcc"

    set_player_color(config, "Zezima", None)
    assert config.get("player_colors") == {}
    assert player_color(config, "zezima", 0), "back to the palette, not blank"


# -- a round-up per board, per window --------------------------------------

def test_a_run_writes_a_round_up_for_every_board(db, player, monkeypatch):
    """Both leaderboards owe one for the same window. Writing only the first
    would leave the second with nothing, for ever - the window is settled by
    then and never comes due again.
    """
    from wom import summaries, winners

    def wrote(config, system, digest, kind="player"):
        return ("WINNER: none\n\nWords.",
                {"input_tokens": 1, "output_tokens": 1, "model": "test"})

    monkeypatch.setattr(summaries, "generate", wrote)
    results = summaries.summarise_all(db, {"summary_model": "m"},
                                      [player], ["day"])
    wrote = {entry["player"] for entry in results}
    assert "Maxing" in wrote and "Grinding" in wrote

    from wom import periods
    window = periods.latest_window("day")
    for board in winners.BOARDS:
        assert db.group_summary(window.period, window.key, board) is not None, board


def test_the_two_round_ups_are_written_from_different_digests(db, player,
                                                              monkeypatch):
    """Same window, same readings, different rule - so if both boards were
    handed the same digest the second would be the first one's answer."""
    from wom import periods, summaries, winners

    seen = []

    def wrote(config, system, digest, kind="player"):
        seen.append(digest)
        return ("Words.",
                {"input_tokens": 1, "output_tokens": 1, "model": "t"})

    monkeypatch.setattr(summaries, "generate", wrote)
    window = periods.latest_window("day")
    for board in winners.BOARDS:
        summaries.summarise_group(db, {"summary_model": "m"}, [player], window,
                                  board=board)
    assert len(seen) == 2
    assert seen[0] != seen[1], "each board has to be told which one it is"


# -- what a player's own client reported ------------------------------------

def _quest(db, player, when):
    """One reported quest, the way the webhook would have stored it."""
    from wom import gameplay
    gameplay.store(db, player["username"], "quest", when, {
        "extra": {"questName": "Dragon Slayer II",
                  "completedQuests": 23, "totalQuests": 156}})


def test_what_a_client_reported_reaches_both_digests(db, player, config):
    """The round-up was the last thing writing about a period that could not
    see the things somebody would actually mention.

    Wise Old Man gives us 99s and thresholds. A quest, a diary, a pet, which
    drop filled a log slot - all of that is stored whole as it arrives and has
    always been on the Milestones page, and neither digest read any of it.
    """
    from wom import periods, summaries

    window = periods.latest_window("day")
    _quest(db, player, window.start_iso())

    note = summaries.build_digest(db, config, player, window)
    round_up = summaries.build_group_digest(db, config, [player], window)

    for digest in (note, round_up):
        assert "Dragon Slayer II" in digest
        assert "Quest" in digest
        # The progress the event carried, formatted the way the feed does.
        assert "23 of 156" in digest


def test_a_digest_says_reported_events_are_opt_in(db, player, config):
    """An empty block means "not told", never "did nothing".

    Reporting takes a second URL in Dink and a few toggles, so it is sparse by
    construction - and a model left to work that out for itself would read a
    player who never opted in as one who had a quiet month. The same rule the
    coverage lines follow.
    """
    from wom import periods, summaries

    window = periods.latest_window("day")
    assert summaries.REPORTED_CAVEAT not in summaries.build_digest(
        db, config, player, window), "nothing reported, so nothing to explain"

    _quest(db, player, window.start_iso())
    assert summaries.REPORTED_CAVEAT in summaries.build_digest(
        db, config, player, window)
    assert summaries.REPORTED_CAVEAT in summaries.build_group_digest(
        db, config, [player], window)


def test_a_reported_event_outside_the_window_is_not_in_it(db, player, config):
    from datetime import timedelta

    from wom import periods, summaries
    from wom.util import api_stamp

    window = periods.latest_window("day")
    _quest(db, player, api_stamp(window.start - timedelta(days=3)))
    assert "Dragon Slayer II" not in summaries.build_digest(
        db, config, player, window)


# -- keeping what the model was given ---------------------------------------

def test_the_digest_and_the_prompt_behind_a_note_are_kept(db, player,
                                                          monkeypatch):
    """A hash answers "has this changed"; only the digest answers "why did it
    say that" - and by the time anybody asks, the readings are gone."""
    from wom import periods, summaries

    def wrote(config, system, digest, kind="player"):
        return ("Words.", {"input_tokens": 1, "output_tokens": 1, "model": "t"})

    monkeypatch.setattr(summaries, "generate", wrote)
    window = periods.latest_window("day")
    summaries.summarise_player(db, {}, player, window)

    row = db.summary(player["id"], "day", window.key)
    assert row["digest"], "the digest was not kept"
    assert row["digest"] == summaries.build_digest(db, {}, player, window)
    assert row["prompt_hash"] == summaries.digest_hash(
        summaries.load_prompt({}, window.period))


def test_a_round_up_keeps_its_digest_and_prompt_too(db, player, monkeypatch):
    from wom import periods, summaries, winners

    def wrote(config, system, digest, kind="player"):
        return ("WINNER: nobody\n\nWords.",
                {"input_tokens": 1, "output_tokens": 1, "model": "t"})

    monkeypatch.setattr(summaries, "generate", wrote)
    window = periods.latest_window("day")
    summaries.summarise_group(db, {}, [player], window, board=winners.GRINDING)

    row = db.group_summary("day", window.key, winners.GRINDING)
    assert row["digest"] and "Grinding" in row["digest"]
    assert row["prompt_hash"]


def test_notes_written_before_this_keep_a_null_digest(db):
    """Nullable on purpose: those digests were not kept and cannot be
    rebuilt, so the honest answer is that we do not have them."""
    from conftest import before_migration

    conn = db.connect()
    with conn:
        conn.execute("ALTER TABLE summaries DROP COLUMN digest")
        conn.execute("ALTER TABLE summaries DROP COLUMN prompt_hash")
    before_migration(db, 12)
    reopened = type(db)(db.path)

    columns = {row["name"] for row in reopened.connect().execute(
        "PRAGMA table_info(summaries)")}
    assert {"digest", "prompt_hash"} <= columns


# -- one setting was covering two different jobs ----------------------------

def test_the_round_up_falls_back_to_the_notes_model_unless_given_one():
    """Every config written before this said "use one model for both", so
    that is what an empty group setting has to keep meaning."""
    from wom import summaries

    both = {"summary_model": "claude-haiku-4-5", "group_model": ""}
    assert summaries.setting(both, "model", "player") == "claude-haiku-4-5"
    assert summaries.setting(both, "model", "group") == "claude-haiku-4-5"

    split = dict(both, group_model="claude-opus-5")
    assert summaries.setting(split, "model", "player") == "claude-haiku-4-5"
    assert summaries.setting(split, "model", "group") == "claude-opus-5"


def test_the_admin_page_offers_the_round_up_its_own_model(signed_in):
    page = signed_in.get("/admin").get_data(as_text=True)
    assert 'name="group_model"' in page and 'name="group_effort"' in page
    assert "same as above" in page, (
        "an unset group model has to be choosable, not only its default")


def test_a_death_is_kept_but_never_reaches_a_recap(db, player, config):
    """Deaths arrive on the same webhook as everything else in the block and
    have their own shelf in the Gallery. A round-up is about what somebody
    did, and reaching for the deaths would be writing about the one thing
    they would least like read back to them.
    """
    from wom import gameplay, periods, summaries

    window = periods.latest_window("day")
    gameplay.store(db, player["username"], "death", window.start_iso(),
                   {"extra": {"valueLost": 1200000}})

    assert db.game_events(player["username"], kind="death"), (
        "still stored - the Gallery reads these")
    for digest in (summaries.build_digest(db, config, player, window),
                   summaries.build_group_digest(db, config, [player], window)):
        assert "Died" not in digest
        assert "1,200,000" not in digest
    assert "death" not in summaries.REPORTED_KINDS
