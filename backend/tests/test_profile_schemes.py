"""Deep Profile (Phase 1) and scheme filter/select/allocate (Phase 2)."""
from __future__ import annotations

import pytest

from tests.conftest import VALID_PROFILE


# ── Profile ───────────────────────────────────────────────────────────────────
async def test_profile_requires_all_ten_fields(client, registered):
    partial = {k: v for k, v in VALID_PROFILE.items() if k != "occupation"}
    r = await client.post("/api/v1/profile", json=partial, headers=registered["headers"])
    assert r.status_code == 422
    assert any(e["field"] == "occupation" for e in r.json()["details"])


async def test_cannot_invest_more_than_you_have(client, registered):
    bad = {**VALID_PROFILE, "investment_amount": 9_000_000, "total_corpus": 4_000_000}
    r = await client.post("/api/v1/profile", json=bad, headers=registered["headers"])
    assert r.status_code == 422
    assert "cannot exceed total_corpus" in str(r.json()["details"])


async def test_retirement_age_must_exceed_current_age(client, registered):
    bad = {**VALID_PROFILE, "date_of_birth": "1960-01-01", "target_retirement_age": 55}
    r = await client.post("/api/v1/profile", json=bad, headers=registered["headers"])
    assert r.status_code == 422


async def test_minor_is_rejected(client, registered):
    from datetime import date

    dob = date.today().replace(year=date.today().year - 15).isoformat()
    r = await client.post(
        "/api/v1/profile",
        json={**VALID_PROFILE, "date_of_birth": dob},
        headers=registered["headers"],
    )
    assert r.status_code == 422


async def test_post_profile_is_an_idempotent_upsert(client, registered):
    h = registered["headers"]
    r1 = await client.post("/api/v1/profile", json=VALID_PROFILE, headers=h)
    r2 = await client.post(
        "/api/v1/profile",
        json={**VALID_PROFILE, "occupation": "Data Engineer"},
        headers=h,
    )
    assert r1.status_code == 201 and r2.status_code == 201
    # Same row, not a duplicate.
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.json()["occupation"] == "Data Engineer"


async def test_put_is_a_partial_update(client, profiled):
    h = profiled["headers"]
    r = await client.put("/api/v1/profile", json={"monthly_income": 300_000}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["monthly_income"] == 300_000
    # The other nine fields must survive.
    assert body["occupation"] == VALID_PROFILE["occupation"]
    assert body["goal"] == VALID_PROFILE["goal"]
    assert body["is_complete"] is True


async def test_partial_update_still_enforces_cross_field_invariants(client, profiled):
    """
    A PUT touching only investment_amount could otherwise push it above an
    UNCHANGED total_corpus -- ProfileCreate's model_validator never sees the
    merged record, so the service re-checks it.
    """
    r = await client.put(
        "/api/v1/profile",
        json={"investment_amount": 99_000_000},
        headers=profiled["headers"],
    )
    assert r.status_code == 422
    assert "cannot exceed" in r.json()["message"]


# ── Filter ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "risk", ["conservative", "moderate", "moderately_high", "aggressive"]
)
async def test_every_risk_appetite_sees_equity(client, registered, risk):
    """
    THE CLAUDE.md §4 REGRESSION GUARD.

    All 20 real Nippon equity schemes carry risk_score_1_to_6 == 6 (a genuine
    SEBI riskometer fact). Under the old hard allow-list, every investor below
    'aggressive' got ZERO eligible equity schemes. Risk is now disclosed, never
    blocked -- so every risk tier must still see every equity scheme that is
    actually open for purchase.

    16, not 20: 4 of the 20 real equity schemes (Small Cap + 3 closed-to-inflow
    international feeders) are genuinely CLOSED TO NEW PURCHASE per the live
    Upstox MF instrument master -- a real tradability fact, verified via
    scripts/build_upstox_isin_map.py, not a bug. See
    filter_summary['closed_to_purchase'].
    """
    await client.post(
        "/api/v1/profile",
        json={**VALID_PROFILE, "risk_appetite": risk},
        headers=registered["headers"],
    )
    r = await client.get("/api/v1/schemes/filtered", headers=registered["headers"])
    assert r.status_code == 200
    body = r.json()

    equity = [s for s in body["eligible_schemes"] if s["instrument_type"] == "equity"]
    assert len(equity) == 16, f"{risk} investor saw {len(equity)}/16 open equity schemes"
    # Hard risk blocks are gone for good.
    assert body["filter_summary"]["by_risk"] == 0
    assert body["filter_summary"]["closed_to_purchase"] == 4

    if risk == "aggressive":
        # Score 6 is inside an aggressive investor's allow-list -> no flag.
        assert all(s["risk_flag"] is None for s in equity)
    else:
        assert all(s["risk_flag"] is not None for s in equity)


