"""Budgets, the tripwire, and knowing who is calling."""

import time

from wom.web.limits import (TRUSTED_HEADER_ENV, Budget, Tripwire,
                            client_address)


def test_a_budget_allows_its_allowance_then_refuses():
    budget = Budget(allowance=3, window=60)
    for _ in range(3):
        assert budget.check("a") == 0
        budget.record("a")
    assert budget.check("a") > 0


def test_budgets_are_per_key():
    budget = Budget(allowance=1, window=60)
    budget.record("a")
    assert budget.check("a") > 0
    assert budget.check("b") == 0, "one caller must not spend another's allowance"


def test_checking_does_not_spend():
    """A request refused by a second bucket must not eat the first one's."""
    budget = Budget(allowance=2, window=60)
    for _ in range(5):
        assert budget.check("a") == 0
    budget.record("a")
    budget.record("a")
    assert budget.check("a") > 0


def test_the_tripwire_latches_and_stays_latched():
    wire = Tripwire(allowance=3, window=60)
    assert wire.note("1.1.1.1") is False
    assert wire.note("1.1.1.1") is False
    assert wire.note("2.2.2.2") is True, "the call that crosses it says so"
    assert wire.tripped
    assert wire.tripped_by == "2.2.2.2"
    # Latched means latched: further calls do not re-report, and it does not
    # heal on its own.
    assert wire.note("3.3.3.3") is False
    assert wire.tripped


def test_the_tripwire_clears_only_when_asked():
    wire = Tripwire(allowance=1, window=60)
    wire.note("1.1.1.1")
    assert wire.tripped
    wire.reset()
    assert not wire.tripped
    assert wire.status()["tripped"] is False


def test_a_client_supplied_header_is_ignored_until_a_proxy_is_named(app, monkeypatch):
    """Otherwise every limit here has a dial the caller controls: rotate the
    header and each request counts as a different person."""
    monkeypatch.delenv(TRUSTED_HEADER_ENV, raising=False)
    with app.test_request_context("/", headers={"Fly-Client-IP": "203.0.113.7"}):
        assert client_address()[1] == "remote_addr"

    monkeypatch.setenv(TRUSTED_HEADER_ENV, "Fly-Client-IP")
    with app.test_request_context("/", headers={"Fly-Client-IP": "203.0.113.7"}):
        assert client_address() == ("203.0.113.7", "Fly-Client-IP")
    # Named but absent: the proxy is the only thing that sets it, so its
    # absence means this request did not come through the proxy.
    with app.test_request_context("/", headers={"CF-Connecting-IP": "203.0.113.8"}):
        assert client_address()[1] == "remote_addr"

    # A list header reads leftmost, which is the original client.
    monkeypatch.setenv(TRUSTED_HEADER_ENV, "X-Forwarded-For")
    with app.test_request_context(
            "/", headers={"X-Forwarded-For": "198.51.100.9, 172.16.0.1"}):
        assert client_address() == ("198.51.100.9", "X-Forwarded-For")


def test_the_sign_in_lockout_cannot_be_shaken_off_with_a_header(client, app,
                                                                monkeypatch):
    """The bug this replaced: ten guesses, ten claimed addresses, no lockout."""
    monkeypatch.delenv(TRUSTED_HEADER_ENV, raising=False)
    # The lockout belongs to this app's Limits, so exhausting it here cannot
    # reach any other test's app - which it could when it was a module global.
    limits = app.config["LIMITS"]
    refused = False
    for attempt in range(limits.sign_in_attempts + 2):
        page = client.post(
            "/admin/login", data={"password": "wrong"},
            headers={"Fly-Client-IP": "203.0.113.{}".format(attempt)})
        if "Too many attempts" in page.get_data(as_text=True):
            refused = True
            break
    assert refused, "a header nobody vouches for must not buy a fresh allowance"


def test_one_app_locking_out_cannot_lock_out_another(app, monkeypatch):
    """The lockout is per app, like every other budget.

    As a module global it was shared by every app in the process, so one
    exhausting it took the next one's login with it - which is a test-suite
    nuisance, and would be a real one for anything serving two apps at once.
    """
    monkeypatch.delenv(TRUSTED_HEADER_ENV, raising=False)
    first = app.test_client()
    for _ in range(app.config["LIMITS"].sign_in_attempts + 1):
        first.post("/admin/login", data={"password": "wrong"})
    assert "Too many attempts" in first.post(
        "/admin/login", data={"password": "wrong"}).get_data(as_text=True)

    from wom.web import create_app
    second = create_app()
    second.config["TESTING"] = True
    page = second.test_client().post("/admin/login", data={"password": "wrong"})
    assert "Too many attempts" not in page.get_data(as_text=True), (
        "a second app must start with its own allowance")


