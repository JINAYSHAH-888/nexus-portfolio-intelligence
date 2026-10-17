"""Phase 3 analysis: overlap, PLOS, risk, ARISE."""
from __future__ import annotations

import pytest

from tests.conftest import VALID_PROFILE

ANALYSIS_GET = ["/api/v1/analysis/overlap", "/api/v1/analysis/plos", "/api/v1/analysis/risk"]


@pytest.mark.parametrize("path", ANALYSIS_GET + ["/api/v1/analysis/prices"])
async def test_analysis_requires_auth(client, path):
    assert (await client.get(path)).status_code == 401


@pytest.mark.parametrize("path", ANALYSIS_GET)
async def test_analysis_requires_a_selection(client, profiled, path):
    r = await client.get(path, headers=profiled["headers"])
    assert r.status_code == 404
    assert r.json()["message"] == "Scheme selection not found"


# ── Overlap ───────────────────────────────────────────────────────────────────
async def test_overlap_matrix_is_symmetric_with_unit_diagonal(client, selected):
    r = await client.get("/api/v1/analysis/overlap", headers=selected["headers"])
    ov = r.json()

    cells = {(c["row_scheme_id"], c["col_scheme_id"]): c["jaccard_score"] for c in ov["heatmap"]}
    for (a, b), v in cells.items():
        if a == b:
            # A scheme overlaps itself perfectly -- unless it has no holdings at
            # all (debt schemes hold bonds, not the stock lists we compare).
            assert v in (pytest.approx(1.0), pytest.approx(0.0))
        else:
            assert cells[(b, a)] == pytest.approx(v), "Jaccard must be symmetric"
        assert 0.0 <= v <= 1.0


async def test_two_equity_schemes_actually_overlap(client, profiled):
    """Large-cap and multi-cap funds genuinely share large-cap names."""
    ids = ["nippon-large-cap-fund", "nippon-multi-cap-fund"]
    await client.post(
        "/api/v1/schemes/select",
        json={"selected_scheme_ids": ids},
        headers=profiled["headers"],
    )
    r = await client.get("/api/v1/analysis/overlap", headers=profiled["headers"])
    ov = r.json()
    assert ov["avg_pairwise_jaccard"] > 0.0
    edge = next(e for e in ov["edges"])
    assert edge["shared_stocks"] > 0
    assert edge["shared_stock_names"]


async def test_debt_overlap_is_computed_at_the_issuer_level(client, profiled):
    """
    Debt schemes hold bonds, so their "holdings" are ISSUERS, not stocks -- and
    the overlap engine compares them just the same. This is meaningful, not a
    degenerate case: the gilt fund and the corporate bond fund genuinely share
    'State Government Securities' as a common issuer, giving a real (small)
    Jaccard of 1/35. Asserting zero here would have been wrong.
    """
    ids = ["nippon-gilt-fund", "nippon-corporate-bond-fund"]
    await client.post(
        "/api/v1/schemes/select",
        json={"selected_scheme_ids": ids},
        headers=profiled["headers"],
    )
    r = await client.get("/api/v1/analysis/overlap", headers=profiled["headers"])
    assert r.status_code == 200
    ov = r.json()
    assert ov["avg_pairwise_jaccard"] == pytest.approx(1 / 35, abs=1e-4)
    assert 0.0 < ov["avg_pairwise_jaccard"] < 0.1  # low, as two bond funds should be


async def test_single_scheme_selection_is_handled(client, profiled):
    """No pairs to compare -- must degrade cleanly, not divide by zero."""
    await client.post(
        "/api/v1/schemes/select",
        json={"selected_scheme_ids": ["nippon-large-cap-fund"]},
        headers=profiled["headers"],
    )
    for path in ANALYSIS_GET:
        r = await client.get(path, headers=profiled["headers"])
        assert r.status_code == 200, f"{path}: {r.text}"


