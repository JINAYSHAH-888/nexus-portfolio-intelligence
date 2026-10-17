"""
GET /schemes/{scheme_id} -- the per-scheme dashboard endpoint.

Exposes fields (fund_manager, sector_allocation, sip_returns, investment
philosophy, top holdings) that existed in the data all along but had no route
until now.
"""
from __future__ import annotations


async def test_returns_the_rich_fields_not_available_on_the_list_view(client, registered):
    r = await client.get(
        "/api/v1/schemes/nippon-large-cap-fund", headers=registered["headers"]
    )
    assert r.status_code == 200
    body = r.json()

    assert body["id"] == "nippon-large-cap-fund"
    assert body["fund_manager"]
    assert len(body["sector_allocation"]) > 0
    assert body["sector_allocation"][0]["sector"]
    assert "since_inception" in body["sip_returns"]
    assert body["subtier"] in ("Core", "Growth", "Aggressive")


async def test_debt_scheme_carries_debt_specific_fields(client, registered):
    r = await client.get(
        "/api/v1/schemes/nippon-gilt-fund", headers=registered["headers"]
    )
    body = r.json()
    assert body["instrument_type"] == "debt"
    assert body["ytm"] is not None
    assert body["modified_duration"] is not None
    assert body["subtier"] is None  # subtiering is an equity-only concept


async def test_top_holdings_are_present_and_weight_sorted(client, registered):
    r = await client.get(
        "/api/v1/schemes/nippon-large-cap-fund", headers=registered["headers"]
    )
    holdings = r.json()["top_holdings"]
    assert len(holdings) > 0
    weights = [h["weight"] for h in holdings]
    assert weights == sorted(weights, reverse=True)


async def test_unknown_scheme_is_404(client, registered):
    r = await client.get(
        "/api/v1/schemes/not-a-real-scheme-id", headers=registered["headers"]
    )
    assert r.status_code == 404


async def test_requires_only_login_not_a_completed_profile(client, registered):
    """None of this data is investor-specific -- gating it on a completed
    Deep Profile the way /filtered is gated would be an unnecessary blocker."""
    r = await client.get(
        "/api/v1/schemes/nippon-large-cap-fund", headers=registered["headers"]
    )
    assert r.status_code == 200


async def test_unauthenticated_request_is_401(client):
    r = await client.get("/api/v1/schemes/nippon-large-cap-fund")
    assert r.status_code == 401


class TestRouteOrderingRegression:
    """
    THE BUG THIS CLASS EXISTS TO PREVENT. `/{scheme_id}` is a dynamic route
    registered in the SAME router as several static ones (/filtered, /select,
    /allocate, /selected). FastAPI/Starlette dispatch requests in ROUTE
    REGISTRATION ORDER -- if /{scheme_id} had been declared before /filtered,
    a request to GET /schemes/filtered would match it FIRST with
    scheme_id="filtered" and never reach the real /filtered handler at all.
    This was caught and fixed during development; these tests pin it so a
    future refactor can't silently reintroduce it.
    """

    async def test_filtered_route_is_not_shadowed(self, client, profiled):
        r = await client.get("/api/v1/schemes/filtered", headers=profiled["headers"])
        assert r.status_code == 200
        # The real /filtered response has this key; the detail endpoint's 404
        # error body does not.
        assert "eligible_schemes" in r.json()

    async def test_selected_route_is_not_shadowed(self, client, profiled):
        r = await client.get("/api/v1/schemes/selected", headers=profiled["headers"])
        # 404 here is the REAL "no selection yet" response (SelectionNotFoundError),
        # not a coincidental match -- confirmed by the exact pinned message.
        assert r.status_code == 404
        assert r.json()["message"] == "Scheme selection not found"

    async def test_a_scheme_literally_named_filtered_would_still_be_ambiguous(self):
        """
        Documents the residual limitation rather than hiding it: no real scheme
        is named 'filtered', 'select', 'allocate', or 'selected' (verified
        against the real 35-scheme dataset), so this collision is
        theoretical today -- but anyone adding a scheme with one of those
        literal IDs would silently resurrect this bug. Out of scope to fully
        eliminate (would need a path-prefix, e.g. /schemes/detail/{id}) but
        worth a standing note.
        """
        import json

        from backend.core.config import settings

        ids = {s["id"] for s in json.loads(settings.SCHEMES_PATH.read_text())}
        reserved = {"filtered", "select", "allocate", "selected"}
        assert ids.isdisjoint(reserved)
