"""
Overlap Analysis Engine — computes PLOS, HHI, and EEI via DuckDB.

PLOS (Portfolio Level Overlap Score)
  Measures how much two schemes overlap at the portfolio level.
  Uses both holdings-based Jaccard AND real price correlation.

HHI (Herfindahl-Hirschman Index)
  Measures concentration within a single portfolio.
  HHI = Σ weight_i²  (in basis points, 0–10000)
  HHI < 1500: competitive (diversified)
  HHI 1500–2500: moderately concentrated
  HHI > 2500: highly concentrated

EEI (Effective Exposure Index)
  Measures the effective number of independent positions.
  EEI = 1 / Σ weight_i²  (in decimal, not basis points)
  EEI = N: perfectly equal weights (maximum diversification)
  EEI = 1: single-position portfolio (zero diversification)

All three metrics are computed in DuckDB for large holdings universes
and fall back to pure Python for small in-memory datasets.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_SCHEMES_PATH = Path(__file__).resolve().parent.parent / "data" / "schemes_metadata.json"


@dataclass
class ConcentrationMetrics:
    """HHI and EEI for a single scheme's holdings."""
    scheme_id: str
    hhi: float              # Herfindahl-Hirschman Index (0–10000)
    eei: float              # Effective Exposure Index (1–N)
    n_holdings: int
    top_holding_weight: float
    top_5_weight: float
    concentration_label: str  # "Diversified" | "Moderate" | "Concentrated"


@dataclass
class OverlapMatrix:
    """Pairwise overlap matrix for a set of schemes."""
    scheme_ids: list[str]
    jaccard_matrix: list[list[float]]   # N×N Jaccard scores
    correlation_matrix: list[list[float]]  # N×N price correlations
    plos_matrix: list[list[float]]         # N×N PLOS scores (composite)
    avg_plos: float
    most_overlapping_pair: tuple[str, str] | None
    data_source: str  # "timescaledb" | "holdings_only" | "synthetic"


def compute_hhi(weights: list[float]) -> float:
    """
    Compute HHI in basis points from a list of portfolio weights (0-100 scale).

    Args:
        weights: list of weights (percentage, e.g. [5.2, 3.1, 2.8, ...])

    Returns:
        HHI in basis points (0–10000)
    """
    # Normalize to fractions if given as percentages
    total = sum(weights)
    if total <= 0:
        return 0.0
    fractions = [w / total for w in weights]
    return sum(f ** 2 for f in fractions) * 10000


def compute_eei(weights: list[float]) -> float:
    """
    Compute EEI (Effective Exposure Index = effective number of positions).

    EEI = 1 / HHI_decimal = 1 / Σ(w_i²)

    Args:
        weights: list of weights (any scale, normalized internally)

    Returns:
        EEI: float ≥ 1.0
    """
    total = sum(weights)
    if total <= 0:
        return 1.0
    fractions = [w / total for w in weights]
    sum_sq = sum(f ** 2 for f in fractions)
    return 1.0 / sum_sq if sum_sq > 0 else len(weights)


def concentration_label(hhi: float) -> str:
    if hhi < 1500:
        return "Diversified"
    if hhi < 2500:
        return "Moderate"
    return "Concentrated"


def compute_scheme_concentration(scheme_id: str, holdings: list[dict]) -> ConcentrationMetrics:
    """
    Compute HHI and EEI for a single scheme's holdings.

    Args:
        scheme_id: scheme identifier
        holdings: list of {name, weight} dicts

    Returns:
        ConcentrationMetrics dataclass
    """
    valid = [
        h for h in holdings
        if h.get("weight", 0) > 0
        and str(h.get("name", "")).lower() not in (
            "equity less than 1% of corpus",
            "cash and other receivables",
            "grand total",
        )
    ]

    if not valid:
        return ConcentrationMetrics(
            scheme_id=scheme_id, hhi=0.0, eei=0.0, n_holdings=0,
            top_holding_weight=0.0, top_5_weight=0.0,
            concentration_label="Unknown",
        )

    weights = [h["weight"] for h in valid]
    weights_sorted = sorted(weights, reverse=True)
    hhi = compute_hhi(weights)
    eei = compute_eei(weights)

    return ConcentrationMetrics(
        scheme_id=scheme_id,
        hhi=round(hhi, 2),
        eei=round(eei, 2),
        n_holdings=len(valid),
        top_holding_weight=round(weights_sorted[0], 2) if weights_sorted else 0.0,
        top_5_weight=round(sum(weights_sorted[:5]), 2),
        concentration_label=concentration_label(hhi),
    )