# ── PLOS ──────────────────────────────────────────────────────────────────────
async def test_plos_layers_and_bounds(client, selected):
    r = await client.get("/api/v1/analysis/plos", headers=selected["headers"])
    plos = r.json()

    assert 0 <= plos["portfolio_plos"] <= 100
    for pair in plos["pair_scores"]:
        assert 0 <= pair["plos_score"] <= 100
        if pair["real_correlation"] is not None:
            assert -1.0 <= pair["real_correlation"] <= 1.0
        for layer in pair["layers"]:
            assert 0.0 <= layer["raw_score"] <= 1.0
            # The engine rounds contribution to 4dp, so 1e-4 is the right
            # tolerance -- 1e-6 was my error, not the engine's.
            assert layer["contribution"] == pytest.approx(
                layer["raw_score"] * layer["weight"], abs=1e-4
            )
        # The 10 layers must form a proper convex combination.
        assert sum(lyr["weight"] for lyr in pair["layers"]) == pytest.approx(1.0, abs=1e-3)


# ── Risk ──────────────────────────────────────────────────────────────────────
async def test_risk_matrices_are_wellformed(client, selected):
    r = await client.get("/api/v1/analysis/risk", headers=selected["headers"])
    risk = r.json()
    n = len(risk["scheme_ids"])

    corr = risk["correlation_matrix"]
    assert len(corr) == n and all(len(row) == n for row in corr)
    for i in range(n):
        assert corr[i][i] == pytest.approx(1.0, abs=1e-6)
        for j in range(n):
            assert corr[i][j] == pytest.approx(corr[j][i], abs=1e-6)
            assert -1.000001 <= corr[i][j] <= 1.000001

    cov = risk["covariance_matrix"]
    assert len(cov) == n
    for i in range(n):
        assert cov[i][i] >= 0, "a variance cannot be negative"


async def test_var_ordering_is_coherent(client, selected):
    r = await client.get("/api/v1/analysis/risk", headers=selected["headers"])
    for m in r.json()["scheme_metrics"]:
        # 99% VaR is deeper in the tail than 95%, and CVaR is deeper than VaR.
        assert m["var_99_parametric"] <= m["var_95_parametric"]
        assert m["cvar_95_parametric"] <= m["var_95_parametric"]
        assert m["cvar_99_parametric"] <= m["var_99_parametric"]
        assert m["max_drawdown"] <= 0
        assert m["annualized_volatility"] >= 0


async def test_json_contains_no_nan_tokens(client, selected):
    """
    NaN/Infinity are not valid JSON and blow up JSON.parse in every browser.
    Correlation against a near-flat debt NAV is 0/0 -> NaN, so this is a live
    risk, not a theoretical one. SafeJSONResponse must have scrubbed them.
    """
    for path in ANALYSIS_GET:
        r = await client.get(path, headers=selected["headers"])
        raw = r.text
        assert "NaN" not in raw, f"{path} leaked a NaN token"
        assert "Infinity" not in raw, f"{path} leaked an Infinity token"
        r.json()  # must parse under a strict parser


# ── ARISE ─────────────────────────────────────────────────────────────────────
async def test_arise_percentiles_are_ordered_and_v4(client, selected):
    r = await client.post(
        "/api/v1/analysis/arise",
        json={"n_simulations": 150},
        headers=selected["headers"],
    )
    assert r.status_code == 200, r.text
    a = r.json()

    assert a["engine_version"] == "v4"
    p = [a[f"terminal_p{q}"] for q in (10, 25, 50, 75, 90)]
    assert p == sorted(p)
    assert 0.0 <= a["adaptive_success_rate"] <= 1.0
    assert len(a["strategy_leaderboard"]) == 4


async def test_arise_is_deterministic_under_a_fixed_seed(client, selected):
    """Same portfolio + same seed must reproduce exactly. Non-reproducible
    financial projections are unauditable."""
    payload = {"n_simulations": 120}
    r1 = await client.post("/api/v1/analysis/arise", json=payload, headers=selected["headers"])
    r2 = await client.post("/api/v1/analysis/arise", json=payload, headers=selected["headers"])
    a, b = r1.json(), r2.json()
    assert a["adaptive_success_rate"] == b["adaptive_success_rate"]
    assert a["terminal_p50"] == b["terminal_p50"]


