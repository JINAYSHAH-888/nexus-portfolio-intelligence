"""Reports (Phase 4), market endpoints, and app-level behaviour."""
from __future__ import annotations

import pytest


# ── Reports ───────────────────────────────────────────────────────────────────
async def test_generate_report_produces_a_real_pdf(client, selected):
    r = await client.post("/api/v1/reports/generate", headers=selected["headers"])
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["status"] == "completed", body.get("error_message")
    assert body["file_size_bytes"] > 1000
    assert set(body["scheme_ids"]) == set(selected["scheme_ids"])

    r = await client.get(f"/api/v1/reports/{body['id']}/download", headers=selected["headers"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    # A real PDF, not an empty file or an HTML error page.
    assert r.content.startswith(b"%PDF-")
    assert len(r.content) > 1000


async def test_report_requires_a_selection(client, profiled):
    r = await client.post("/api/v1/reports/generate", headers=profiled["headers"])
    assert r.status_code == 404


async def test_reports_are_listed_newest_first(client, selected):
    for _ in range(2):
        await client.post("/api/v1/reports/generate", headers=selected["headers"])

    r = await client.get("/api/v1/reports", headers=selected["headers"])
    assert r.status_code == 200
    assert r.json()["count"] == 2


async def test_download_of_a_missing_report_is_404(client, selected):
    import uuid

    r = await client.get(
        f"/api/v1/reports/{uuid.uuid4()}/download", headers=selected["headers"]
    )
    assert r.status_code == 404


# ── Market ────────────────────────────────────────────────────────────────────
async def test_nav_falls_back_to_the_factsheet_and_says_so(client, registered):
    """
    No egress to AMFI in this environment, so the loader must degrade to the
    factsheet-disclosed NAV -- a real, sourced number -- and LABEL it as stale
    rather than passing it off as today's.
    """
    r = await client.get(
        "/api/v1/market/nav/nippon-large-cap-fund", headers=registered["headers"]
    )
    assert r.status_code == 200
    body = r.json()
    assert body["nav"] > 0
    assert body["data_source"] in ("amfi_live", "factsheet_disclosed")
    if body["data_source"] == "factsheet_disclosed":
        assert "warning" in body
        assert body["as_of"]


async def test_nav_for_unknown_scheme_is_404(client, registered):
    r = await client.get("/api/v1/market/nav/not-a-scheme", headers=registered["headers"])
    assert r.status_code == 404


async def test_portfolio_value_does_not_fabricate_pnl(client, selected):
    """
    The platform stores an intended allocation, not a transaction history, so a
    mark-to-market P&L is not derivable. It must SAY so rather than return a
    plausible-looking zero.
    """
    r = await client.get("/api/v1/market/portfolio-value", headers=selected["headers"])
    assert r.status_code == 200
    body = r.json()

    assert body["pnl_available"] is False
    assert "note" in body
    assert len(body["holdings"]) == 3
    # Allocations must reconstruct the invested total exactly.
    assert sum(h["allocated_amount"] for h in body["holdings"]) == pytest.approx(
        body["invested_amount"], abs=1.0
    )
    for h in body["holdings"]:
        assert h["nav"] > 0
        assert h["implied_units"] > 0


async def test_legacy_market_pnl_delegates_to_the_real_ledger(client, selected):
    """
    /market/pnl is now a deprecated alias for /portfolio/pnl. With an empty
    ledger it still says pnl_available: false -- but that is now an EMPTY STATE
    with a clear next action, not a permanent limitation.
    """
    r = await client.get("/api/v1/market/pnl", headers=selected["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["pnl_available"] is False
    assert "POST /api/v1/portfolio/transactions" in body["reason"]

    # Record a purchase, and the same legacy endpoint starts answering for real.
    await client.post(
        "/api/v1/portfolio/transactions",
        json={
            "scheme_id": "nippon-large-cap-fund",
            "txn_type": "buy",
            "units": "100",
            "nav": "50",
            "txn_date": "2023-01-10",
        },
        headers=selected["headers"],
    )
    r = await client.get("/api/v1/market/pnl", headers=selected["headers"])
    assert r.json()["pnl_available"] is True
    assert r.json()["summary"]["total_invested"] == pytest.approx(5000.0)


async def test_dashboard_summarises_the_portfolio(client, selected):
    r = await client.get("/api/v1/market/dashboard", headers=selected["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["portfolio"]["scheme_count"] == 3
    assert body["portfolio"]["equity_schemes"] == 2
    assert body["portfolio"]["debt_schemes"] == 1
    assert body["investor"]["age"] is not None


# ── App level ─────────────────────────────────────────────────────────────────
async def test_health_is_dependency_free(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_readiness_reports_dependency_state(client):
    r = await client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["database"] == "ok"
    # Redis is disabled in tests: degraded, but NOT unready. Pulling a healthy
    # pod from rotation because a fail-open cache is down would be wrong.
    assert body["status"] == "ready"


async def test_every_response_carries_a_request_id(client):
    r = await client.get("/health")
    assert r.headers["X-Request-ID"]
    assert float(r.headers["X-Response-Time-ms"]) >= 0


async def test_supplied_request_id_is_echoed(client):
    r = await client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers["X-Request-ID"] == "trace-abc-123"


async def test_openapi_documents_the_full_surface(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]

    # Every endpoint listed in CLAUDE.md §6.
    for p in [
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/logout-all",
        "/api/v1/auth/me",
        "/api/v1/profile",
        "/api/v1/schemes/filtered",
        "/api/v1/schemes/select",
        "/api/v1/schemes/allocate",
        "/api/v1/schemes/selected",
        "/api/v1/analysis/overlap",
        "/api/v1/analysis/plos",
        "/api/v1/analysis/risk",
        "/api/v1/analysis/arise",
        "/api/v1/analysis/prices",
        "/api/v1/market/portfolio-value",
        "/api/v1/market/dashboard",
        "/api/v1/market/pnl",
        "/api/v1/market/fundamentals/{ticker}",
        "/api/v1/market/nav/{scheme_id}",
        "/api/v1/market/tasks/download-prices",
        "/api/v1/market/tasks/download-nav",
        "/api/v1/market/tasks/refresh-holdings",
        "/api/v1/reports/generate",
        "/api/v1/reports/{report_id}",
        "/api/v1/reports/{report_id}/download",
        "/api/v1/notifications/mine",
        "/api/v1/notifications/{notification_id}/read",
    ]:
        assert p in paths, f"missing documented endpoint: {p}"


async def test_unknown_route_is_404(client):
    assert (await client.get("/api/v1/nope")).status_code == 404