async def test_tax_saving_is_the_one_hard_goal_block(client, registered):
    """
    Section 80(C) eligibility is a legal fact about the scheme (ELSS lock-in),
    not a preference -- so it stays a hard gate while every other goal is soft.
    """
    await client.post(
        "/api/v1/profile",
        json={**VALID_PROFILE, "goal": "tax_saving"},
        headers=registered["headers"],
    )
    r = await client.get("/api/v1/schemes/filtered", headers=registered["headers"])
    body = r.json()

    assert body["filter_summary"]["by_goal"] > 0
    for s in body["eligible_schemes"]:
        assert "tax_saving" in s["suitable_for"]
    for f in body["filtered_out"]:
        assert any("Section 80(C)" in reason for reason in f["reasons"])


async def test_debt_minimum_investment_is_a_hard_block(client, registered):
    """Debt schemes carry a real Rs 5,000 minimum; equity ones Rs 500."""
    await client.post(
        "/api/v1/profile",
        json={**VALID_PROFILE, "investment_amount": 1000, "total_corpus": 50_000},
        headers=registered["headers"],
    )
    r = await client.get("/api/v1/schemes/filtered", headers=registered["headers"])
    body = r.json()

    assert body["filter_summary"]["by_investment_amount"] == 15  # all 15 debt schemes
    assert all(s["instrument_type"] == "equity" for s in body["eligible_schemes"])


async def test_equity_subtier_uses_real_volatility(client, profiled):
    r = await client.get("/api/v1/schemes/filtered", headers=profiled["headers"])
    equity = [s for s in r.json()["eligible_schemes"] if s["instrument_type"] == "equity"]
    tiers = {s["subtier"] for s in equity}
    # Real std_deviation spans 13.62%-20.89%, so more than one tier must appear;
    # a single tier would mean the sub-tiering is doing nothing.
    assert tiers.issubset({"Core", "Growth", "Aggressive"})
    assert len(tiers) > 1
    assert all(s["subtier"] is None for s in r.json()["eligible_schemes"]
               if s["instrument_type"] == "debt")


# ── Select / allocate ─────────────────────────────────────────────────────────
async def test_select_defaults_to_equal_weight_fractions(client, profiled):
    ids = ["nippon-large-cap-fund", "nippon-small-cap-fund"]
    r = await client.post(
        "/api/v1/schemes/select",
        json={"selected_scheme_ids": ids},
        headers=profiled["headers"],
    )
    w = r.json()["weights"]
    # Fractions summing to 1.0 -- NOT percentages. PortfolioStatisticsBuilder
    # raises if the sum isn't ~1.0, so 50/50 here would break ARISE later.
    assert w == {ids[0]: pytest.approx(0.5), ids[1]: pytest.approx(0.5)}


async def test_unknown_scheme_id_is_rejected(client, profiled):
    r = await client.post(
        "/api/v1/schemes/select",
        json={"selected_scheme_ids": ["hdfc-flexi-cap-fund"]},
        headers=profiled["headers"],
    )
    assert r.status_code == 422
    assert "hdfc-flexi-cap-fund" in r.json()["details"]["unknown_scheme_ids"]


async def test_duplicate_scheme_ids_are_rejected(client, profiled):
    r = await client.post(
        "/api/v1/schemes/select",
        json={"selected_scheme_ids": ["nippon-large-cap-fund", "nippon-large-cap-fund"]},
        headers=profiled["headers"],
    )
    assert r.status_code == 422


async def test_weights_must_sum_to_one(client, selected):
    ids = selected["scheme_ids"]
    r = await client.post(
        "/api/v1/schemes/allocate",
        json={"weights": {ids[0]: 0.5, ids[1]: 0.2, ids[2]: 0.1}},  # 0.8
        headers=selected["headers"],
    )
    assert r.status_code == 422


async def test_weights_must_cover_exactly_the_selected_schemes(client, selected):
    """
    Pydantic checks the SUM but not the KEY SET. A weights dict that sums to 1.0
    while naming an unselected scheme would sail through and only explode much
    later inside PortfolioStatisticsBuilder. Caught here, with names.
    """
    ids = selected["scheme_ids"]
    r = await client.post(
        "/api/v1/schemes/allocate",
        json={"weights": {ids[0]: 0.5, ids[1]: 0.5}},  # sums to 1.0, missing ids[2]
        headers=selected["headers"],
    )
    assert r.status_code == 422
    assert ids[2] in r.json()["details"]["missing_weights_for"]


async def test_allocate_before_select_is_404(client, profiled):
    r = await client.post(
        "/api/v1/schemes/allocate",
        json={"weights": {"nippon-large-cap-fund": 1.0}},
        headers=profiled["headers"],
    )
    assert r.status_code == 404


async def test_filter_requires_a_complete_profile(client, registered):
    r = await client.get("/api/v1/schemes/filtered", headers=registered["headers"])
    assert r.status_code == 409
    assert r.json()["error"] == "profile_incomplete"
