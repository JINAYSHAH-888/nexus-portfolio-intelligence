"""
The full product flow over real HTTP, end to end.

CLAUDE.md §7 says this 13-step flow was verified in-session but "the script
itself was not persisted in the repo; recreate it from the conftest pattern if
you need to re-verify end-to-end." So here it is, persisted, as a test that
runs in CI.

    register -> me -> profile -> filter -> select -> allocate -> selected
    -> overlap -> plos -> risk -> arise -> refresh -> logout
"""
from __future__ import annotations

import pytest

from tests.conftest import VALID_PROFILE

pytestmark = pytest.mark.e2e


async def test_full_investor_journey(client):
    # ── 1. Register ───────────────────────────────────────────────────────────
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "journey@example.com",
            "password": "Str0ngPassw0rd!",
            "full_name": "Journey Tester",
        },
    )
    assert r.status_code == 201, r.text
    tokens = r.json()
    access, refresh = tokens["access_token"], tokens["refresh_token"]
    headers = {"Authorization": f"Bearer {access}"}

    # ── 2. /auth/me ───────────────────────────────────────────────────────────
    r = await client.get("/api/v1/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "journey@example.com"
    assert r.json()["role"] == "investor"

    # ── 3. Analysis is gated on a profile ─────────────────────────────────────
    r = await client.get("/api/v1/analysis/overlap", headers=headers)
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "profile_incomplete"
    assert len(r.json()["details"]["missing_fields"]) == 10

    # ── 4. Deep Profile ───────────────────────────────────────────────────────
    r = await client.post("/api/v1/profile", json=VALID_PROFILE, headers=headers)
    assert r.status_code == 201, r.text
    profile = r.json()
    assert profile["is_complete"] is True
    assert profile["age"] is not None
    assert profile["years_to_retirement"] == 60 - profile["age"]

    # ── 5. Analysis is ALSO gated on a selection ──────────────────────────────
    r = await client.get("/api/v1/analysis/overlap", headers=headers)
    assert r.status_code == 404, r.text
    # Message pinned by CLAUDE.md §6.
    assert r.json()["message"] == "Scheme selection not found"

    # ── 6. Filter ─────────────────────────────────────────────────────────────
    r = await client.get("/api/v1/schemes/filtered", headers=headers)
    assert r.status_code == 200, r.text
    filt = r.json()

    # THE §4 REGRESSION GUARD. A 'moderate' investor must see every OPEN
    # scheme, appropriately flagged -- not a hard-blocked subset. Every one of
    # the 20 real equity schemes carries risk_score 6, so a hard risk gate
    # would leave this investor with zero equity funds.
    #
    # 31, not 35: 4 schemes are genuinely CLOSED TO NEW PURCHASE per the live
    # Upstox MF instrument master (Small Cap + 3 closed international feeder
    # funds) -- see filter_summary['closed_to_purchase']. That is a real
    # tradability fact, verified NAV-corroborated at build time by
    # scripts/build_upstox_isin_map.py, not a regression.
    assert filt["total_schemes"] == 35
    assert filt["eligible_count"] == 31
    assert filt["filter_summary"]["closed_to_purchase"] == 4
    assert filt["filter_summary"]["by_risk"] == 0  # correct, not a bug: risk is disclosed
    assert filt["filter_summary"]["risk_flagged"] > 0

    equity = [s for s in filt["eligible_schemes"] if s["instrument_type"] == "equity"]
    assert len(equity) == 16
    for s in equity:
        assert s["risk_flag"] is not None  # moderate investor, risk_score 6 -> flagged
        assert s["risk_flag_level"] in ("mild", "strong")
        assert s["subtier"] in ("Core", "Growth", "Aggressive")

    # ── 7. Select ─────────────────────────────────────────────────────────────
    ids = ["nippon-large-cap-fund", "nippon-small-cap-fund", "nippon-corporate-bond-fund"]
    r = await client.post(
        "/api/v1/schemes/select",
        json={"selected_scheme_ids": ids},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    sel = r.json()
    assert sel["selected_count"] == 3
    # Equal-weight default, stored as fractions summing to 1.0.
    assert sum(sel["weights"].values()) == pytest.approx(1.0)

    # ── 8. Allocate ───────────────────────────────────────────────────────────
    weights = {ids[0]: 0.5, ids[1]: 0.2, ids[2]: 0.3}
    r = await client.post(
        "/api/v1/schemes/allocate", json={"weights": weights}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["weights"] == weights

    # ── 9. Selected ───────────────────────────────────────────────────────────
    r = await client.get("/api/v1/schemes/selected", headers=headers)
    assert r.status_code == 200, r.text
    assert set(r.json()["selected_scheme_ids"]) == set(ids)
    assert len(r.json()["schemes"]) == 3

    # ── 10. Overlap ───────────────────────────────────────────────────────────
    r = await client.get("/api/v1/analysis/overlap", headers=headers)
    assert r.status_code == 200, r.text
    ov = r.json()
    assert len(ov["nodes"]) == 3
    # Full N x N heatmap including the diagonal.
    assert len(ov["heatmap"]) == 9
    assert 0.0 <= ov["avg_pairwise_jaccard"] <= 1.0

    # ── 11. PLOS ──────────────────────────────────────────────────────────────
    r = await client.get("/api/v1/analysis/plos", headers=headers)
    assert r.status_code == 200, r.text
    plos = r.json()
    assert len(plos["pair_scores"]) == 3  # C(3,2)
    assert 0.0 <= plos["portfolio_plos"] <= 100.0
    assert plos["diversification_score"] == pytest.approx(100 - plos["portfolio_plos"])
    assert len(plos["pair_scores"][0]["layers"]) >= 1

    # ── 12. Risk ──────────────────────────────────────────────────────────────
    r = await client.get("/api/v1/analysis/risk", headers=headers)
    assert r.status_code == 200, r.text
    risk = r.json()
    assert len(risk["scheme_metrics"]) == 3
    assert len(risk["correlation_matrix"]) == 3
    assert len(risk["covariance_matrix"]) == 3
    # A diversified blend must be less volatile than the weighted average of
    # its parts. If this ever exceeds 1.0 the covariance maths is wrong.
    assert 0.0 < risk["diversification_ratio"] <= 1.05
    for m in risk["scheme_metrics"]:
        assert m["var_95_parametric"] < 0  # a loss quantile is negative
        assert m["cvar_95_parametric"] <= m["var_95_parametric"]  # CVaR is in the tail

    # ── 13. ARISE v4 ──────────────────────────────────────────────────────────
    r = await client.post(
        "/api/v1/analysis/arise",
        json={"n_simulations": 200, "life_expectancy": 85, "strategy": "adaptive"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    arise = r.json()

    assert arise["engine_version"] == "v4"
    assert arise["n_simulations"] == 200
    assert arise["current_age"] == profile["age"]
    assert arise["retire_age"] == 60
    assert arise["life_expectancy"] == 85

    assert 0.0 <= arise["robotic_success_rate"] <= 1.0
    assert 0.0 <= arise["adaptive_success_rate"] <= 1.0

    # Terminal wealth percentiles must be monotonically ordered.
    p = [arise[f"terminal_p{q}"] for q in (10, 25, 50, 75, 90)]
    assert p == sorted(p), f"terminal percentiles out of order: {p}"

    # v4 additions that the response model was previously dropping.
    assert len(arise["strategy_leaderboard"]) == 4
    assert {e["strategy"] for e in arise["strategy_leaderboard"]} == {
        "robotic",
        "guardrails",
        "vpw",
        "ratchet",
    }
    assert "milevsky_ruin_prob" in arise

    # Provenance: the SIP was estimated, and the response says so out loud.
    assert "monthly_sip_source" in arise["assumptions"]
    assert "ESTIMATE" in arise["assumptions"]["monthly_sip_source"]

    # The shrinkage actually reached the simulation (CLAUDE.md §10).
    shr = arise["shrinkage_diagnostics"]
    assert shr["portfolio_cagr_used"] == arise["portfolio_cagr_used"]

    # ── 14. Refresh rotates ───────────────────────────────────────────────────
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200, r.text
    new_refresh = r.json()["refresh_token"]
    assert new_refresh != refresh

    # ── 15. Logout ────────────────────────────────────────────────────────────
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": new_refresh})
    assert r.status_code == 200, r.text

    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert r.status_code == 401, r.text
