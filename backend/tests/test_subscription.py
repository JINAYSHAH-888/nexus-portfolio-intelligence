"""
Subscription-key resolution: aggregating every investor's holdings into one
shared subscription set, ranked and capped for the V3 feed's instrument limit.
"""
from __future__ import annotations

from backend.integrations.upstox.subscription import (
    SchemeSelectionSnapshot,
    aggregate_holding_weights,
    resolve_subscription_keys,
)

HOLDINGS = {
    "scheme-a": [{"name": "Reliance", "weight": 0.5}, {"name": "TCS", "weight": 0.3}],
    "scheme-b": [{"name": "TCS", "weight": 0.4}, {"name": "Infosys", "weight": 0.2}],
}


class TestAggregateHoldingWeights:
    def test_a_single_investor_gets_their_own_weighted_holdings(self):
        selections = [
            SchemeSelectionSnapshot(scheme_ids=["scheme-a"], weights={"scheme-a": 1.0})
        ]
        scores = aggregate_holding_weights(selections, HOLDINGS)
        assert scores["Reliance"] == 0.5
        assert scores["TCS"] == 0.3

    def test_a_stock_held_across_multiple_investors_sums_not_overwrites(self):
        """
        Two investors both hold TCS (via different schemes). The combined
        score must be the SUM, not just the last one processed -- this is
        what makes a widely-held name outrank a name one investor holds
        heavily but nobody else touches.
        """
        selections = [
            SchemeSelectionSnapshot(scheme_ids=["scheme-a"], weights={"scheme-a": 1.0}),
            SchemeSelectionSnapshot(scheme_ids=["scheme-b"], weights={"scheme-b": 1.0}),
        ]
        scores = aggregate_holding_weights(selections, HOLDINGS)
        assert scores["TCS"] == 0.3 + 0.4  # from scheme-a AND scheme-b

    def test_partial_allocation_scales_the_contribution(self):
        """An investor who only put 20% of their money in scheme-a contributes
        20% of that scheme's holding weights, not the full amount."""
        selections = [
            SchemeSelectionSnapshot(
                scheme_ids=["scheme-a", "scheme-b"],
                weights={"scheme-a": 0.2, "scheme-b": 0.8},
            )
        ]
        scores = aggregate_holding_weights(selections, HOLDINGS)
        assert scores["Reliance"] == 0.5 * 0.2
        assert scores["TCS"] == 0.3 * 0.2 + 0.4 * 0.8

    def test_zero_weight_scheme_contributes_nothing(self):
        selections = [
            SchemeSelectionSnapshot(scheme_ids=["scheme-a"], weights={"scheme-a": 0.0})
        ]
        assert aggregate_holding_weights(selections, HOLDINGS) == {}

    def test_no_selections_at_all_is_an_empty_result_not_a_crash(self):
        assert aggregate_holding_weights([], HOLDINGS) == {}


class TestResolveSubscriptionKeys:
    def test_benchmarks_always_make_the_cut(self):
        keys, dropped = resolve_subscription_keys(
            [], HOLDINGS, {}, mode_limit=2000
        )
        assert set(keys) >= {"NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"}
        assert dropped == []

    def test_higher_conviction_names_are_kept_under_a_tight_cap(self):
        """
        With a cap that can only fit ONE stock beyond the benchmarks, the
        widely/heavily-held one (TCS, combined score 0.7) must survive over
        the lighter one (Infosys, 0.2) -- not an arbitrary or alphabetical pick.
        """
        selections = [
            SchemeSelectionSnapshot(scheme_ids=["scheme-a"], weights={"scheme-a": 1.0}),
            SchemeSelectionSnapshot(scheme_ids=["scheme-b"], weights={"scheme-b": 1.0}),
        ]
        instrument_map = {"Reliance": "K_REL", "TCS": "K_TCS", "Infosys": "K_INFY"}

        keys, dropped = resolve_subscription_keys(
            selections, HOLDINGS, instrument_map, mode_limit=3  # 2 benchmarks + 1 stock
        )
        assert "K_TCS" in keys  # combined score 0.7, the highest
        assert "K_TCS" not in dropped

    def test_unmapped_stocks_are_silently_skipped_not_a_crash(self):
        """A stock with no verified Upstox mapping (see equity_mapping.py) must
        be excluded from the subscription, not sent as a None/garbage key."""
        selections = [
            SchemeSelectionSnapshot(scheme_ids=["scheme-a"], weights={"scheme-a": 1.0})
        ]
        # Reliance unmapped (None), TCS mapped.
        instrument_map = {"Reliance": None, "TCS": "K_TCS"}

        keys, _dropped = resolve_subscription_keys(
            selections, HOLDINGS, instrument_map, mode_limit=2000
        )
        assert "K_TCS" in keys
        assert None not in keys
        assert all(isinstance(k, str) for k in keys)

    def test_everything_fits_when_the_cap_is_generous(self):
        selections = [
            SchemeSelectionSnapshot(scheme_ids=["scheme-a"], weights={"scheme-a": 1.0})
        ]
        instrument_map = {"Reliance": "K_REL", "TCS": "K_TCS"}
        keys, dropped = resolve_subscription_keys(
            selections, HOLDINGS, instrument_map, mode_limit=2000
        )
        assert dropped == []
        assert "K_REL" in keys and "K_TCS" in keys