def test_a_latched_tripwire_is_still_latched_after_a_restart(tmp_path):
    """A deploy is not a person clearing it, and neither is the crash the
    flood caused."""
    from wom.web.limits import ConfigLatch

    latch = ConfigLatch()
    wire = Tripwire(allowance=1, window=60, store=latch)
    wire.note("203.0.113.44")
    assert wire.tripped

    # A new process, reading the same settings.
    again = Tripwire(allowance=1, window=60, store=ConfigLatch())
    assert again.tripped and again.tripped_by == "203.0.113.44"

    again.reset()
    assert not Tripwire(allowance=1, window=60, store=ConfigLatch()).tripped


def test_data_endpoints_refuse_a_caller_past_the_ceiling(client, app, monkeypatch):
    monkeypatch.setenv(TRUSTED_HEADER_ENV, "Fly-Client-IP")
    limits = app.config["LIMITS"]
    limits.api_per_address = Budget(allowance=3, window=60)
    limits.api_tripwire = Tripwire(allowance=999, window=60)
    headers = {"Fly-Client-IP": "203.0.113.20"}
    for _ in range(3):
        assert client.get("/api/chart/skill_gains", headers=headers).status_code == 200
    assert client.get("/api/chart/skill_gains", headers=headers).status_code == 429
    assert client.get("/api/chart/skill_gains",
                      headers={"Fly-Client-IP": "203.0.113.21"}).status_code == 200


def test_one_caller_cannot_trip_the_wire_on_their_own(client, app, monkeypatch):
    """Which is why the wire's total is set where it is.

    A refused call is never counted by the tripwire, so a single address can
    only ever contribute its own allowance to the total. One machine hammering
    the endpoints is stopped by its per-address budget long before it gets
    near - the wire is the backstop for many addresses at once, and the total
    has to be high enough that a busy evening on a shared link is not one of
    them, because latching takes the data offline for everyone until a person
    clears it.
    """
    monkeypatch.setenv(TRUSTED_HEADER_ENV, "Fly-Client-IP")
    limits = app.config["LIMITS"]
    limits.api_per_address = Budget(allowance=3, window=60)
    wire = Tripwire(allowance=5, window=60)
    limits.api_tripwire = wire

    headers = {"Fly-Client-IP": "203.0.113.40"}
    for _ in range(20):
        client.get("/api/chart/skill_gains", headers=headers)
    assert not wire.tripped, "twenty tries from one address, three of them counted"
    assert wire.seen_in_window == 3


def test_a_tripped_wire_stops_data_but_not_the_site(client, app, monkeypatch):
    monkeypatch.setenv(TRUSTED_HEADER_ENV, "Fly-Client-IP")
    limits = app.config["LIMITS"]
    wire = Tripwire(allowance=2, window=60)
    limits.api_tripwire = wire
    limits.api_per_address = Budget(allowance=999, window=60)

    headers = {"Fly-Client-IP": "203.0.113.30"}
    for _ in range(3):
        client.get("/api/chart/skill_gains", headers=headers)
    assert wire.tripped

    assert client.get("/api/chart/skill_gains", headers=headers).status_code == 503
    assert client.get("/api/player/zezima", headers=headers).status_code == 503
    assert client.get("/", headers=headers).status_code == 200, "pages keep working"
    assert client.get("/admin/login").status_code == 200, "admin stays reachable"


def test_an_admin_is_not_budgeted_and_can_still_read(signed_in, app):
    wire = Tripwire(allowance=1, window=60)
    wire.note("1.1.1.1")
    app.config["LIMITS"].api_tripwire = wire
    assert wire.tripped
    assert signed_in.get("/api/chart/skill_gains").status_code == 200

    signed_in.post("/admin/resume")
    assert not wire.tripped


