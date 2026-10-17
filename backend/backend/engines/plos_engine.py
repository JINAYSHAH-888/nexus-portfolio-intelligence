"""
PLOS Engine — Portfolio Level Overlap Scoring with Real Market Data.

Computes actual portfolio return correlation between schemes using real NSE
stock prices from yfinance (not just name-matching Jaccard).

The key insight:
  Two schemes may hold different stocks but those stocks may be highly
  correlated (e.g. HDFC Bank vs ICICI Bank). Name-matching misses this.
  Real correlation captures it.

Scoring approach:
  PRIMARY (60% of score):  Real return correlation between scheme portfolios
  SECONDARY (40% of score): Holdings-based overlap layers (fast, no network)

Top-5 recommendation:
  Given the investor's selected schemes, identifies the 5 that together
  minimize total pairwise correlation — best diversification.

Layers:
  C01  Real portfolio return correlation     (yfinance prices × holding weights)
  C02  Real sector-weighted correlation      (sector return proxies)
  L01  Direct stock overlap (Jaccard)        (fallback/complement)
  L02  Top-10 holding overlap                (high-conviction positions)
  L03  Sector allocation cosine similarity
  L04  Weighted stock overlap                (Σ min(w_a, w_b))
  L05  Category / benchmark / manager meta
  L06  Risk profile proximity
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger(__name__)

# ── Layer weights — must sum to 1.0 ───────────────────────────────────────────
LAYER_WEIGHTS = {
    # Real market data layers (dominant weight)
    "C01_real_return_correlation":   0.30,
    "C02_sector_return_correlation": 0.10,
    # Holdings-based layers (complement + fallback when prices unavailable)
    "L01_direct_stock_overlap":      0.10,
    "L02_top10_overlap":             0.07,
    "L03_sector_cosine":             0.07,
    "L04_weighted_stock_overlap":    0.07,
    "L05_category_benchmark":        0.07,
    "L06_risk_profile_proximity":    0.05,
    # Alpha Vantage fundamental layers (enrichment)
    "L07_fundamental_quality":       0.09,   # ROE, D/E, revenue growth
    "L08_valuation_bucket":          0.08,   # P/E bucket: Value/Blend/Growth
}
assert abs(sum(LAYER_WEIGHTS.values()) - 1.0) < 1e-9


@dataclass
class LayerScore:
    layer_id: str
    description: str
    raw_score: float        # 0..1
    weight: float
    contribution: float     # raw_score * weight
    data_source: str        # "real_market_data" | "holdings_data" | "metadata"


@dataclass
class PairPLOS:
    scheme_a_id: str
    scheme_b_id: str
    scheme_a_name: str
    scheme_b_name: str
    plos_score: float                   # 0-100
    real_correlation: float | None      # actual Pearson correlation, -1..1
    layers: list[LayerScore]
    interpretation: str
    data_quality: str                   # "real" | "synthetic" | "holdings_only"


@dataclass
class PLOSResult:
    scheme_ids: list[str]
    pair_scores: list[PairPLOS]
    portfolio_plos: float               # average pairwise score
    diversification_score: float        # 100 - portfolio_plos
    interpretation: str
    top_5_recommendation: list[dict]    # best 5 schemes by diversification
    most_overlapping_pair: tuple[str, str] | None
    least_overlapping_pair: tuple[str, str] | None
    data_source: str                    # "real_nse_prices" | "synthetic_calibrated"


def _interpret(score: float) -> str:
    if score >= 70: return "Very High overlap — minimal diversification benefit"
    if score >= 50: return "High overlap — significant shared exposure"
    if score >= 30: return "Moderate overlap — some shared risk"
    return "Low overlap — good diversification"


class PLOSEngine:
    """
    Computes PLOS scores using real NSE price correlation as the primary signal.

    Usage:
        engine = PLOSEngine(
            schemes_path="backend/data/schemes_metadata.json",
            holdings_path="backend/data/sample_holdings.json",
        )
        result = engine.compute_plos(["nippon-large-cap-fund", "nippon-multi-cap-fund"])
    """

    def __init__(
        self,
        schemes_path: str | Path,
        holdings_path: str | Path,
        price_period: str = "3y",
        force_synthetic: bool = False,
    ) -> None:
        self._schemes: dict[str, dict] = {}
        self._holdings: dict[str, list[dict]] = {}
        self._price_period = price_period
        self._force_synthetic = force_synthetic
        self._portfolio_return_cache: dict[str, "pd.Series"] = {}
        self._fundamental_cache: dict[str, dict] = {}  # scheme_id → {ticker → FundamentalData}
        self._load(Path(schemes_path), Path(holdings_path))
        self._market_loader = self._init_loader()
        self._av_loader = self._init_av_loader()

    def _init_av_loader(self):
        try:
            from backend.data_loaders.alpha_vantage_loader import AlphaVantageLoader
            return AlphaVantageLoader()
        except Exception as e:
            logger.warning(f"Could not init AlphaVantageLoader: {e}")
            return None

    def _init_loader(self):
        try:
            from backend.data_loaders.market_data import MarketDataLoader
            return MarketDataLoader(force_synthetic=self._force_synthetic)
        except Exception as e:
            logger.warning(f"Could not init MarketDataLoader: {e}")
            return None

    def _load(self, schemes_path: Path, holdings_path: Path) -> None:
        with open(schemes_path, encoding="utf-8") as f:
            schemes_list = json.load(f)
        self._schemes = {s["id"]: s for s in schemes_list}

        with open(holdings_path, encoding="utf-8") as f:
            hdata = json.load(f)

        if isinstance(hdata, dict):
            for sid, data in hdata.items():
                raw = data.get("holdings", []) if isinstance(data, dict) else data
                self._holdings[sid] = [
                    h for h in raw
                    if not h.get("is_category_subtotal", False)
                    and h.get("name", "").lower() not in (
                        "equity less than 1% of corpus",
                        "cash and other receivables", "grand total"
                    )
                ]
        for s in schemes_list:
            if s["id"] not in self._holdings and s.get("holdings"):
                self._holdings[s["id"]] = s["holdings"]

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _stock_set(self, sid: str, top10_only: bool = False) -> set[str]:
        h = self._holdings.get(sid, [])
        if top10_only:
            h = [x for x in h if x.get("is_top_10")]
        return {x["name"].strip().rstrip("*").lower() for x in h if x.get("weight", 0) > 0}

    def _weight_map(self, sid: str) -> dict[str, float]:
        return {
            x["name"].strip().rstrip("*").lower(): x.get("weight", 0.0)
            for x in self._holdings.get(sid, []) if x.get("weight", 0) > 0
        }

    def _sector_vec(self, sid: str) -> dict[str, float]:
        sectors = self._schemes.get(sid, {}).get("sector_allocation") or []
        out: dict[str, float] = {}
        for s in sectors:
            try:
                v = s.get("weight", 0)
                if isinstance(v, str): v = float(v.replace("%", ""))
                out[str(s.get("sector", "")).strip().lower()] = float(v)
            except (ValueError, TypeError):
                pass
        return out

    @staticmethod
    def _jaccard(a: set, b: set) -> float:
        if not a or not b: return 0.0
        return len(a & b) / len(a | b)

    @staticmethod
    def _cosine(v1: dict, v2: dict) -> float:
        keys = set(v1) | set(v2)
        if not keys: return 0.0
        dot = sum(v1.get(k, 0) * v2.get(k, 0) for k in keys)
        m1 = math.sqrt(sum(v**2 for v in v1.values()))
        m2 = math.sqrt(sum(v**2 for v in v2.values()))
        return dot / (m1 * m2) if m1 and m2 else 0.0

    @staticmethod
    def _weighted_overlap(wa: dict, wb: dict) -> float:
        shared = set(wa) & set(wb)
        return min(sum(min(wa[s], wb[s]) for s in shared) / 100.0, 1.0)

    @staticmethod
    def _safe_sim(a, b, max_diff: float) -> float:
        if a is None or b is None: return 0.5
        return float(max(0.0, 1.0 - abs(a - b) / max_diff))

    # ── Real market data correlation ────────────────────────────────────────────
    def _get_portfolio_returns(self, sid: str) -> "pd.Series | None":
        """
        Fetch or compute real portfolio return series for a scheme.
        Caches result per scheme_id.
        """
        if sid in self._portfolio_return_cache:
            return self._portfolio_return_cache[sid]

        if self._market_loader is None:
            return None

        holdings = self._holdings.get(sid, [])
        if not holdings:
            return None

        try:
            result = self._market_loader.get_scheme_portfolio_returns(
                sid, holdings, period=self._price_period
            )
            series = result.returns
            if len(series) < 60:  # need at least 60 days
                return None
            self._portfolio_return_cache[sid] = series
            return series
        except Exception as e:
            logger.warning(f"Could not get portfolio returns for {sid}: {e}")
            return None

    def _real_correlation(self, sid_a: str, sid_b: str) -> tuple[float, str]:
        """
        Compute Pearson correlation between actual portfolio return series.

        Returns (correlation_as_similarity_0_to_1, data_quality_label)
        """
        import pandas as pd

        r_a = self._get_portfolio_returns(sid_a)
        r_b = self._get_portfolio_returns(sid_b)

        if r_a is None or r_b is None:
            # Fall back to Jaccard as proxy
            j = self._jaccard(self._stock_set(sid_a), self._stock_set(sid_b))
            return j, "holdings_only"

        # Align on common dates
        aligned = pd.DataFrame({"a": r_a, "b": r_b}).dropna()
        if len(aligned) < 60:
            j = self._jaccard(self._stock_set(sid_a), self._stock_set(sid_b))
            return j, "holdings_only"

        corr = float(aligned["a"].corr(aligned["b"]))
        if math.isnan(corr):
            corr = 0.0

        # Convert Pearson (-1..1) to similarity (0..1)
        # -1 = perfectly inverse = 0 overlap, +1 = perfectly same = max overlap
        similarity = (corr + 1) / 2

        data_quality = "synthetic" if self._force_synthetic else "real"
        return similarity, data_quality

    def _sector_return_correlation(self, sid_a: str, sid_b: str) -> float:
        """
        Cosine similarity on sector weight vectors — proxy for sector return overlap.
        Two schemes with identical sector weights will have identical sector returns
        regardless of which specific stocks they hold within those sectors.
        """
        return self._cosine(self._sector_vec(sid_a), self._sector_vec(sid_b))

    def _get_fundamentals_sync(self, sid: str) -> dict:
        """
        Get Alpha Vantage fundamental data for top holdings of a scheme.
        Returns cached result if available, else returns empty dict
        (async fetch happens in compute_plos → prefetch_fundamentals).
        """
        return self._fundamental_cache.get(sid, {})

    async def prefetch_fundamentals(self, scheme_ids: list[str]) -> None:
        """
        Pre-fetch Alpha Vantage fundamentals for all scheme top-holdings.
        Called once before compute_plos to populate the fundamental cache.
        This respects the 5 calls/min rate limit via the AV loader.
        """
        if self._av_loader is None or not self._av_loader.is_configured():
            return

        from backend.data_loaders.market_data import MarketDataLoader
        mloader = MarketDataLoader()

        for sid in scheme_ids:
            if sid in self._fundamental_cache:
                continue  # already cached

            holdings = self._holdings.get(sid, [])
            top10 = sorted(holdings, key=lambda h: h.get("weight", 0), reverse=True)[:10]
            tickers = []
            for h in top10:
                t = mloader.stock_name_to_ticker(h.get("name", "").rstrip("*").strip())
                if t and t != "NOT_LISTED_YET":
                    tickers.append(t)

            if tickers:
                fundamentals = await self._av_loader.get_bulk_fundamentals(
                    tickers, max_api_calls=3  # conservative: 3 live calls per scheme
                )
                self._fundamental_cache[sid] = fundamentals

    # ── 6-group layer computation ───────────────────────────────────────────────
    def _compute_layers(self, sid_a: str, sid_b: str) -> tuple[list[LayerScore], float | None, str]:
        sa = self._schemes.get(sid_a, {})
        sb = self._schemes.get(sid_b, {})

        stocks_a = self._stock_set(sid_a)
        stocks_b = self._stock_set(sid_b)
        top10_a = self._stock_set(sid_a, top10_only=True)
        top10_b = self._stock_set(sid_b, top10_only=True)

        # Real correlation (primary)
        real_sim, dq = self._real_correlation(sid_a, sid_b)
        real_pearson = (real_sim * 2) - 1  # back-convert for reporting

        sector_corr = self._sector_return_correlation(sid_a, sid_b)

        # Category + benchmark + manager (L05)
        cat_match = 1.0 if sa.get("category") and sa.get("category") == sb.get("category") else 0.0
        bench_match = 1.0 if sa.get("benchmark") and sa.get("benchmark") == sb.get("benchmark") else 0.0
        mgr_match = self._manager_sim(sa.get("fund_manager", ""), sb.get("fund_manager", ""))
        meta_score = (cat_match * 0.4 + bench_match * 0.4 + mgr_match * 0.2)

        # Risk proximity (L06)
        risk_prox = self._safe_sim(
            sa.get("risk_score_1_to_6"), sb.get("risk_score_1_to_6"), 5.0
        )

        # Alpha Vantage fundamental layers (L07, L08)
        fund_a = self._get_fundamentals_sync(sid_a)
        fund_b = self._get_fundamentals_sync(sid_b)

        # L07: Fundamental quality overlap
        # Both schemes holding same quality tier = higher structural overlap
        if fund_a and fund_b and self._av_loader:
            qa = self._av_loader.compute_quality_score(fund_a)
            qb = self._av_loader.compute_quality_score(fund_b)
            # Similarity = 1 - normalized difference in quality scores
            quality_sim = max(0.0, 1.0 - abs(qa - qb))
        else:
            quality_sim = 0.5  # neutral when Alpha Vantage not configured

        # L08: Valuation bucket overlap
        # Both schemes in same P/E bucket (Value/Blend/Growth) = style overlap
        if fund_a and fund_b and self._av_loader:
            val_overlap = self._av_loader.compute_valuation_overlap(fund_a, fund_b)
        else:
            val_overlap = 0.5  # neutral

        raw: dict[str, tuple[float, str]] = {
            "C01_real_return_correlation":   (real_sim,  "real_market_data"),
            "C02_sector_return_correlation": (sector_corr, "holdings_data"),
            "L01_direct_stock_overlap":      (self._jaccard(stocks_a, stocks_b), "holdings_data"),
            "L02_top10_overlap":             (self._jaccard(top10_a, top10_b), "holdings_data"),
            "L03_sector_cosine":             (self._cosine(self._sector_vec(sid_a), self._sector_vec(sid_b)), "holdings_data"),
            "L04_weighted_stock_overlap":    (self._weighted_overlap(self._weight_map(sid_a), self._weight_map(sid_b)), "holdings_data"),
            "L05_category_benchmark":        (meta_score, "metadata"),
            "L06_risk_profile_proximity":    (risk_prox, "metadata"),
            "L07_fundamental_quality":       (quality_sim, "alpha_vantage"),
            "L08_valuation_bucket":          (val_overlap, "alpha_vantage"),
        }

        descriptions = {
            "C01_real_return_correlation":   "Actual portfolio return correlation (yfinance prices × holding weights)",
            "C02_sector_return_correlation": "Sector allocation cosine similarity (sector return proxy)",
            "L01_direct_stock_overlap":      "Jaccard overlap on full stock name sets",
            "L02_top10_overlap":             "Jaccard overlap on top-10 conviction holdings",
            "L03_sector_cosine":             "Cosine similarity on sector weight vectors",
            "L04_weighted_stock_overlap":    "Sum of min(weight_A, weight_B) for shared stocks",
            "L05_category_benchmark":        "Category / benchmark / fund manager similarity",
            "L06_risk_profile_proximity":    "SEBI riskometer score proximity",
            "L07_fundamental_quality":       "Quality tier overlap: ROE, D/E, revenue growth (Alpha Vantage)",
            "L08_valuation_bucket":          "Valuation style overlap: Value/Blend/Growth bucket (Alpha Vantage P/E)",
        }

        layers = [
            LayerScore(
                layer_id=lid,
                description=descriptions[lid],
                raw_score=round(score, 4),
                weight=LAYER_WEIGHTS[lid],
                contribution=round(score * LAYER_WEIGHTS[lid], 4),
                data_source=source,
            )
            for lid, (score, source) in raw.items()
        ]

        return layers, real_pearson, dq

    @staticmethod
    def _manager_sim(mgr_a: str, mgr_b: str) -> float:
        if not mgr_a or not mgr_b: return 0.0
        na = mgr_a.split("(")[0].strip().lower()
        nb = mgr_b.split("(")[0].strip().lower()
        if na == nb and na: return 1.0
        if set(na.split()) & set(nb.split()): return 0.5
        return 0.0

    # ── Top-5 recommendation ────────────────────────────────────────────────────
    def _recommend_top5(self, all_schemes: list[str], pair_map: dict) -> list[dict]:
        """
        Find the 5 schemes that together minimize total pairwise PLOS score.
        Uses a greedy selection: pick the scheme with lowest avg overlap to already-selected.

        Args:
            all_schemes: all eligible scheme IDs
            pair_map: {(a,b): plos_score} for all pairs

        Returns list of {scheme_id, name, avg_overlap_score, rank}
        """
        if len(all_schemes) <= 5:
            return [
                {
                    "rank": i + 1,
                    "scheme_id": sid,
                    "scheme_name": self._schemes.get(sid, {}).get("name", sid),
                    "avg_plos_with_others": 0.0,
                }
                for i, sid in enumerate(all_schemes)
            ]

        # Greedy selection
        def avg_overlap_with_selected(candidate: str, selected: list[str]) -> float:
            if not selected: return 0.0
            scores = []
            for s in selected:
                key = (min(candidate, s), max(candidate, s))
                scores.append(pair_map.get(key, 0.0))
            return sum(scores) / len(scores)

        # Start with the scheme that has lowest average overlap with all others
        avg_all = {
            sid: sum(pair_map.get((min(sid, other), max(sid, other)), 0.0)
                     for other in all_schemes if other != sid) / max(len(all_schemes) - 1, 1)
            for sid in all_schemes
        }
        selected = [min(avg_all, key=avg_all.get)]

        while len(selected) < 5:
            remaining = [s for s in all_schemes if s not in selected]
            next_pick = min(
                remaining,
                key=lambda c: avg_overlap_with_selected(c, selected)
            )
            selected.append(next_pick)

        return [
            {
                "rank": i + 1,
                "scheme_id": sid,
                "scheme_name": self._schemes.get(sid, {}).get("name", sid),
                "avg_plos_with_others": round(avg_overlap_with_selected(sid, [s for s in selected if s != sid]), 2),
            }
            for i, sid in enumerate(selected)
        ]

    # ── Public API ───────────────────────────────────────────────────────────────
    def compute_plos(self, scheme_ids: list[str]) -> PLOSResult:
        """
        Compute PLOS scores using real market data as primary signal.

        For equity schemes with holdings data: uses actual yfinance prices.
        For others: falls back to holdings-based metrics.
        """
        if len(scheme_ids) < 2:
            return PLOSResult(
                scheme_ids=scheme_ids,
                pair_scores=[],
                portfolio_plos=0.0,
                diversification_score=100.0,
                interpretation="Select at least 2 schemes for overlap analysis.",
                top_5_recommendation=[],
                most_overlapping_pair=None,
                least_overlapping_pair=None,
                data_source="n/a",
            )

        pair_scores: list[PairPLOS] = []
        pair_map: dict[tuple[str, str], float] = {}
        data_sources: list[str] = []
        n = len(scheme_ids)

        for i in range(n):
            for j in range(i + 1, n):
                sid_a, sid_b = scheme_ids[i], scheme_ids[j]
                layers, real_corr, dq = self._compute_layers(sid_a, sid_b)
                plos = round(sum(ls.contribution for ls in layers) * 100, 2)
                data_sources.append(dq)

                pair_scores.append(PairPLOS(
                    scheme_a_id=sid_a,
                    scheme_b_id=sid_b,
                    scheme_a_name=self._schemes.get(sid_a, {}).get("name", sid_a),
                    scheme_b_name=self._schemes.get(sid_b, {}).get("name", sid_b),
                    plos_score=plos,
                    real_correlation=round(real_corr, 4) if real_corr is not None else None,
                    layers=layers,
                    interpretation=_interpret(plos),
                    data_quality=dq,
                ))
                pair_map[(min(sid_a, sid_b), max(sid_a, sid_b))] = plos

        pair_scores.sort(key=lambda p: p.plos_score, reverse=True)

        portfolio_plos = round(
            sum(p.plos_score for p in pair_scores) / len(pair_scores), 2
        )

        top5 = self._recommend_top5(scheme_ids, pair_map)

        most_pair = (pair_scores[0].scheme_a_id, pair_scores[0].scheme_b_id) if pair_scores else None
        least_pair = (pair_scores[-1].scheme_a_id, pair_scores[-1].scheme_b_id) if len(pair_scores) > 1 else None

        dominant_source = (
            "real_nse_prices" if data_sources.count("real") > len(data_sources) // 2
            else "synthetic_calibrated" if "synthetic" in data_sources
            else "holdings_data"
        )

        return PLOSResult(
            scheme_ids=scheme_ids,
            pair_scores=pair_scores,
            portfolio_plos=portfolio_plos,
            diversification_score=round(100.0 - portfolio_plos, 2),
            interpretation=_interpret(portfolio_plos),
            top_5_recommendation=top5,
            most_overlapping_pair=most_pair,
            least_overlapping_pair=least_pair,
            data_source=dominant_source,
        )
