"""Budgets, the tripwire, and knowing who is calling."""

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
    from wom.web import admin as admin_module
    # The lockout is one budget for the whole process, so this test borrows it
    # and puts it back rather than leaving everyone else locked out.
    admin_module._sign_in.reset()
    try:
        refused = False
        for attempt in range(admin_module.SIGN_IN_ATTEMPTS + 2):
            page = client.post(
                "/admin/login", data={"password": "wrong"},
                headers={"Fly-Client-IP": "203.0.113.{}".format(attempt)})
            if "Too many attempts" in page.get_data(as_text=True):
                refused = True
                break
        assert refused, "a header nobody vouches for must not buy a fresh allowance"
    finally:
        admin_module._sign_in.reset()


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