def test_exports_are_budgeted_per_address_and_overall():
    from wom.web.limits import Limits

    limits = Limits(exports_per_address=3, exports_per_day=5)
    granted = sum(1 for n in range(12)
                  if limits.export_allowed("10.0.0.{}".format(n // 3), False)[0] == 0)
    assert granted == 5, "the overall cap is the backstop"
    assert limits.export_allowed("10.0.0.9", is_admin=True) == (0, None)


def test_take_is_atomic_where_check_then_record_is_not():
    """The sign-in path counts in ones, so the slack in check() matters.

    check() deliberately does not spend, so that a request refused by a
    second bucket keeps its allowance here. The cost is that concurrent
    callers all pass a check only one of them should - fine against a limit
    of hundreds, not against six.
    """
    fresh = Budget(allowance=2, window=60)
    assert fresh.take("a") == 0
    assert fresh.take("a") == 0
    assert fresh.take("a") > 0, "the third is refused, however they interleave"


def test_a_correct_password_does_not_spend_an_attempt():
    """Otherwise signing in six times in five minutes locks you out."""
    budget = Budget(allowance=2, window=60)
    for _ in range(10):
        assert budget.take("a") == 0, "a refunded attempt costs nothing"
        budget.refund("a")
    assert budget.take("a") == 0, "and the allowance is still whole"


def test_signing_in_repeatedly_does_not_lock_you_out(client, app, monkeypatch):
    monkeypatch.delenv(TRUSTED_HEADER_ENV, raising=False)
    attempts = app.config["LIMITS"].sign_in_attempts
    for _ in range(attempts + 3):
        page = client.post("/admin/login", data={"password": "test-password"},
                           follow_redirects=True)
        assert "Too many attempts" not in page.get_data(as_text=True)
        client.post("/admin/logout")


def test_a_quiet_key_is_eventually_forgotten():
    """A caller rotating addresses must not grow the dict without bound.

    Pruning only the keys that happen to be looked at never reached the ones
    seen once and abandoned, which is precisely the traffic that fills it.
    """
    # No sleeping, and no window short enough to expire while the loop below
    # is still filling it. This used to wait out a 50ms window, which meant
    # the assertion that nothing had aged out yet was a race against how long
    # 1030 dict writes took - and on a loaded machine it lost.
    budget = Budget(allowance=5, window=30)
    for n in range(1030):
        budget.record("addr-{}".format(n))
    assert len(budget._seen) == 1030, "nothing has aged out yet, so nothing goes"

    # Age every call past the window by hand. What is under test is the
    # pruning rule, not the clock it reads.
    budget._seen = {key: [when - budget.window - 1 for when in calls]
                    for key, calls in budget._seen.items()}
    budget.record("fresh")
    assert "addr-0" not in budget._seen, "an address seen once and abandoned"
    assert len(budget._seen) == 1, "only the live one is left"


# -- the one-at-a-time guard on admin jobs --------------------------------

def test_a_second_job_is_refused_while_one_is_running():
    """Two update passes over the same rows is twice the API traffic for the
    same answer, and for summaries twice the Claude bill."""
    from wom.web.jobs import JobRunner
    import threading

    runner = JobRunner()
    holding, release = threading.Event(), threading.Event()

    def slow(job):
        holding.set()
        release.wait(5)

    assert runner.start("update", slow) is not None
    holding.wait(5)
    assert runner.busy is True
    assert runner.start("summarise", lambda job: None) is None, "refused"

    release.set()
    for _ in range(500):
        if not runner.busy:
            break
        time.sleep(0.01)
    assert runner.busy is False
    assert runner.start("summarise", lambda job: None) is not None, "and free after"


def test_a_job_that_raises_is_recorded_as_failed_not_lost():
    from wom.web.jobs import JobRunner

    runner = JobRunner()
    runner.start("update", lambda job: 1 / 0)
    for _ in range(500):
        if not runner.busy:
            break
        time.sleep(0.01)
    status = runner.status()
    assert status["failed"] is True
    assert "failed" in status["note"]


def test_progress_lines_are_kept_but_bounded():
    """A run is bounded, but nothing should be able to grow without limit."""
    from wom.web.jobs import Job

    job = Job("update")
    for n in range(500):
        job.say("line {}".format(n), keep=True)
    assert len(job.lines) == 200
    assert job.lines[-1] == "line 499", "the newest are the ones kept"


def test_nothing_having_run_yet_is_a_status_not_a_crash():
    from wom.web.jobs import JobRunner
    status = JobRunner().status()
    assert status["running"] is False and status["name"] is None


# -- what the entry points insist on before anything runs ------------------

def test_an_interpreter_without_zoneinfo_is_refused_loudly():
    """The failure without this check is quiet, which is the whole problem:
    zoneinfo is imported inside a try/except so a missing time zone database
    degrades rather than crashes, and on too old an interpreter that same
    path turns every zone but US Eastern into UTC."""
    import io as _io
    from wom import runtime

    said = _io.StringIO()
    assert runtime.check((3, 8), said) is False
    message = said.getvalue()
    assert "3.9 or newer" in message and "3.8" in message
    assert "zoneinfo" in message, "and says which missing piece is the reason"


def test_the_floor_is_the_one_zoneinfo_needs():
    from wom import runtime
    assert runtime.MINIMUM == (3, 9)
    assert runtime.check((3, 9)) is True
    assert runtime.check((3, 12)) is True


def test_this_interpreter_passes_its_own_check():
    from wom import runtime
    assert runtime.check() is True
