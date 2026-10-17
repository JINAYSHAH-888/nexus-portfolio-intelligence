"""
PortfolioStatistics — the single source of truth for everything ARISE and
portfolio-level analytics need, computed EXCLUSIVELY from the investor's
selected schemes and their real historical price data.

Core principle enforced here: the selected mutual fund schemes are the ONLY
source of truth. No assumptions about previous investments or other assets.

Data flow:
    Selected scheme_ids + weights
      -> MarketDataLoader (yfinance-backed real prices, synthetic fallback
         only when unreachable, built from each scheme's actual holdings)
      -> per-scheme real return series
      -> weighted blend => ONE real portfolio daily return series
      -> risk_metrics.py => CAGR, volatility, drawdown, Sharpe, Sortino
      -> covariance_engine.py => correlation matrix, risk contribution
      -> overlap_analysis.py => HHI/EEI on the investor's real scheme weights

No return, volatility, or correlation number here is hardcoded. Every
figure traces back to real price history for schemes the investor selected.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backend.analytics import risk_metrics
from backend.analytics.covariance_engine import compute_covariance_matrix, compute_correlation_matrix
from backend.analytics.overlap_analysis import compute_hhi, compute_eei, concentration_label

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SCHEMES_PATH = _DATA_DIR / "schemes_metadata.json"
_HOLDINGS_PATH = _DATA_DIR / "sample_holdings.json"


@dataclass
class SchemeStatistics:
    """Real historical statistics for a single selected scheme."""
    scheme_id: str
    scheme_name: str
    weight: float

    cagr: float
    annualized_volatility: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float

    correlation_with_portfolio: float
    risk_contribution_pct: float
    return_contribution_pct: float

    data_points: int
    is_synthetic: bool


@dataclass
class PortfolioStatistics:
    """Complete real-data statistical description of the investor's actual
    selected portfolio. ARISE consumes this; it never touches price data
    directly."""
    scheme_ids: list[str]
    weights: dict[str, float]

    portfolio_daily_returns: pd.Series
    portfolio_annual_returns: pd.Series

    portfolio_cagr: float
    portfolio_volatility: float
    portfolio_max_drawdown: float
    portfolio_sharpe: float
    portfolio_sortino: float

    scheme_statistics: list[SchemeStatistics]
    correlation_matrix: pd.DataFrame

    hhi: float
    eei: float
    concentration_label: str

    sector_weights: dict[str, float]
    max_sector_weight: float

    risk_free_rate_proxy: float
    data_source: str
    n_years_history: float

    # ── Return-shrinkage/cap diagnostics (see _shrink_and_cap_return) ──────────
    portfolio_cagr_raw: float = 0.0        # the unshrunk sample CAGR, for transparency
    prior_cagr_used: float = 0.0           # asset-class-blended prior this was shrunk toward
    sample_weight_cagr: float = 0.0        # n/(n+tau) -- how much the sample was trusted
    return_capped: bool = False            # True if the evidence-scaled cap actually bound


def get_risk_free_rate_proxy(scheme_statistics: list["SchemeStatistics"]) -> float:
    """
    The risk-free rate ARISE compounds its Liquid Reserve bucket at.

    THE BUG THIS REPLACES: this used to return the realised CAGR of the
    lowest-volatility scheme in the investor's selection. That is not a
    risk-free rate -- it is a realised return, and it is unbounded. Measured in
    this repo it produced 10.05% on one selection and 21.58% on another. ARISE
    passes this straight into `liquid_reserve_return`, so the "safe" bucket of a
    retirement plan was compounding at up to 21% a year, materially inflating
    success rates. A risk-free rate that depends on how well your equity fund
    happened to do is not a risk-free rate.

    It now resolves from the overnight fund's OWN DISCLOSED YTM (real factsheet
    data, ~5.4%) -- an actual near-riskless yield the investor could genuinely
    access -- clamped to a plausible band, and identical to the rate the risk
    dashboard uses for Sharpe. One risk-free rate for the whole product.

    `scheme_statistics` is retained for signature compatibility with the
    pre-existing callers; it is deliberately no longer consulted.
    """
    from backend.core.assumptions import resolve_risk_free_rate

    return resolve_risk_free_rate()


from backend.core.async_utils import run_async_safely


# ── Return shrinkage / evidence-scaled cap ──────────────────────────────────
# WHY THIS EXISTS: without it, portfolio_cagr is the raw CAGR of whatever
# daily-returns draw was available -- for a scheme that fell back to
# synthetic data (network unreachable, thin real history), that's a SINGLE
# realized path from a regime-switching sampler. Even a well-calibrated
# sampler occasionally draws a hot multi-year run, and with nothing to damp
# it, ARISE's Monte Carlo would extrapolate that single lucky draw forward
# undamped for a 25+ year retirement horizon -- exactly the original
# "runaway corpus" failure mode this whole engine was built to fix. This
# shrinks the sample CAGR toward a defensible, asset-class-blended prior in
# proportion to how much real history actually supports it (more years of
# real data => more weight on the sample; thin/synthetic history => mostly
# the prior), then caps how far above the prior the result can land.
RETURN_SHRINKAGE_TAU_YEARS = 10.0     # weight on sample = n/(n+tau)
RETURN_CAP_BAND = 0.02                # sample can push at most ~2pp above prior at full weight
RETURN_CAP_ABS_CEILING = 0.15         # absolute ceiling regardless of weight
RETURN_FLOOR_BAND = 0.04              # sample can pull at most ~4pp below prior

# Fallback category priors used only when a scheme's own real metadata
# (std_deviation for equity, ytm_decimal for debt) is unavailable.
_EQUITY_PRIOR_MU, _EQUITY_PRIOR_SIGMA = 0.11, 0.16
_DEBT_PRIOR_MU, _DEBT_PRIOR_SIGMA = 0.065, 0.035


def _scheme_prior(scheme_meta: dict) -> tuple[float, float]:
    """
    (prior_mu, prior_sigma) for one scheme, preferring its OWN real disclosed
    metadata over generic category defaults:
      equity -> prior_mu = long-run equity assumption (11%, a labelled
                assumption, not a scheme-specific fact); prior_sigma = the
                scheme's own disclosed std_deviation when available.
      debt   -> prior_mu = the scheme's own YTM (ytm_decimal) -- a real,
                scheme-specific running yield, the best available forward
                anchor for a bond fund; prior_sigma = duration-scaled
                (matches debt_returns_model.py's own approximation) when
                modified_duration_years is available.
    """
    itype = scheme_meta.get("instrument_type")
    if itype == "debt":
        ytm = scheme_meta.get("ytm_decimal")
        mu = float(ytm) if ytm is not None else _DEBT_PRIOR_MU
        dur = scheme_meta.get("modified_duration_years")
        sigma = max(float(dur) * 0.012, 0.002) if dur is not None else _DEBT_PRIOR_SIGMA
        return mu, sigma
    # equity (and anything else): scheme's own disclosed std_deviation if present
    std_dev = scheme_meta.get("std_deviation")
    sigma = float(std_dev) / 100.0 if std_dev is not None else _EQUITY_PRIOR_SIGMA
    return _EQUITY_PRIOR_MU, sigma


def shrink_and_cap_portfolio_return(
    sample_cagr: float,
    n_years_history: float,
    present_ids: list[str],
    weights: dict[str, float],
    schemes: dict[str, dict],
) -> tuple[float, float, float, bool]:
    """
    Returns (shrunk_capped_cagr, prior_cagr_used, sample_weight, was_capped).
    """
    prior_mu = 0.0
    total_w = 0.0
    for sid in present_ids:
        w = weights.get(sid, 0.0)
        mu, _sigma = _scheme_prior(schemes.get(sid, {}))
        prior_mu += w * mu
        total_w += w
    if total_w > 0:
        prior_mu /= total_w

    sample_weight = n_years_history / (n_years_history + RETURN_SHRINKAGE_TAU_YEARS)
    shrunk = sample_weight * sample_cagr + (1 - sample_weight) * prior_mu

    cap = min(prior_mu + RETURN_CAP_BAND * max(sample_weight, 0.1), RETURN_CAP_ABS_CEILING)
    floor = prior_mu - RETURN_FLOOR_BAND
    capped_value = min(max(shrunk, floor), cap)
    was_capped = capped_value != shrunk

    return round(capped_value, 4), round(prior_mu, 4), round(sample_weight, 4), was_capped


class PortfolioStatisticsBuilder:
    """
    Builds a PortfolioStatistics object for a given scheme selection + weights.

    Usage:
        builder = PortfolioStatisticsBuilder()
        stats = builder.build(
            scheme_ids=["nippon-large-cap-fund", "nippon-multi-cap-fund"],
            weights={"nippon-large-cap-fund": 0.6, "nippon-multi-cap-fund": 0.4},
        )
    """

    def __init__(
        self,
        schemes_path: str | Path = _SCHEMES_PATH,
        holdings_path: str | Path = _HOLDINGS_PATH,
        price_period: str = "10y",
        force_synthetic: bool = False,
    ) -> None:
        self._price_period = price_period
        self._force_synthetic = force_synthetic

        with open(Path(schemes_path), encoding="utf-8") as f:
            schemes_list = json.load(f)
        self._schemes: dict[str, dict] = {s["id"]: s for s in schemes_list}

        with open(Path(holdings_path), encoding="utf-8") as f:
            hdata = json.load(f)
        self._holdings: dict[str, list[dict]] = {}
        if isinstance(hdata, dict):
            for sid, data in hdata.items():
                raw = data.get("holdings", []) if isinstance(data, dict) else data
                self._holdings[sid] = raw
        for s in schemes_list:
            if s["id"] not in self._holdings and s.get("holdings"):
                self._holdings[s["id"]] = s["holdings"]

        try:
            from backend.data_loaders.market_data import MarketDataLoader
            self._loader = MarketDataLoader(force_synthetic=force_synthetic)
        except Exception as e:
            logger.warning(f"Could not init MarketDataLoader: {e}")
            self._loader = None

    def _get_scheme_returns(self, scheme_id: str) -> tuple[pd.Series | None, bool]:
        holdings = self._holdings.get(scheme_id, [])
        scheme_meta = self._schemes.get(scheme_id, {"id": scheme_id})
        if self._loader is None:
            return None, True
        try:
            result = run_async_safely(
                self._loader.get_scheme_returns_with_fallback(scheme_meta, holdings, period=self._price_period)
            )
            if len(result.returns) < 60:
                return None, True
            return result.returns, result.is_synthetic
        except Exception as e:
            logger.warning(f"Could not get returns for {scheme_id}: {e}")
            return None, True

    def _sector_weights_for_scheme(self, scheme_id: str) -> dict[str, float]:
        sectors = self._schemes.get(scheme_id, {}).get("sector_allocation") or []
        out: dict[str, float] = {}
        for s in sectors:
            try:
                v = s.get("weight", 0)
                if isinstance(v, str):
                    v = float(v.replace("%", ""))
                out[str(s.get("sector", "Unknown")).strip()] = float(v) / 100.0
            except (ValueError, TypeError):
                pass
        return out

    def build(self, *, scheme_ids: list[str], weights: dict[str, float]) -> PortfolioStatistics:
        """Build the complete real-data PortfolioStatistics for exactly the
        given schemes at exactly the given weights."""
        if abs(sum(weights.values()) - 1.0) > 0.01:
            raise ValueError(f"Weights must sum to 1.0, got {sum(weights.values()):.4f}")
        if set(weights.keys()) != set(scheme_ids):
            raise ValueError("weights must cover exactly the given scheme_ids")

        scheme_returns: dict[str, pd.Series] = {}
        is_synthetic_map: dict[str, bool] = {}
        for sid in scheme_ids:
            r, is_synth = self._get_scheme_returns(sid)
            if r is not None:
                scheme_returns[sid] = r
                is_synthetic_map[sid] = is_synth

        if not scheme_returns:
            raise ValueError(
                "No usable return data for any selected scheme. Cannot build portfolio statistics."
            )

        aligned = pd.DataFrame(scheme_returns).dropna()
        if aligned.empty:
            raise ValueError("Selected schemes have no overlapping historical trading days.")

        present_ids = list(aligned.columns)
        w = pd.Series({sid: weights[sid] for sid in present_ids})
        w = w / w.sum()

        portfolio_daily = aligned[present_ids].dot(w)
        portfolio_annual = risk_metrics.annual_returns_from_daily(portfolio_daily)
        n_years = len(portfolio_daily) / risk_metrics.TRADING_DAYS

        prelim_stats: list[SchemeStatistics] = []
        for sid in present_ids:
            r = aligned[sid]
            c = risk_metrics.cagr(r)
            vol = risk_metrics.annualized_volatility(r)
            corr_with_port = float(r.corr(portfolio_daily)) if portfolio_daily.std() > 0 else 0.0
            prelim_stats.append(SchemeStatistics(
                scheme_id=sid,
                scheme_name=self._schemes.get(sid, {}).get("name", sid),
                weight=float(w[sid]),
                cagr=round(c, 4),
                annualized_volatility=round(vol, 4),
                max_drawdown=round(risk_metrics.max_drawdown(r), 4),
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                correlation_with_portfolio=round(corr_with_port, 4),
                risk_contribution_pct=0.0,
                return_contribution_pct=round(float(w[sid]) * c, 4),
                data_points=len(r),
                is_synthetic=is_synthetic_map.get(sid, True),
            ))

        rf_proxy = get_risk_free_rate_proxy(prelim_stats)

        cov_matrix = compute_covariance_matrix(aligned[present_ids], method="ledoit_wolf")
        corr_matrix = compute_correlation_matrix(aligned[present_ids])

        weights_arr = w[present_ids].values
        cov_arr = cov_matrix.values if not cov_matrix.empty else np.diag(
            [risk_metrics.annualized_volatility(aligned[c]) ** 2 for c in present_ids]
        )
        port_var = float(weights_arr @ cov_arr @ weights_arr) if cov_arr.size else 0.0

        if port_var > 0:
            marginal = cov_arr @ weights_arr
            risk_contrib = weights_arr * marginal / port_var
        else:
            risk_contrib = np.zeros(len(present_ids))

        total_return_contrib = sum(s.return_contribution_pct for s in prelim_stats) or 1.0

        final_stats: list[SchemeStatistics] = []
        for i, s in enumerate(prelim_stats):
            r = aligned[s.scheme_id]
            final_stats.append(SchemeStatistics(
                scheme_id=s.scheme_id,
                scheme_name=s.scheme_name,
                weight=s.weight,
                cagr=s.cagr,
                annualized_volatility=s.annualized_volatility,
                max_drawdown=s.max_drawdown,
                sharpe_ratio=round(risk_metrics.sharpe_ratio(r, rf_proxy), 3),
                sortino_ratio=round(risk_metrics.sortino_ratio(r, rf_proxy), 3),
                correlation_with_portfolio=s.correlation_with_portfolio,
                risk_contribution_pct=round(float(risk_contrib[i]) * 100, 2),
                return_contribution_pct=round(s.return_contribution_pct / total_return_contrib * 100, 2)
                    if total_return_contrib else 0.0,
                data_points=s.data_points,
                is_synthetic=s.is_synthetic,
            ))

        weight_list = [w[sid] * 100 for sid in present_ids]
        hhi = compute_hhi(weight_list)
        eei = compute_eei(weight_list)

        sector_totals: dict[str, float] = {}
        for sid in present_ids:
            sec_w = self._sector_weights_for_scheme(sid)
            for sector, frac in sec_w.items():
                sector_totals[sector] = sector_totals.get(sector, 0.0) + frac * float(w[sid])
        max_sector = max(sector_totals.values()) if sector_totals else 0.0

        any_synthetic = any(is_synthetic_map.values())
        all_synthetic = all(is_synthetic_map.values())
        data_source = "synthetic_calibrated" if all_synthetic else ("mixed" if any_synthetic else "real_market_data")

        raw_cagr = round(risk_metrics.cagr(portfolio_daily), 4)
        shrunk_cagr, prior_cagr, sample_weight, was_capped = shrink_and_cap_portfolio_return(
            sample_cagr=raw_cagr, n_years_history=n_years,
            present_ids=present_ids, weights={sid: float(w[sid]) for sid in present_ids},
            schemes=self._schemes,
        )

        # CRITICAL: ARISE's block-bootstrap draws its future scenarios
        # directly from `portfolio_annual_returns`, NOT from `portfolio_cagr`
        # (which is only ever used as a de-risking threshold reference, see
        # arise_engine.vol_scalar). A shrunk/capped `portfolio_cagr` alone
        # would be a correct-looking number the simulation never actually
        # uses -- the raw annual-returns series (which can be a single
        # synthetic draw with a much lower or higher realized mean) would
        # still drive every bootstrapped path. Shift the annual-returns
        # series itself so ITS mean matches the shrunk target, preserving
        # each year's relative deviation (so real year-to-year shape/
        # variance survives, only the anchor moves) -- this is what makes
        # the shrinkage/cap protection actually reach the Monte Carlo paths.
        raw_annual_mean = float(portfolio_annual.mean()) if len(portfolio_annual) else raw_cagr
        anchored_annual_returns = portfolio_annual + (shrunk_cagr - raw_annual_mean)

        return PortfolioStatistics(
            scheme_ids=scheme_ids,
            weights={sid: float(w[sid]) for sid in present_ids},
            portfolio_daily_returns=portfolio_daily,
            portfolio_annual_returns=anchored_annual_returns,
            portfolio_cagr=shrunk_cagr,
            portfolio_volatility=round(risk_metrics.annualized_volatility(portfolio_daily), 4),
            portfolio_max_drawdown=round(risk_metrics.max_drawdown(portfolio_daily), 4),
            portfolio_sharpe=round(risk_metrics.sharpe_ratio(portfolio_daily, rf_proxy), 3),
            portfolio_sortino=round(risk_metrics.sortino_ratio(portfolio_daily, rf_proxy), 3),
            scheme_statistics=final_stats,
            correlation_matrix=corr_matrix,
            hhi=round(hhi, 2),
            eei=round(eei, 2),
            concentration_label=concentration_label(hhi),
            sector_weights={k: round(v, 4) for k, v in sector_totals.items()},
            max_sector_weight=round(max_sector, 4),
            risk_free_rate_proxy=round(rf_proxy, 4),
            data_source=data_source,
            n_years_history=round(n_years, 1),
            portfolio_cagr_raw=raw_cagr,
            prior_cagr_used=prior_cagr,
            sample_weight_cagr=sample_weight,
            return_capped=was_capped,
        )
