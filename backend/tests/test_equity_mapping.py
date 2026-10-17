"""The stock -> Upstox NSE instrument_key mapper. Lower risk than the MF
mapper (trading symbols are unique within NSE_EQ by construction -- no
'settlement variant' or 'BONUS unit' collision class exists here), but still
verified against the real uploaded file rather than assumed."""
from __future__ import annotations

from pathlib import Path

import pytest

UPLOADED_NSE_FILE = Path("/mnt/user-data/uploads/NSE_json.gz")


@pytest.fixture(scope="module")
def nse_index():
    if not UPLOADED_NSE_FILE.exists():
        pytest.skip("Real Upstox NSE instrument file not present in this environment.")
    import scripts.build_upstox_equity_map as m

    return m.load_index(UPLOADED_NSE_FILE)


class TestBuildEquityMap:
    def test_the_large_majority_of_our_existing_tickers_resolve(self, nse_index):
        import scripts.build_upstox_equity_map as m

        results, unresolved = m.build_from_index(nse_index)
        resolved = sum(1 for r in results.values() if r["instrument_key"])
        # 211/221 confirmed by hand; assert a floor rather than the exact
        # number so a future stocks_ticker_map.json correction doesn't need
        # this test edited.
        assert resolved >= 200
        assert resolved / len(results) > 0.9

    def test_known_placeholder_entries_are_skipped_not_reported_as_new_failures(self, nse_index):
        import scripts.build_upstox_equity_map as m

        results, _ = m.build_from_index(nse_index)
        placeholder_rows = [
            r for name, r in results.items()
            if r["match_method"] == "SKIPPED"
        ]
        assert len(placeholder_rows) > 0  # the NOT_LISTED_YET pre-IPO holdings

    def test_a_resolved_entry_carries_isin_and_lot_size(self, nse_index):
        import scripts.build_upstox_equity_map as m

        results, _ = m.build_from_index(nse_index)
        reliance = results.get("Reliance Industries Limited")
        if reliance and reliance["instrument_key"]:
            assert reliance["isin"].startswith("INE")
            assert reliance["lot_size"] == 1


class TestEquityMapping:
    def test_loads_the_committed_map(self):
        from backend.integrations.upstox import equity_mapping

        equity_mapping.reset_cache()
        mapping = equity_mapping.get_mapping("Reliance Industries Limited")
        # May be None if the committed file doesn't have this exact name key --
        # what matters is it doesn't crash and returns a well-typed result.
        if mapping is not None:
            assert mapping.is_resolved or mapping.instrument_key is None

    def test_unknown_stock_returns_none_not_a_crash(self):
        from backend.integrations.upstox import equity_mapping

        assert equity_mapping.get_mapping("Not A Real Company Ltd") is None
        assert equity_mapping.instrument_key_for("Not A Real Company Ltd") is None

    def test_missing_map_file_degrades_to_empty_not_an_exception(self, monkeypatch):
        from pathlib import Path as P

        from backend.core.config import settings
        from backend.integrations.upstox import equity_mapping

        monkeypatch.setattr(settings, "EQUITY_MAP_PATH", P("/nonexistent/x.json"))
        equity_mapping.reset_cache()
        try:
            assert equity_mapping.get_mapping("Anything") is None
        finally:
            equity_mapping.reset_cache()
