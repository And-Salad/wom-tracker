"""Budgets, the tripwire, and knowing who is calling."""

from wom.web.limits import Budget, Tripwire, client_address


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


def test_the_client_address_prefers_a_header_the_proxy_sets(app):
    """remote_addr behind a proxy is the proxy, which pools every visitor."""
    with app.test_request_context("/", headers={"Fly-Client-IP": "203.0.113.7"}):
        assert client_address() == ("203.0.113.7", "Fly-Client-IP")

    with app.test_request_context(
            "/", headers={"X-Forwarded-For": "198.51.100.9, 172.16.0.1"}):
        address, source = client_address()
        assert address == "198.51.100.9", "the leftmost entry is the client"
        assert source == "X-Forwarded-For"

    with app.test_request_context("/"):
        assert client_address()[1] == "remote_addr"


def test_data_endpoints_refuse_a_caller_past_the_ceiling(client, app):
    limits = app.config["LIMITS"]
    limits.api_per_address = Budget(allowance=3, window=60)
    limits.api_tripwire = Tripwire(allowance=999, window=60)
    headers = {"Fly-Client-IP": "203.0.113.20"}
    for _ in range(3):
        assert client.get("/api/chart/skill_gains", headers=headers).status_code == 200
    assert client.get("/api/chart/skill_gains", headers=headers).status_code == 429
    assert client.get("/api/chart/skill_gains",
                      headers={"Fly-Client-IP": "203.0.113.21"}).status_code == 200


def test_a_tripped_wire_stops_data_but_not_the_site(client, app):
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