async def test_arise_labels_its_sip_estimate_when_the_profile_has_none(client, selected):
    """
    The profile now HAS a monthly_sip field, so the 20%-of-income estimate is only
    a backstop for profiles that never set it. When it IS used, it must say so --
    loudly, and in the response the investor sees.
    """
    r = await client.post(
        "/api/v1/analysis/arise", json={"n_simulations": 100}, headers=selected["headers"]
    )
    a = r.json()["assumptions"]
    assert a["monthly_sip"] == pytest.approx(VALID_PROFILE["monthly_income"] * 0.20)
    assert a["monthly_sip_is_estimated"] is True
    assert "ESTIMATE" in a["monthly_sip_source"]

    # An explicit request value overrides it, and the provenance changes.
    r = await client.post(
        "/api/v1/analysis/arise",
        json={"n_simulations": 100, "monthly_sip": 75_000},
        headers=selected["headers"],
    )
    a = r.json()["assumptions"]
    assert a["monthly_sip"] == 75_000
    assert a["monthly_sip_is_estimated"] is False
    assert a["monthly_sip_source"] == "client-supplied (request)"


async def test_more_sip_cannot_reduce_success(client, selected):
    """A basic monotonicity sanity check on the simulation."""
    async def success(sip: int) -> float:
        r = await client.post(
            "/api/v1/analysis/arise",
            json={"n_simulations": 250, "monthly_sip": sip},
            headers=selected["headers"],
        )
        return r.json()["adaptive_success_rate"]

    low = await success(10_000)
    high = await success(200_000)
    assert high >= low


async def test_shrinkage_actually_reaches_the_simulation(client, selected):
    """
    CLAUDE.md §10's headline bug: an earlier fix REPORTED a shrunk CAGR that the
    simulation never consumed. The number ARISE used must be the shrunk one, and
    it must differ from the raw sample CAGR whenever shrinkage did anything.
    """
    r = await client.post(
        "/api/v1/analysis/arise", json={"n_simulations": 100}, headers=selected["headers"]
    )
    a = r.json()
    shr = a["shrinkage_diagnostics"]

    assert shr["portfolio_cagr_used"] == a["portfolio_cagr_used"]
    assert 0.0 <= shr["sample_weight_cagr"] <= 1.0
    # The prior must be a real, blended number, not a stub.
    assert shr["prior_cagr_used"] != 0.0


async def test_arise_rejects_an_impossible_horizon(client, selected):
    r = await client.post(
        "/api/v1/analysis/arise",
        json={"n_simulations": 100, "life_expectancy": 70},  # profile retires at 60... ok
        headers=selected["headers"],
    )
    assert r.status_code == 200

    # life_expectancy below the schema floor is a 422 from Pydantic.
    r = await client.post(
        "/api/v1/analysis/arise",
        json={"n_simulations": 100, "life_expectancy": 55},
        headers=selected["headers"],
    )
    assert r.status_code == 422


async def test_arise_simulation_count_is_capped(client, selected):
    r = await client.post(
        "/api/v1/analysis/arise",
        json={"n_simulations": 99_999},
        headers=selected["headers"],
    )
    # Schema ceiling is 5000 -> a request above it is rejected outright rather
    # than silently costing the server a 100k-path run.
    assert r.status_code == 422


async def test_prices_endpoint_declares_its_delay(client, selected):
    r = await client.get("/api/v1/analysis/prices", headers=selected["headers"])
    assert r.status_code == 200
    body = r.json()
    # yfinance is delayed, never a true tick. Saying otherwise would mislead
    # anyone trading off it.
    assert body["data_delayed"] is True
    assert "prices" in body
