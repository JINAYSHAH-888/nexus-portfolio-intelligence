"""Regression: with five or fewer schemes, the top-5 list reported a
hardcoded 0.0 overlap for every scheme regardless of the real pair scores."""

from backend.engines.plos_engine import PLOSEngine


def test_small_portfolio_reports_real_overlap():
    engine = PLOSEngine.__new__(PLOSEngine)  # _recommend_top5 needs only names
    engine._schemes = {}
    pair_map = {("a", "b"): 30.0, ("a", "c"): 10.0, ("b", "c"): 20.0}
    out = {
        r["scheme_id"]: r["avg_plos_with_others"]
        for r in engine._recommend_top5(["a", "b", "c"], pair_map)
    }
    assert out == {"a": 20.0, "b": 25.0, "c": 15.0}
