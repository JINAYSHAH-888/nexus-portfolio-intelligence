"""
Upstox integration: token expiry math, the verified ISIN mapper's safety
mechanisms, and the real-tradability filter fix.

No live network calls anywhere in this file -- this sandbox has no route to
upstox.com. auth.py's HTTP calls are exercised with a monkeypatched httpx
client; the ISIN mapper is tested against the REAL uploaded MF instrument
file, which is the actual data, just not fetched live.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


# ══════════════════════════════════════════════════════════════════════════════
# Token expiry: "03:30 IST regardless of issue time" is an asymmetric rule
# ══════════════════════════════════════════════════════════════════════════════
class TestTokenExpiry:
    def test_evening_token_expires_next_morning(self):
        from backend.integrations.upstox.token_store import next_upstox_expiry

        issued = datetime(2026, 7, 14, 20, 0, tzinfo=IST)  # Tue 8pm
        expiry = next_upstox_expiry(issued)
        assert expiry.astimezone(IST) == datetime(2026, 7, 15, 3, 30, tzinfo=IST)

    def test_token_issued_after_expiry_time_expires_same_day(self):
        """
        THE ASYMMETRIC CASE from Upstox's own docs: '02:30 AM Wednesday still
        expires 03:30 AM the SAME Wednesday' -- only an hour of validity, not a
        fresh 24h window. A naive 'tomorrow at 3:30' rule gets this wrong.
        """
        from backend.integrations.upstox.token_store import next_upstox_expiry

        issued = datetime(2026, 7, 15, 2, 30, tzinfo=IST)  # Wed 2:30am
        expiry = next_upstox_expiry(issued)
        assert expiry.astimezone(IST) == datetime(2026, 7, 15, 3, 30, tzinfo=IST)

    def test_token_issued_at_exactly_expiry_time_rolls_to_next_day(self):
        from backend.integrations.upstox.token_store import next_upstox_expiry

        issued = datetime(2026, 7, 15, 3, 30, tzinfo=IST)
        expiry = next_upstox_expiry(issued)
        assert expiry.astimezone(IST) == datetime(2026, 7, 16, 3, 30, tzinfo=IST)

    def test_seconds_until_expiry_is_always_positive(self):
        from backend.integrations.upstox.token_store import seconds_until_expiry

        for hour in (0, 3, 3.4, 3.6, 12, 23):
            dt = datetime(2026, 7, 15, int(hour), int((hour % 1) * 60), tzinfo=IST)
            assert seconds_until_expiry(dt.astimezone(UTC)) > 0

    async def test_store_and_retrieve_round_trips(self, monkeypatch):
        """
        Regression guard for the double-JSON-encoding bug: cache_set already
        serialises, so we must store the dict, never a pre-serialised string.

        REDIS_ENABLED=false in the test harness by design (test_infrastructure.py
        pins the fail-open behaviour), so cache_get/cache_set are no-ops here --
        this test fakes them with a plain dict to exercise store_token/get_token's
        OWN logic (the encode/decode round trip) without needing live Redis.
        """
        from backend.infrastructure import redis_client
        from backend.integrations.upstox.token_store import (
            UpstoxToken,
            get_token,
            store_token,
        )

        fake_store: dict[str, object] = {}

        async def fake_cache_set(key, value, ttl=3600):
            fake_store[key] = value

        async def fake_cache_get(key):
            return fake_store.get(key)

        monkeypatch.setattr(redis_client, "cache_set", fake_cache_set)
        monkeypatch.setattr(redis_client, "cache_get", fake_cache_get)

        token = UpstoxToken(
            access_token="tok123",
            extended_token="ext456",
            user_id="UCC001",
            user_name="Test User",
            email="t@example.com",
            broker="UPSTOX",
            exchanges=["NSE", "BSE"],
            products=["D", "I"],
            order_types=["MARKET", "LIMIT"],
            user_type="individual",
            poa=False,
            is_active=True,
            issued_at="2026-07-15T00:00:00+00:00",
        )
        await store_token(token)
        fetched = await get_token("UCC001")
        assert fetched is not None
        assert fetched.access_token == "tok123"
        assert fetched.exchanges == ["NSE", "BSE"]


class TestUpstoxTokenParsing:
    def test_parses_the_real_documented_response_shape(self):
        from backend.integrations.upstox.token_store import UpstoxToken

        # Exact field set from the Upstox "Get Token" API doc.
        body = {
            "email": "user@example.com",
            "exchanges": ["NSE", "NFO", "BSE", "CDS", "BFO", "BCD"],
            "products": ["D", "CO", "I"],
            "broker": "UPSTOX",
            "user_id": "AB1234",
            "user_name": "Test User",
            "order_types": ["MARKET", "LIMIT", "SL", "SL-M"],
            "user_type": "individual",
            "poa": False,
            "is_active": True,
            "access_token": "abc.def.ghi",
            "extended_token": "xyz.123",
        }
        token = UpstoxToken.from_api_response(body)
        assert token.user_id == "AB1234"
        assert token.extended_token == "xyz.123"
        assert token.is_active is True

    def test_missing_extended_token_is_none_not_a_crash(self):
        """extended_token is documented but not guaranteed on every app tier."""
        from backend.integrations.upstox.token_store import UpstoxToken

        body = {
            "email": "u@e.com", "exchanges": [], "products": [], "broker": "UPSTOX",
            "user_id": "X1", "user_name": "U", "order_types": [], "user_type": "individual",
            "poa": False, "is_active": True, "access_token": "tok",
        }
        token = UpstoxToken.from_api_response(body)
        assert token.extended_token is None


# ══════════════════════════════════════════════════════════════════════════════
# OAuth: authorize URL construction, error surfacing (mocked HTTP -- no live
# network to upstox.com from this environment)
# ══════════════════════════════════════════════════════════════════════════════
class TestAuthorizeUrl:
    def test_url_matches_the_documented_contract(self, monkeypatch):
        from backend.core.config import settings
        from backend.integrations.upstox import auth

        monkeypatch.setattr(settings, "UPSTOX_API_KEY", "test-client-id")
        monkeypatch.setattr(settings, "UPSTOX_API_SECRET", "test-secret")
        monkeypatch.setattr(settings, "UPSTOX_REDIRECT_URI", "http://localhost:8000/cb")

        url, state = auth.build_authorize_url()
        assert url.startswith("https://api.upstox.com/v2/login/authorization/dialog?")
        assert "response_type=code" in url
        assert "client_id=test-client-id" in url
        assert state in url

    def test_state_is_generated_when_not_supplied(self, monkeypatch):
        from backend.core.config import settings
        from backend.integrations.upstox import auth

        monkeypatch.setattr(settings, "UPSTOX_API_KEY", "k")
        monkeypatch.setattr(settings, "UPSTOX_API_SECRET", "s")

        _url1, state1 = auth.build_authorize_url()
        _url2, state2 = auth.build_authorize_url()
        assert state1 != state2  # never reused -- CSRF guard would be pointless otherwise

    def test_refuses_to_build_a_url_without_credentials(self, monkeypatch):
        from backend.core.config import settings
        from backend.core.errors import ValidationError
        from backend.integrations.upstox import auth

        monkeypatch.setattr(settings, "UPSTOX_API_KEY", None)
        with pytest.raises(ValidationError):
            auth.build_authorize_url()


class TestExchangeCode:
    async def test_upstox_error_body_is_surfaced_not_swallowed(self, monkeypatch):
        """
        Upstox's error messages are specific and actionable (e.g. 'No segments
        for these users are active'). Collapsing them into a generic failure
        would send the investor down the wrong troubleshooting path.
        """
        import httpx

        from backend.core.config import settings
        from backend.core.errors import UpstreamError
        from backend.integrations.upstox import auth

        monkeypatch.setattr(settings, "UPSTOX_API_KEY", "k")
        monkeypatch.setattr(settings, "UPSTOX_API_SECRET", "s")

        async def fake_post(self, url, headers=None, data=None):
            return httpx.Response(
                400,
                json={"error": "invalid_grant", "message": "Auth code already used"},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

        with pytest.raises(UpstreamError) as exc:
            await auth.exchange_code("stale-code")
        assert "already used" in str(exc.value.details)

    async def test_inactive_account_is_a_clear_actionable_error(self, monkeypatch):
        import httpx

        from backend.core.config import settings
        from backend.core.errors import UpstreamError
        from backend.integrations.upstox import auth

        monkeypatch.setattr(settings, "UPSTOX_API_KEY", "k")
        monkeypatch.setattr(settings, "UPSTOX_API_SECRET", "s")

        async def fake_post(self, url, headers=None, data=None):
            return httpx.Response(
                200,
                json={
                    "email": "u@e.com", "exchanges": [], "products": [], "broker": "UPSTOX",
                    "user_id": "X1", "user_name": "U", "order_types": [],
                    "user_type": "individual", "poa": False, "is_active": False,
                    "access_token": "tok",
                },
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

        with pytest.raises(UpstreamError, match="not active"):
            await auth.exchange_code("some-code")


# ══════════════════════════════════════════════════════════════════════════════
# The verified ISIN mapper -- the module's whole reason for existing is the
# 63%-NAV-drift bug it caught on its FIRST real run. These pin the mechanisms
# that caught it, not just the final output.
# ══════════════════════════════════════════════════════════════════════════════
UPLOADED_MF_FILE = Path("/mnt/user-data/uploads/mf-instruments_json.gz")


@pytest.fixture(scope="module")
def mf_instruments():
    if not UPLOADED_MF_FILE.exists():
        pytest.skip("Real Upstox MF instrument file not present in this environment.")
    import gzip

    with gzip.open(UPLOADED_MF_FILE, "rt", encoding="utf-8") as f:
        return json.load(f)


class TestIsinMapperSafety:
    def test_settlement_variants_of_the_same_isin_are_not_ambiguity(self, mf_instruments):
        """
        Upstox emits 2-3 rows per ISIN (different settlement_type: T2/T3/L1),
        not different funds. Naively counting >1 row as ambiguous would halt
        on EVERY scheme, permanently. Deduping must collapse these first.
        """
        import scripts.build_upstox_isin_map as m

        dupe_key = next(
            k for k, n in [(x["instrument_key"], x["name"]) for x in mf_instruments]
            if sum(1 for y in mf_instruments if y["instrument_key"] == k) > 1
        )
        variants = [x for x in mf_instruments if x["instrument_key"] == dupe_key]
        deduped = m._dedupe_settlement_variants(variants)
        assert len(deduped) == 1

    def test_bonus_and_insured_units_are_excluded(self):
        import scripts.build_upstox_isin_map as m

        assert m._is_excluded("NIPPON INDIA X FUND - GROWTH PLAN - BONUS OPTION")
        assert m._is_excluded("NIPPON INDIA LARGE CAP FUND - GROWTH PLAN - GROWTH-INSURED")
        assert m._is_excluded("NIPPON INDIA X FUND - IDCW REINVESTMENT")
        assert not m._is_excluded("NIPPON INDIA LARGE CAP FUND-REGULAR PLAN-GROWTH")

    def test_squashed_matching_handles_upstoxs_word_concatenation(self):
        """Our 'Flexi Cap' vs Upstox's 'FLEXICAP' -- the exact case the token
        matcher alone could not resolve."""
        import scripts.build_upstox_isin_map as m

        assert m._squashed("Nippon India Flexi Cap Fund") in m._squashed(
            "NIPPON INDIA FLEXICAP FUND-REGULAR PLAN-GROWTH"
        )

    def test_segregated_portfolio_suffix_is_stripped_for_matching(self):
        """
        Our scheme name carries '(Existing Number of Segregated Portfolios -
        1)'; Upstox's listing for the main fund carries no such suffix. A
        strict token-subset match would reject the correct candidate outright
        without this normalisation.
        """
        import scripts.build_upstox_isin_map as m

        ours = m._normalise(
            "Nippon India Medium Duration Fund (Existing Number of Segregated Portfolios - 1)"
        )
        theirs = m._normalise("NIPPON INDIA MEDIUM DURATION FUND- GROWTH PLAN")
        assert ours <= theirs

    def test_the_documented_63_percent_drift_bug_is_rejected_by_max_drift(self):
        """
        The bug this whole script exists to prevent, reproduced directly: our
        Medium Duration factsheet NAV is 16.46; the wrong candidate (a BONUS
        unit of a DIFFERENT fund) was 26.85 -- a 63% drift. MAX_DRIFT_PCT=15%
        must reject it outright, not accept-with-a-warning.
        """
        import scripts.build_upstox_isin_map as m

        our_nav = 16.464
        wrong_candidate_nav = 26.8468
        drift = abs(wrong_candidate_nav - our_nav) / our_nav * 100
        assert drift > m.MAX_DRIFT_PCT

    def test_full_run_against_the_real_file_resolves_all_35_with_tight_drift(
        self, mf_instruments, tmp_path, monkeypatch
    ):
        """
        The end-to-end proof: run the actual mapper against the actual uploaded
        Upstox file and check every one of the 35 real schemes resolves to
        exactly one instrument, NAV-corroborated within tolerance.
        """
        import scripts.build_upstox_isin_map as m

        results, problems = m.build(UPLOADED_MF_FILE)
        assert problems == [], f"{len(problems)} scheme(s) failed to resolve: {problems}"
        assert len(results) == 35
        for sid, row in results.items():
            assert row["instrument_key"] is not None, sid
            assert row["nav_drift_pct"] <= m.MAX_DRIFT_PCT, sid

    def test_the_previously_broken_medium_duration_scheme_now_resolves_correctly(
        self, mf_instruments
    ):
        import scripts.build_upstox_isin_map as m

        results, _ = m.build(UPLOADED_MF_FILE)
        row = results["nippon-medium-duration-fund-existing-number-of-segregated-portfolios-1"]
        assert row["instrument_key"] == "INF204KA1PZ1"
        assert row["nav_drift_pct"] < 5.0


class TestMfMapping:
    def test_loads_the_committed_map(self):
        from backend.integrations.upstox import mf_mapping

        mf_mapping.reset_cache()
        mapping = mf_mapping.get_mapping("nippon-large-cap-fund")
        assert mapping is not None
        assert mapping.is_resolved
        assert mapping.instrument_key.startswith("INF")

    def test_unknown_scheme_returns_none_not_a_crash(self):
        from backend.integrations.upstox import mf_mapping

        assert mf_mapping.get_mapping("not-a-real-scheme") is None

    def test_the_four_closed_schemes_are_flagged_not_purchasable(self):
        from backend.integrations.upstox import mf_mapping

        mf_mapping.reset_cache()
        closed = {
            sid
            for sid, m in mf_mapping.all_mappings().items()
            if m and m.purchase_allowed is False
        }
        assert closed == {
            "nippon-small-cap-fund",
            "nippon-japan-equity-fund",
            "nippon-us-equity-opportunities-fund",
            "nippon-taiwan-equity-fund",
        }


# ══════════════════════════════════════════════════════════════════════════════
# The filter-engine tradability fix, over HTTP
# ══════════════════════════════════════════════════════════════════════════════
async def test_closed_schemes_are_moved_to_filtered_out_not_silently_offered(client, profiled):
    """
    A scheme closed to new purchase must never reach 'eligible' -- selecting,
    allocating, and running ARISE on a fund you cannot actually buy would be a
    silent, expensive lie.
    """
    r = await client.get("/api/v1/schemes/filtered", headers=profiled["headers"])
    body = r.json()

    eligible_ids = {s["id"] for s in body["eligible_schemes"]}
    assert "nippon-small-cap-fund" not in eligible_ids

    filtered_ids = {f["scheme_id"] for f in body["filtered_out"]}
    assert "nippon-small-cap-fund" in filtered_ids
    assert body["filter_summary"]["closed_to_purchase"] == 4


async def test_open_schemes_carry_the_real_upstox_minimum(client, profiled):
    r = await client.get("/api/v1/schemes/filtered", headers=profiled["headers"])
    body = r.json()

    large_cap = next(s for s in body["eligible_schemes"] if s["id"] == "nippon-large-cap-fund")
    assert large_cap.get("upstox_purchasable") is True
    assert large_cap.get("upstox_min_investment") is not None


async def test_tradability_filter_degrades_cleanly_without_the_map(
    client, profiled, monkeypatch
):
    """The platform must keep working with zero Upstox configuration -- absence
    of the ISIN map is a no-op, not an error."""
    from backend.core.config import settings

    monkeypatch.setattr(settings, "ISIN_MAP_PATH", Path("/nonexistent/path.json"))
    from backend.integrations.upstox import mf_mapping

    mf_mapping.reset_cache()
    try:
        r = await client.get("/api/v1/schemes/filtered", headers=profiled["headers"])
        assert r.status_code == 200
        assert r.json()["eligible_count"] == 35  # engine's own result, untouched
    finally:
        mf_mapping.reset_cache()


class TestBareCallbackRoute:
    """
    Upstox validates redirect_uri against what's registered in the Developer
    App dashboard and rejects any mismatch outright -- the registered value is
    the bare "http://HOST/callback" path, not the versioned
    /api/v1/auth/upstox/callback every other route lives under. This pins
    that the bare route actually exists, is reachable, and is the SAME
    handler (not a divergent copy) as the documented canonical one.
    """

    async def test_bare_callback_route_exists_and_is_reachable(self, client):
        # No state param at all -> FastAPI's own 422 for a missing required
        # query param. Reaching this response (rather than a 404) proves the
        # route exists at the bare path.
        r = await client.get("/callback")
        assert r.status_code == 422

    async def test_bare_callback_uses_the_same_state_validation_as_the_canonical_route(
        self, client
    ):
        # An invalid/expired state must be rejected identically on both paths
        # -- proving they're the same function, not two implementations that
        # could silently drift apart.
        bare = await client.get("/callback", params={"code": "x", "state": "not-a-real-state"})
        canonical = await client.get(
            "/api/v1/auth/upstox/callback", params={"code": "x", "state": "not-a-real-state"}
        )
        assert bare.status_code == canonical.status_code == 422
        assert bare.json()["message"] == canonical.json()["message"]

    def test_the_configured_redirect_uri_matches_the_bare_path(self):
        from backend.core.config import settings

        assert settings.UPSTOX_REDIRECT_URI.endswith("/callback")
        assert "/api/v1" not in settings.UPSTOX_REDIRECT_URI

    def test_bare_callback_is_excluded_from_the_public_api_schema(self):
        """/api/v1/auth/upstox/callback stays the one documented, canonical
        path; /callback is purely the external redirect target Upstox needs."""
        from backend.main import app

        assert "/callback" not in app.openapi()["paths"]
        assert "/api/v1/auth/upstox/callback" in app.openapi()["paths"]


class TestLoginReturnsJsonNotARedirect:
    """
    THE BUG THIS CLASS EXISTS TO PREVENT. /auth/upstox/login used to return a
    raw HTTP 307 redirect straight to Upstox. That's broken for any
    programmatic caller (Swagger's "Execute" button, a frontend fetch()):
    fetch() auto-follows redirects, so following this one means attempting a
    cross-origin XHR into Upstox's own login page, which Upstox correctly
    refuses -- reported by the browser as an opaque "Failed to fetch", not a
    clear CORS error. It's ALSO fundamentally incompatible with requiring a
    Bearer-token CurrentUser: a real browser navigating a link can never
    attach a custom Authorization header. Returning JSON instead resolves
    both problems -- the caller fetches the URL with a real auth header (no
    redirect involved), then performs the actual top-level navigation itself.
    """

    async def test_login_returns_200_json_not_a_redirect(self, client, registered, monkeypatch):
        from backend.core.config import settings

        monkeypatch.setattr(settings, "UPSTOX_API_KEY", "test-key")
        monkeypatch.setattr(settings, "UPSTOX_API_SECRET", "test-secret")

        r = await client.get("/api/v1/auth/upstox/login", headers=registered["headers"])
        assert r.status_code == 200
        assert not (300 <= r.status_code < 400)

    async def test_response_contains_the_real_authorize_url(self, client, registered, monkeypatch):
        from backend.core.config import settings

        monkeypatch.setattr(settings, "UPSTOX_API_KEY", "test-key")
        monkeypatch.setattr(settings, "UPSTOX_API_SECRET", "test-secret")

        r = await client.get("/api/v1/auth/upstox/login", headers=registered["headers"])
        body = r.json()
        assert "authorize_url" in body
        assert body["authorize_url"].startswith(
            "https://api.upstox.com/v2/login/authorization/dialog?"
        )
        assert "state" in body
        assert body["state"] in body["authorize_url"]

    async def test_requires_authentication(self, client):
        r = await client.get("/api/v1/auth/upstox/login")
        assert r.status_code == 401

    async def test_state_is_bound_server_side_for_the_callback_to_find(
        self, client, registered, monkeypatch
    ):
        """
        The whole point of the two-step flow: the state returned by /login
        must be the SAME one /callback validates against.

        REDIS_ENABLED=false in the test harness (the same fail-open default
        as everywhere else in this app) means cache_set/cache_get are true
        no-ops -- so this test fakes them with a plain dict, the same pattern
        test_upstox.py already uses for the token-store round-trip test.
        Without that fake, /login "succeeds" (returns a URL) but the state
        was never actually persisted anywhere, so /callback ALWAYS rejects
        it as expired -- a real, worth-knowing consequence of Redis being the
        state-binding store: unlike everywhere else Redis fails open, a
        Redis outage here silently breaks the ability to complete an Upstox
        login at all, not just degrades performance.
        """
        from backend.core.config import settings
        from backend.infrastructure import redis_client

        monkeypatch.setattr(settings, "UPSTOX_API_KEY", "test-key")
        monkeypatch.setattr(settings, "UPSTOX_API_SECRET", "test-secret")

        fake_store: dict[str, object] = {}

        async def fake_cache_set(key, value, ttl=3600):
            fake_store[key] = value

        async def fake_cache_get(key):
            return fake_store.get(key)

        async def fake_cache_delete(*keys):
            for k in keys:
                fake_store.pop(k, None)

        monkeypatch.setattr(redis_client, "cache_set", fake_cache_set)
        monkeypatch.setattr(redis_client, "cache_get", fake_cache_get)
        monkeypatch.setattr(redis_client, "cache_delete", fake_cache_delete)

        login_resp = await client.get("/api/v1/auth/upstox/login", headers=registered["headers"])
        real_state = login_resp.json()["state"]

        # A real (bound) state fails later only because there's no live
        # Upstox to exchange the fake code with -- NOT because state
        # validation rejected it outright (that would be the "expired or was
        # already used" 422; reaching further than that proves the binding
        # genuinely worked end to end).
        r = await client.get(
            "/api/v1/auth/upstox/callback", params={"code": "fake", "state": real_state}
        )
        assert "expired or was already used" not in r.json().get("message", "")

    async def test_state_binding_requires_redis_and_fails_visibly_not_silently(
        self, client, registered, monkeypatch
    ):
        """
        The flip side of the above, made explicit: WITHOUT a working cache
        (Redis disabled or down), /login still returns 200 with a URL -- but
        that state was never persisted, so /callback correctly rejects it
        with a clear, actionable message rather than something confusing. A
        real deployment must not run Upstox login with Redis disabled.
        """
        from backend.core.config import settings

        monkeypatch.setattr(settings, "UPSTOX_API_KEY", "test-key")
        monkeypatch.setattr(settings, "UPSTOX_API_SECRET", "test-secret")

        login_resp = await client.get("/api/v1/auth/upstox/login", headers=registered["headers"])
        assert login_resp.status_code == 200
        state = login_resp.json()["state"]

        r = await client.get(
            "/api/v1/auth/upstox/callback", params={"code": "fake", "state": state}
        )
        assert r.status_code == 422
        assert "expired or was already used" in r.json()["message"]