def compute_duckdb_hhi_batch(scheme_holdings_map: dict[str, list[dict]]) -> dict[str, ConcentrationMetrics]:
    """
    Compute HHI/EEI for multiple schemes.

    Args:
        scheme_holdings_map: {scheme_id: [{name, weight}, ...]}

    Returns:
        {scheme_id: ConcentrationMetrics}
    """
    # For the holdings-level computation we use DuckDB in-memory
    import duckdb

    results: dict[str, ConcentrationMetrics] = {}

    # Build flat records for DuckDB
    records = []
    for sid, holdings in scheme_holdings_map.items():
        for h in holdings:
            w = h.get("weight", 0)
            if w > 0:
                records.append({"scheme_id": sid, "weight": w})

    if not records:
        return results

    conn = duckdb.connect(":memory:")
    holdings_df = pd.DataFrame(records)
    conn.register("holdings", holdings_df)

    sql = """
        WITH totals AS (
            SELECT scheme_id, SUM(weight) AS total_weight
            FROM holdings
            GROUP BY scheme_id
        ),
        fractions AS (
            SELECT h.scheme_id, h.weight / t.total_weight AS frac
            FROM holdings h
            JOIN totals t ON h.scheme_id = t.scheme_id
        )
        SELECT
            scheme_id,
            SUM(frac * frac) * 10000   AS hhi,
            1.0 / SUM(frac * frac)     AS eei,
            COUNT(*)                   AS n_holdings,
            MAX(frac * 100)            AS top_weight
        FROM fractions
        GROUP BY scheme_id
    """

    try:
        result_df = conn.execute(sql).df()
        for _, row in result_df.iterrows():
            sid = row["scheme_id"]
            hhi = float(row["hhi"])
            results[sid] = ConcentrationMetrics(
                scheme_id=sid,
                hhi=round(hhi, 2),
                eei=round(float(row["eei"]), 2),
                n_holdings=int(row["n_holdings"]),
                top_holding_weight=round(float(row["top_weight"]), 2),
                top_5_weight=0.0,  # would need a separate TOP-5 query
                concentration_label=concentration_label(hhi),
            )
    except Exception as e:
        logger.warning(f"DuckDB HHI batch failed ({e}), computing individually")
        for sid, holdings in scheme_holdings_map.items():
            results[sid] = compute_scheme_concentration(sid, holdings)
    finally:
        conn.close()

    return results


