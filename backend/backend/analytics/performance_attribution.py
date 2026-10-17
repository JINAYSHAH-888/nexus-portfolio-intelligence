"""
Performance Attribution Engine — Brinson-Hood-Beebower (BHB) model.

Decomposes active return into three components:
  1. Allocation Effect   — did we overweight/underweight the right sectors?
  2. Selection Effect    — did we pick better stocks within each sector?
  3. Interaction Effect  — did overweighting coincide with good selection?

Total Active Return = Allocation + Selection + Interaction

Inputs (all from DuckDB / TimescaleDB):
  - Portfolio weights by sector (w_p)
  - Benchmark weights by sector (w_b)  — Nifty 50 sector composition
  - Portfolio return by sector (r_p)
  - Benchmark return by sector (r_b)

Output is consumed by the reporting service (Phase 4 PDF).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class SectorAttribution:
    sector: str
    portfolio_weight: float     # w_p
    benchmark_weight: float     # w_b
    portfolio_return: float     # r_p
    benchmark_return: float     # r_b
    allocation_effect: float    # (w_p - w_b) × (r_b - R_b)
    selection_effect: float     # w_p × (r_p - r_b)
    interaction_effect: float   # (w_p - w_b) × (r_p - r_b)
    total_effect: float


@dataclass
class AttributionResult:
    portfolio_return: float
    benchmark_return: float
    active_return: float

    total_allocation: float
    total_selection: float
    total_interaction: float

    sector_attribution: list[SectorAttribution]
    data_source: str


def compute_bhb_attribution(
    portfolio_sector_weights: dict[str, float],
    portfolio_sector_returns: dict[str, float],
    benchmark_sector_weights: dict[str, float],
    benchmark_sector_returns: dict[str, float],
) -> AttributionResult:
    """
    Compute Brinson-Hood-Beebower performance attribution.

    All weights must sum to ~1.0 (or ~100 if percentage scale — normalized internally).
    All returns in decimal form (e.g. 0.12 = 12%).

    Args:
        portfolio_sector_weights:    {sector: weight_fraction}
        portfolio_sector_returns:    {sector: return_decimal}
        benchmark_sector_weights:    {sector: weight_fraction}
        benchmark_sector_returns:    {sector: return_decimal}

    Returns:
        AttributionResult with per-sector breakdown and totals.
    """
    all_sectors = sorted(
        set(portfolio_sector_weights) | set(benchmark_sector_weights)
    )

    # Normalize weights
    p_total = sum(portfolio_sector_weights.values()) or 1.0
    b_total = sum(benchmark_sector_weights.values()) or 1.0

    # Benchmark total return (weighted average)
    R_b = sum(
        (benchmark_sector_weights.get(s, 0) / b_total) * benchmark_sector_returns.get(s, 0)
        for s in all_sectors
    )

    sector_attrs: list[SectorAttribution] = []
    total_alloc = total_sel = total_inter = 0.0

    for sector in all_sectors:
        w_p = portfolio_sector_weights.get(sector, 0.0) / p_total
        w_b = benchmark_sector_weights.get(sector, 0.0) / b_total
        r_p = portfolio_sector_returns.get(sector, 0.0)
        r_b = benchmark_sector_returns.get(sector, 0.0)

        # BHB decomposition
        allocation = (w_p - w_b) * (r_b - R_b)
        selection = w_b * (r_p - r_b)    # note: uses benchmark weight per BHB
        interaction = (w_p - w_b) * (r_p - r_b)
        total = allocation + selection + interaction

        total_alloc += allocation
        total_sel += selection
        total_inter += interaction

        sector_attrs.append(SectorAttribution(
            sector=sector,
            portfolio_weight=round(w_p, 4),
            benchmark_weight=round(w_b, 4),
            portfolio_return=round(r_p, 4),
            benchmark_return=round(r_b, 4),
            allocation_effect=round(allocation, 6),
            selection_effect=round(selection, 6),
            interaction_effect=round(interaction, 6),
            total_effect=round(total, 6),
        ))

    # Portfolio total return
    R_p = sum(
        (portfolio_sector_weights.get(s, 0) / p_total) * portfolio_sector_returns.get(s, 0)
        for s in all_sectors
    )

    return AttributionResult(
        portfolio_return=round(R_p, 4),
        benchmark_return=round(R_b, 4),
        active_return=round(R_p - R_b, 4),
        total_allocation=round(total_alloc, 6),
        total_selection=round(total_sel, 6),
        total_interaction=round(total_inter, 6),
        sector_attribution=sorted(sector_attrs, key=lambda x: abs(x.total_effect), reverse=True),
        data_source="holdings_and_timescaledb",
    )


def build_benchmark_weights_nifty50() -> dict[str, float]:
    """
    Approximate Nifty 50 sector weights (June 2026).
    Used as benchmark for attribution when no custom benchmark is specified.
    Updated quarterly from NSE official data.
    """
    return {
        "Financial Services": 0.369,
        "Information Technology": 0.132,
        "Oil & Gas": 0.092,
        "Consumer Goods": 0.090,
        "Automobile": 0.068,
        "Healthcare": 0.053,
        "Metals": 0.044,
        "Construction": 0.038,
        "Power": 0.033,
        "Telecom": 0.027,
        "Others": 0.054,
    }


def estimate_sector_returns_from_prices(
    sector_holdings: dict[str, list[str]],
    returns_df: pd.DataFrame,
) -> dict[str, float]:
    """
    Estimate sector-level returns from stock-level returns.

    Args:
        sector_holdings: {sector: [ticker, ...]}
        returns_df: Wide DataFrame of stock daily returns

    Returns:
        {sector: annualized_return}
    """
    TRADING_DAYS = 252
    sector_returns: dict[str, float] = {}

    for sector, tickers in sector_holdings.items():
        available = [t for t in tickers if t in returns_df.columns]
        if not available:
            sector_returns[sector] = 0.0
            continue
        # Equal-weight sector return
        mean_daily = returns_df[available].mean(axis=1).mean()
        ann_return = float((1 + mean_daily) ** TRADING_DAYS - 1)
        sector_returns[sector] = round(ann_return, 4)

    return sector_returns
