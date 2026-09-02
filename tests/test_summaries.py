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