def compute_pairwise_plos_matrix(
    scheme_ids: list[str],
    holdings_map: dict[str, list[dict]],
    returns_df: pd.DataFrame | None = None,
) -> OverlapMatrix:
    """
    Compute the full PLOS matrix for a set of schemes.

    Combines:
      - Jaccard overlap (holdings-based) — fast, always available
      - Price correlation (TimescaleDB via DuckDB) — when price data exists

    PLOS = 0.5 × jaccard_similarity + 0.5 × (correlation + 1) / 2

    Args:
        scheme_ids: ordered list of scheme IDs
        holdings_map: {scheme_id: [{name, weight}, ...]}
        returns_df: Wide returns DataFrame (dates × tickers) from DuckDB query
                    If None, only Jaccard is used.

    Returns:
        OverlapMatrix with all pairwise scores
    """
    n = len(scheme_ids)
    jaccard_matrix = [[0.0] * n for _ in range(n)]
    corr_matrix = [[0.0] * n for _ in range(n)]
    plos_matrix = [[0.0] * n for _ in range(n)]

    # Build stock name sets
    def stock_set(sid: str) -> set[str]:
        return {
            h["name"].strip().rstrip("*").lower()
            for h in holdings_map.get(sid, [])
            if h.get("weight", 0) > 0
        }

    def jaccard(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    stock_sets = {sid: stock_set(sid) for sid in scheme_ids}
    data_source = "holdings_only"

    # Build scheme-level returns from holdings-weighted stock returns
    scheme_returns: dict[str, pd.Series] = {}
    if returns_df is not None and not returns_df.empty:
        data_source = "timescaledb"
        for sid in scheme_ids:
            holdings = holdings_map.get(sid, [])
            # Build weight map: ticker → weight fraction
            w_map: dict[str, float] = {}
            from backend.data_loaders.market_data import MarketDataLoader
            loader = MarketDataLoader()
            for h in holdings[:30]:
                ticker = loader.stock_name_to_ticker(h["name"].rstrip("*").strip())
                if ticker and ticker in returns_df.columns and ticker != "NOT_LISTED_YET":
                    w_map[ticker] = h.get("weight", 0.0)

            if w_map:
                total_w = sum(w_map.values())
                norm_w = {t: w / total_w for t, w in w_map.items()}
                available = {t: w for t, w in norm_w.items() if t in returns_df.columns}
                if available:
                    w_series = pd.Series(available)
                    w_series = w_series / w_series.sum()
                    scheme_returns[sid] = returns_df[list(available.keys())].dot(w_series)

    for i in range(n):
        for j in range(n):
            sid_a, sid_b = scheme_ids[i], scheme_ids[j]

            # Diagonal
            if i == j:
                jaccard_matrix[i][j] = 1.0
                corr_matrix[i][j] = 1.0
                plos_matrix[i][j] = 100.0
                continue

            # Jaccard
            j_score = jaccard(stock_sets[sid_a], stock_sets[sid_b])
            jaccard_matrix[i][j] = round(j_score, 4)

            # Price correlation
            if sid_a in scheme_returns and sid_b in scheme_returns:
                aligned = pd.DataFrame({
                    "a": scheme_returns[sid_a],
                    "b": scheme_returns[sid_b],
                }).dropna()
                if len(aligned) >= 60:
                    corr = aligned["a"].corr(aligned["b"])
                    corr = float(corr) if not (corr != corr) else 0.0
                else:
                    corr = 0.0
            else:
                corr = j_score  # fallback: use Jaccard as correlation proxy

            corr_matrix[i][j] = round(corr, 4)

            # PLOS (0–100): blend Jaccard + normalized correlation
            corr_sim = (corr + 1) / 2  # map [-1,1] → [0,1]
            plos = (0.5 * j_score + 0.5 * corr_sim) * 100
            plos_matrix[i][j] = round(plos, 2)

    # Find most overlapping pair (off-diagonal)
    most_overlapping = None
    max_plos = -1.0
    for i in range(n):
        for j in range(i + 1, n):
            if plos_matrix[i][j] > max_plos:
                max_plos = plos_matrix[i][j]
                most_overlapping = (scheme_ids[i], scheme_ids[j])

    all_off_diag = [
        plos_matrix[i][j]
        for i in range(n) for j in range(n)
        if i != j
    ]
    avg_plos = sum(all_off_diag) / len(all_off_diag) if all_off_diag else 0.0

    return OverlapMatrix(
        scheme_ids=scheme_ids,
        jaccard_matrix=jaccard_matrix,
        correlation_matrix=corr_matrix,
        plos_matrix=plos_matrix,
        avg_plos=round(avg_plos, 2),
        most_overlapping_pair=most_overlapping,
        data_source=data_source,
    )
