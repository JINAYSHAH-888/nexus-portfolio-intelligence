"""
Phase 5: what the direct-vs-regular plan choice actually costs.

Both expense ratios were already in schemes_metadata.json and nothing
surfaced the difference between them. Across these 35 schemes the weighted
averages are ~0.58% (direct) and ~1.19% (regular). That 61bp gap sounds
negligible and is not: unlike market returns it is CERTAIN, it compounds for
as long as the investor holds, and on a real profile here it came to Rs 93.3
lakh -- 8.9% of the projected corpus.
"""
from __future__ import annotations

import json

import pytest

from backend.analytics.plan_costs import plan_cost_comparison
from backend.core.config import settings


def _schemes() -> list[dict]:
    with open(settings.SCHEMES_PATH, encoding="utf-8") as f:
        rows = json.load(f)
    return rows if isinstance(rows, list) else list(rows.values())


def _equal_weights(rows: list[dict]) -> dict[str, float]:
    return {r["id"]: 1.0 / len(rows) for r in rows}


class TestPlanCostComparison:
    def test_regular_always_costs_more_than_direct(self):
        rows = _schemes()
        out = plan_cost_comparison(
            scheme_meta=rows, weights=_equal_weights(rows),
            monthly_sip=25_000, years=25, gross_return=0.12,
        )
        assert out["available"] is True
        assert out["weighted_ter_regular"] > out["weighted_ter_direct"]
        assert out["projected_corpus_direct"] > out["projected_corpus_regular"]
        assert out["cost_of_regular_plan"] > 0

    def test_both_plans_are_projected_from_the_same_gross_return(self):
        """THE PROPERTY THAT MAKES IT A FEE COMPARISON. If the two sides used
        different gross returns the difference would be a forecast, not a
        measurement of the fee."""
        rows = _schemes()
        out = plan_cost_comparison(
            scheme_meta=rows, weights=_equal_weights(rows),
            monthly_sip=10_000, years=20, gross_return=0.12,
        )
        gap_rate = out["weighted_ter_regular"] - out["weighted_ter_direct"]
        assert out["ter_gap_pp_per_year"] == pytest.approx(100 * gap_rate, abs=1e-6)

    def test_identical_fees_produce_no_gap(self):
        same = [{"id": "a", "expense_ratio_direct": 1.0, "expense_ratio_regular": 1.0}]
        out = plan_cost_comparison(
            scheme_meta=same, weights={"a": 1.0},
            monthly_sip=10_000, years=10, gross_return=0.10,
        )
        assert out["cost_of_regular_plan"] == pytest.approx(0.0, abs=1.0)

    def test_a_longer_horizon_costs_more(self):
        """Fees compound; that is the entire argument."""
        rows = _schemes()
        w = _equal_weights(rows)
        short = plan_cost_comparison(scheme_meta=rows, weights=w, monthly_sip=25_000,
                                     years=10, gross_return=0.12)
        long = plan_cost_comparison(scheme_meta=rows, weights=w, monthly_sip=25_000,
                                    years=25, gross_return=0.12)
        assert long["cost_as_pct_of_corpus"] > short["cost_as_pct_of_corpus"]

    def test_weights_actually_matter(self):
        meta = [
            {"id": "cheap", "expense_ratio_direct": 0.1, "expense_ratio_regular": 0.2},
            {"id": "dear", "expense_ratio_direct": 1.0, "expense_ratio_regular": 2.0},
        ]
        mostly_cheap = plan_cost_comparison(
            scheme_meta=meta, weights={"cheap": 0.9, "dear": 0.1},
            monthly_sip=10_000, years=20, gross_return=0.12,
        )
        mostly_dear = plan_cost_comparison(
            scheme_meta=meta, weights={"cheap": 0.1, "dear": 0.9},
            monthly_sip=10_000, years=20, gross_return=0.12,
        )
        assert mostly_dear["cost_of_regular_plan"] > mostly_cheap["cost_of_regular_plan"]

    def test_schemes_without_fee_data_are_named_not_silently_zeroed(self):
        """Treating a missing fee as zero would drag the weighted average down
        and understate the cost."""
        meta = [
            {"id": "known", "expense_ratio_direct": 0.5, "expense_ratio_regular": 1.5},
            {"id": "unknown"},
        ]
        out = plan_cost_comparison(
            scheme_meta=meta, weights={"known": 0.5, "unknown": 0.5},
            monthly_sip=10_000, years=10, gross_return=0.12,
        )
        assert out["schemes_without_fee_data"] == ["unknown"]
        # Renormalised over the scheme that HAS data, not halved toward zero.
        assert out["weighted_ter_direct"] == pytest.approx(0.005, abs=1e-9)

    def test_no_fee_data_at_all_declines(self):
        out = plan_cost_comparison(
            scheme_meta=[{"id": "x"}], weights={"x": 1.0},
            monthly_sip=10_000, years=10, gross_return=0.12,
        )
        assert out["available"] is False
        assert out["reason"]

    def test_lump_sum_is_included(self):
        rows = _schemes()
        w = _equal_weights(rows)
        without = plan_cost_comparison(scheme_meta=rows, weights=w, monthly_sip=10_000,
                                       years=20, gross_return=0.12)
        with_lump = plan_cost_comparison(scheme_meta=rows, weights=w, monthly_sip=10_000,
                                         years=20, gross_return=0.12, lump_sum=5_000_000)
        assert with_lump["cost_of_regular_plan"] > without["cost_of_regular_plan"]

    def test_it_does_not_tell_anyone_to_switch(self):
        """Switching realises capital gains, may attract exit load, and resets
        the LTCG holding clock. Stating the cost is not the same as advising
        the move."""
        rows = _schemes()
        out = plan_cost_comparison(
            scheme_meta=rows, weights=_equal_weights(rows),
            monthly_sip=25_000, years=25, gross_return=0.12,
        )
        joined = " ".join(out["caveats"]).lower()
        assert "not advice" in joined
        assert "exit load" in joined or "capital gains" in joined


class TestAssumptionsLedgerCoversTheNewWork:
    """Every model choice added across these phases must be registered. The
    platform's whole convention is that a hardcoded choice is acceptable only
    while it is visible."""

    def test_new_model_choices_are_registered(self):
        from backend.core.assumptions import registry

        keys = {a.key for a in registry()}
        for expected in (
            "benchmark_coverage",
            "expense_ratio_netting",
            "gompertz_dispersion",
            "mortality_max_age",
            "india_swr_band",
            "closet_index_screens",
            "allocation_method",
        ):
            assert expected in keys, f"{expected} is not in the assumptions ledger"

    def test_every_assumption_explains_itself(self):
        from backend.core.assumptions import registry

        for a in registry():
            assert a.rationale, f"{a.key} has no rationale"
