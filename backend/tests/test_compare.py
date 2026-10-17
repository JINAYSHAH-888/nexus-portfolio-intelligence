"""
POST /analysis/compare -- "what if my portfolio were this instead?" without
mutating the investor's real saved selection.
"""
from __future__ import annotations

import pytest


async def test_compare_with_no_saved_selection_returns_null_current(client, profiled):
    r = await client.post(
        "/api/v1/analysis/compare",
        json={"scheme_ids": ["nippon-large-cap-fund", "nippon-multi-cap-fund"]},
        headers=profiled["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["current"] is None
    assert body["candidate"]["overlap"]["avg_pairwise_jaccard"] is not None
    assert body["candidate"]["plos"]["portfolio_plos"] is not None


async def test_compare_includes_the_real_saved_selection_when_one_exists(client, selected):
    ids = selected["scheme_ids"]
    r = await client.post(
        "/api/v1/analysis/compare",
        json={"scheme_ids": [ids[0], "nippon-multi-cap-fund"]},  # a candidate swap
        headers=selected["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["current"] is not None
    assert set(body["current"]["scheme_ids"]) == set(ids)
    assert set(body["candidate"]["scheme_ids"]) == {ids[0], "nippon-multi-cap-fund"}


async def test_the_saved_selection_is_never_mutated_by_a_comparison(client, selected):
    """THE CORE GUARANTEE. Comparing a hypothetical must never silently change
    what /schemes/selected returns."""
    ids = selected["scheme_ids"]
    before = await client.get("/api/v1/schemes/selected", headers=selected["headers"])

    await client.post(
        "/api/v1/analysis/compare",
        json={"scheme_ids": ["nippon-gilt-fund", "nippon-liquid-fund"]},  # totally different
        headers=selected["headers"],
    )

    after = await client.get("/api/v1/schemes/selected", headers=selected["headers"])
    assert before.json()["selected_scheme_ids"] == after.json()["selected_scheme_ids"]
    assert set(after.json()["selected_scheme_ids"]) == set(ids)


async def test_default_equal_weighting_when_weights_omitted(client, profiled):
    r = await client.post(
        "/api/v1/analysis/compare",
        json={"scheme_ids": ["nippon-large-cap-fund", "nippon-small-cap-fund"]},
        headers=profiled["headers"],
    )
    weights = r.json()["candidate"]["weights"]
    assert weights == pytest.approx(
        {"nippon-large-cap-fund": 0.5, "nippon-small-cap-fund": 0.5}
    )


async def test_unknown_scheme_id_is_rejected(client, profiled):
    r = await client.post(
        "/api/v1/analysis/compare",
        json={"scheme_ids": ["not-a-real-scheme"]},
        headers=profiled["headers"],
    )
    assert r.status_code == 422


async def test_weights_not_covering_scheme_ids_is_rejected(client, profiled):
    r = await client.post(
        "/api/v1/analysis/compare",
        json={
            "scheme_ids": ["nippon-large-cap-fund", "nippon-small-cap-fund"],
            "weights": {"nippon-large-cap-fund": 1.0},  # missing the second scheme
        },
        headers=profiled["headers"],
    )
    assert r.status_code == 422


async def test_weights_not_summing_to_one_is_rejected(client, profiled):
    r = await client.post(
        "/api/v1/analysis/compare",
        json={
            "scheme_ids": ["nippon-large-cap-fund", "nippon-small-cap-fund"],
            "weights": {"nippon-large-cap-fund": 0.5, "nippon-small-cap-fund": 0.3},
        },
        headers=profiled["headers"],
    )
    assert r.status_code == 422


async def test_compare_requires_a_completed_profile(client, registered):
    r = await client.post(
        "/api/v1/analysis/compare",
        json={"scheme_ids": ["nippon-large-cap-fund", "nippon-small-cap-fund"]},
        headers=registered["headers"],
    )
    assert r.status_code == 409  # profile_incomplete, same gate as the other analysis routes


async def test_single_scheme_comparison_is_handled(client, profiled):
    """No pairs to compare against -- must degrade cleanly like the real
    overlap/plos endpoints do for a one-scheme selection."""
    r = await client.post(
        "/api/v1/analysis/compare",
        json={"scheme_ids": ["nippon-large-cap-fund"]},
        headers=profiled["headers"],
    )
    assert r.status_code == 200
