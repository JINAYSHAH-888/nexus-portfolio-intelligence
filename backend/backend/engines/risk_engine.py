"""
RiskEngine — Portfolio risk analytics using real NSE price data.

Methods:
  1. Ledoit-Wolf shrinkage covariance (Oracle Approximating Shrinkage)
     Better than sample covariance for small T / large N portfolios.

  2. VaR & CVaR
     - Parametric (Gaussian): fast, assumes normality
     - Historical simulation: non-parametric, uses actual return distribution
     - Monte Carlo GBM: forward-looking, 10,000 paths × 252 days

  3. Portfolio metrics
     - Annualized return, volatility, Sharpe ratio
     - Max drawdown (from price history)
     - Beta vs Nifty 50
     - Correlation matrix

All numbers use real yfinance prices. Falls back to synthetic if unavailable.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# ── Risk parameters ────────────────────────────────────────────────────────────
# The risk-free rate is NO LONGER a hardcoded constant. It now resolves from the
# overnight fund's own disclosed YTM (real factsheet data), with an operator
# override via settings.RISK_FREE_RATE, and is clamped to a plausible band.
#
# It previously sat at a hardcoded 0.065 "RBI repo rate proxy" while
# portfolio_statistics.get_risk_free_rate_proxy() independently derived a
# DIFFERENT rate for ARISE -- so the risk dashboard's Sharpe and the retirement
# engine disagreed about what riskless means. See core/assumptions.py.
def _risk_free_rate() -> float:
    from backend.core.assumptions import resolve_risk_free_rate
    return resolve_risk_free_rate()


RISK_FREE_RATE = _risk_free_rate()
TRADING_DAYS = 252
CONFIDENCE_95 = 0.05        # 1 - 0.95
CONFIDENCE_99 = 0.01        # 1 - 0.99
GBM_PATHS = 10_000
GBM_HORIZON_DAYS = 252
# Degrees of freedom for the Student-t shocks in the forward simulation.
# Matches arise_engine.RETURN_T_DF so the risk dashboard and the retirement
# projection assume the same tail behaviour instead of contradicting each
# other (one Gaussian, one fat-tailed) about the same portfolio.
MC_T_DF = 5.0

# Benchmark for the COMBINED portfolio (individual schemes use their own
# declared benchmark -- see data_loaders/benchmark_registry.py). Nifty 500
# rather than Nifty 50: it spans large, mid and small caps, so it is a
# defensible yardstick for a mixed portfolio instead of a large-cap index
# standing in for everything.
PORTFOLIO_BENCHMARK_TICKER = "^CRSLDX"
PORTFOLIO_BENCHMARK_NAME = "Nifty 500 (price proxy for Nifty 500 TRI)"


@dataclass
class PortfolioMetrics:
    """Single-scheme risk metrics."""
    scheme_id: str
    scheme_name: str

    # Return metrics (annualized)
    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    max_drawdown: float         # maximum peak-to-trough loss (e.g. -0.35 = -35%)

    # Beta
    beta_vs_nifty: float | None

    # VaR / CVaR (daily, 95% confidence)
    var_95_parametric: float    # e.g. -0.018 = -1.8% daily loss at 95% CI
    cvar_95_parametric: float
    var_95_historical: float
    cvar_95_historical: float

    # VaR / CVaR (daily, 99% confidence)
    var_99_parametric: float
    cvar_99_parametric: float
    var_99_historical: float
    cvar_99_historical: float

    # Monte Carlo (1-year horizon, 10,000 paths, Student-t shocks)
    mc_p10_return: float        # 10th percentile 1-year return
    mc_p25_return: float
    mc_p50_return: float        # median
    mc_p75_return: float
    mc_p90_return: float
    mc_ruin_probability: float  # P(1-year return < -50%)

    # ── Risk-adjusted return, beyond Sharpe ────────────────────────────────
    # Sharpe divides by total volatility, which penalises upside exactly as
    # hard as downside and assumes a normal distribution Indian equity does
    # not have. These three say what it cannot.
    sortino_ratio: float        # excess return per unit of DOWNSIDE deviation
    calmar_ratio: float         # CAGR per unit of worst drawdown actually endured
    downside_deviation: float   # annualized, below-zero returns only
    ulcer_index: float          # RMS depth of the drawdown PATH, not just its low

    # ── Versus the benchmark ───────────────────────────────────────────────
    alpha_vs_nifty: float | None      # CAPM alpha, annualized
    r_squared_vs_nifty: float | None  # how much of the move beta even explains
    tracking_error: float | None      # annualized stdev of active return
    information_ratio: float | None   # SEBI now mandates this for equity schemes
    up_capture: float | None          # share of Nifty's rallies captured
    down_capture: float | None        # share of its falls taken (lower is better)

    # ── Tail risk the Gaussian estimate misses ─────────────────────────────
    var_95_cornish_fisher: float      # skew/kurtosis-adjusted parametric VaR
    var_99_cornish_fisher: float
    var_backtest_95: dict             # Kupiec POF: does the 95% VaR hold up?

    # ── Is one number even describing one regime? ──────────────────────────
    rolling_stability: dict           # spread of trailing 1y vol and CAGR

    # Return in both spaces, so neither can be mistaken for the other.
    arithmetic_return: float    # mean of ONE year's draw (simulation input)
    geometric_return: float     # the CAGR actually compounded (what you earned)


    # Data quality
    data_points: int            # number of daily observations
    is_synthetic: bool
    data_source: str = "unknown"

    # ── WHICH benchmark the figures above were measured against ────────────
    # Previously every scheme was measured against Nifty 50, which only 6 of
    # the 35 schemes are actually benchmarked to. These three fields make the
    # comparison auditable instead of assumed.
    benchmark_declared: str = ""      # exactly what the factsheet states
    benchmark_used: str | None = None # the series we could really source
    benchmark_quality: str = "none"   # exact | price_proxy | close_proxy | none
    benchmark_note: str = ""          # why, in one line

    # Is this fund actually managed, and is the fee proportionate to the
    # activeness being paid for? Return-based, following ESMA's own 2020
    # method. See analytics/activeness.py.
    activeness: dict = field(default_factory=dict)
    expense_ratio_direct: float | None = None
    expense_ratio_regular: float | None = None


@dataclass
class PortfolioRiskResult:
    """Risk analytics for a selected set of schemes (as an equal-weight portfolio)."""
    scheme_ids: list[str]

    # Individual scheme metrics
    scheme_metrics: list[PortfolioMetrics]

    # Combined equal-weight portfolio metrics
    portfolio_annualized_return: float
    portfolio_volatility: float
    portfolio_sharpe: float
    portfolio_max_drawdown: float
    portfolio_var_95: float
    portfolio_cvar_95: float

    # Correlation matrix (scheme × scheme)
    correlation_matrix: list[list[float]]
    correlation_labels: list[str]       # scheme names for matrix labeling

    # Ledoit-Wolf shrinkage covariance (annualized)
    covariance_matrix: list[list[float]]

    # Diversification benefit
    weighted_avg_vol: float             # weighted avg of individual vols
    diversification_ratio: float        # portfolio_vol / weighted_avg_vol (< 1 = diversified)


    data_source: str
    n_observations: int

    # ── The weights these numbers actually describe ────────────────────────
    # Previously the risk dashboard ALWAYS reported an equal-weight portfolio
    # while ARISE projected the investor's real allocation, so the two pages
    # described different portfolios to the same person. The weights are now
    # an input and are echoed back, so the answer states what it is an answer
    # about.
    weights: dict[str, float] = field(default_factory=dict)
    weighting_scheme: str = "equal"     # "investor" | "equal"
    # Named so nobody has to guess what the portfolio beta is relative to.
    portfolio_benchmark: str | None = None

    # Risk-balanced reference weights (Hierarchical Risk Parity). The platform
    # previously diagnosed -- "your overlap is high, your Sharpe is 0.33" --
    # and stopped. This shows what balance would look like. It is explicitly
    # NOT a recommendation; see analytics/hrp.py for what it omits.
    suggested_allocation: dict = field(default_factory=dict)

    # Portfolio-level risk-adjusted and benchmark-relative measures
    portfolio_sortino: float = 0.0
    portfolio_calmar: float = 0.0
    portfolio_beta: float | None = None
    portfolio_alpha: float | None = None
    portfolio_tracking_error: float | None = None
    portfolio_information_ratio: float | None = None
    portfolio_var_95_cornish_fisher: float = 0.0
    portfolio_var_backtest_95: dict = field(default_factory=dict)
    # GARCH(1,1) + filtered historical simulation: VaR that tracks the current
    # volatility regime (see backend/ml/garch.py and risk_garch.json).
    conditional_risk: dict = field(default_factory=dict)

    # Conservatism, stated rather than buried (see settings.MODEL_CONSERVATISM_HAIRCUT)
    expected_return_unbiased: float = 0.0     # what the corrected maths gives
    conservatism_haircut: float = 0.0         # the deliberate deduction applied
    expected_return_after_haircut: float = 0.0


class RiskEngine:
    """
    Computes portfolio risk metrics using real NSE price data via yfinance.

    Usage:
        engine = RiskEngine(
            schemes_path="backend/data/schemes_metadata.json",
            holdings_path="backend/data/sample_holdings.json",
        )
        result = engine.compute_risk(["nippon-large-cap-fund", "nippon-multi-cap-fund"])
    """

    def __init__(
        self,
        schemes_path: str | Path,
        holdings_path: str | Path,
        price_period: str = "3y",
        force_synthetic: bool = False,
    ) -> None:
        import json
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

        self._benchmark_cache: dict[str, object] = {}

        try:
            from backend.data_loaders.market_data import MarketDataLoader
            self._loader = MarketDataLoader(force_synthetic=force_synthetic)
        except Exception:
            self._loader = None

    # ── Ledoit-Wolf shrinkage ──────────────────────────────────────────────────
    @staticmethod
    def _ledoit_wolf(returns: np.ndarray) -> np.ndarray:
        """
        Ledoit-Wolf Oracle Approximating Shrinkage estimator.

        Shrinks the sample covariance toward a structured target
        (scaled identity matrix) to reduce estimation error when T < 5N.

        Args:
            returns: (T × N) array of daily returns

        Returns:
            (N × N) shrunk covariance matrix (daily scale)
        """
        try:
            from sklearn.covariance import LedoitWolf
            lw = LedoitWolf()
            lw.fit(returns)
            return lw.covariance_
        except ImportError:
            # Fallback: Oracle Approximating Shrinkage (Ledoit-Wolf 2004 formula)
            T, N = returns.shape
            S = np.cov(returns, rowvar=False)
            mu = np.trace(S) / N
            target = mu * np.eye(N)

            # Frobenius norm of S minus target
            alpha = np.sum((S - target) ** 2)
            # Ledoit-Wolf shrinkage intensity
            kappa = np.clip(
                (np.sum([np.sum(np.outer(r - S.mean(axis=0), r - S.mean(axis=0)) ** 2)
                         for r in returns]) / T) / (alpha * (T - 1) / T),
                0, 1,
            )
            return (1 - kappa) * S + kappa * target

    # ── VaR and CVaR ──────────────────────────────────────────────────────────
    @staticmethod
    def _parametric_var_cvar(
        returns: np.ndarray, confidence: float
    ) -> tuple[float, float]:
        """Gaussian parametric VaR and CVaR at given confidence level."""
        from scipy import stats
        mu = float(returns.mean())
        sigma = float(returns.std())
        z = stats.norm.ppf(confidence)
        var = mu + sigma * z
        cvar = mu - sigma * stats.norm.pdf(z) / confidence
        return float(var), float(cvar)

    @staticmethod
    def _historical_var_cvar(
        returns: np.ndarray, confidence: float
    ) -> tuple[float, float]:
        """Non-parametric historical VaR and CVaR."""
        sorted_r = np.sort(returns)
        n = len(sorted_r)
        var_idx = int(confidence * n)
        var = float(sorted_r[var_idx])
        cvar = float(sorted_r[:max(1, var_idx)].mean())
        return var, cvar

    @staticmethod
    def _max_drawdown(prices: np.ndarray) -> float:
        """Maximum peak-to-trough drawdown from a price series."""
        peak = np.maximum.accumulate(prices)
        drawdowns = (prices - peak) / np.where(peak > 0, peak, 1)
        return float(drawdowns.min())

    # ── Monte Carlo forward simulation ─────────────────────────────────────────
    @staticmethod
    def _monte_carlo_gbm(
        mu_arithmetic: float,
        sigma: float,
        n_paths: int = GBM_PATHS,
        horizon: int = GBM_HORIZON_DAYS,
        seed: int = 42,
        fat_tails: bool = True,
    ) -> np.ndarray:
        """
        One-year forward return distribution.

        `mu_arithmetic` is the ARITHMETIC annual mean -- the expected value of
        a single year's return -- which is the industry convention for a
        simulator input, because the simulator produces the volatility drag
        itself.

        TWO THINGS THIS FIXES.

        1. THE DOUBLE ITO CORRECTION. This previously took `r.mean() * 252`
           computed on daily LOG returns, which is already a log drift, and
           then subtracted sigma^2/2 from it as though it were arithmetic.
           Every forward percentile came out ~sigma^2/2 a year too pessimistic
           -- 3.3 percentage points annually at sigma = 0.25. The conversion
           now goes through `log_drift_from_arithmetic`, which applies the Ito
           term exactly once.

        2. GAUSSIAN SHOCKS ON A FAT-TAILED ASSET. Indian equity returns are
           negatively skewed and leptokurtic; normal shocks understate the
           left tail precisely where a risk model is supposed to be useful.
           Shocks are now standardized Student-t, matching the t(5) process
           ARISE already uses, so the two engines no longer disagree about
           the shape of the world. Variance is preserved exactly (the raw t
           is divided by sqrt(df/(df-2))), so this changes the TAILS without
           changing the volatility the caller asked for.

        Returns (n_paths,) of total 1-year returns, e.g. 0.12 = +12%.
        """
        from backend.analytics.risk_metrics import log_drift_from_arithmetic

        rng = np.random.default_rng(seed)
        dt = 1.0 / TRADING_DAYS

        # Contains the Ito term already -- do not subtract another.
        annual_log_drift = log_drift_from_arithmetic(mu_arithmetic, sigma)
        drift = annual_log_drift * dt
        diffusion = sigma * math.sqrt(dt)

        if fat_tails:
            from scipy import stats as _st
            raw = _st.t.rvs(df=MC_T_DF, size=(n_paths, horizon), random_state=rng)
            Z = raw / math.sqrt(MC_T_DF / (MC_T_DF - 2))   # unit variance
        else:
            Z = rng.standard_normal((n_paths, horizon))

        log_returns = drift + diffusion * Z
        return np.exp(log_returns.sum(axis=1)) - 1

    # ── Benchmark series ───────────────────────────────────────────────────────
    def _benchmark_series(self, ticker: str | None):
        """
        Daily returns for one benchmark proxy, memoised for this engine so a
        10-scheme portfolio sharing a benchmark fetches it once, not ten times.
        """
        import pandas as pd

        if not ticker or not self._loader:
            return None
        if ticker in self._benchmark_cache:
            return self._benchmark_cache[ticker]

        series = None
        try:
            rd = self._loader.get_returns(ticker, period=self._price_period)
            # A synthetic fallback for a BENCHMARK is worse than no benchmark:
            # beta against invented data is a number with no meaning. Decline.
            if not rd.is_synthetic and len(rd.returns) >= 60:
                series = (
                    rd.returns
                    if isinstance(rd.returns, pd.Series)
                    else pd.Series(rd.returns)
                )
            else:
                logger.info(
                    "Benchmark %s unusable (synthetic=%s, n=%d); benchmark-relative "
                    "metrics will be withheld rather than computed on invented data.",
                    ticker, rd.is_synthetic, len(rd.returns),
                )
        except Exception as e:
            logger.warning("Benchmark %s fetch failed: %s", ticker, e)

        self._benchmark_cache[ticker] = series
        return series

    # ── Weights ────────────────────────────────────────────────────────────────
    @staticmethod
    def _conditional_risk_v2(port_daily_s) -> dict:
        """Risk v2: the portfolio's GARCH variance blended with the Nifty HAR-X
        forecast mapped by beta; FHS tail at 95%, EVT at 99%. {} if the
        model is untrained or the overlap with market history is too short."""
        import pandas as pd

        from backend.ml import data as D
        from backend.ml import vol_models as V
        from backend.ml.data import load_artifact

        art = load_artifact("risk_v2.json")
        vm_path = D.MODELS / "risk_market_var.csv.gz"
        if not art or not vm_path.exists() or not isinstance(port_daily_s, pd.Series):
            return {}
        vm = pd.read_csv(vm_path, index_col=0, parse_dates=True)["vm"]
        mret = D.market_log_returns()["nifty50"]
        s_ = port_daily_s.copy()
        s_.index = pd.to_datetime(s_.index).tz_localize(None).normalize()
        j = pd.concat([s_.rename("r"), mret.rename("m"), vm.rename("vm")], axis=1, join="inner").dropna().iloc[-750:]
        if len(j) < 500 or j.r.std() == 0:
            return {}
        live = art["live_market_variance"]
        out: dict = {"model": "GARCH + HAR-X blend (India VIX, overnight S&P 500, USD/INR, crude)",
                     "market_forecast_as_of": live["as_of"]}
        for lvl in (0.95, 0.99):
            for h in (1, 10):
                res = V.blended_var_es(j.r.values, j.m.values, j.vm.values, live["1d"], lvl,
                                       horizon_days=h, vm_horizon=live["10d"] if h == 10 else None)
                out[f"var_{int(lvl * 100)}_{h}d"] = round(-res["var"], 4)
                out[f"es_{int(lvl * 100)}_{h}d"] = round(-res["es"], 4)
                out[f"tail_{int(lvl * 100)}"] = res["tail"]
        pg = res["garch"]
        out["beta_to_nifty"] = round(res["beta"], 3)
        out["current_vol_annual"] = round(res["sd_daily_next"] * math.sqrt(252), 4)
        long_run = math.sqrt(0.5 * pg.long_run_vol_daily ** 2 + 0.5 * (
            res["beta"] ** 2 * float(j.vm.mean()) + float(np.var(j.r.values - res["beta"] * j.m.values))))
        out["long_run_vol_annual"] = round(long_run * math.sqrt(252), 4)
        out["persistence"] = round(pg.persistence, 4)
        out["half_life_days"] = (round(math.log(0.5) / math.log(pg.persistence), 1)
                                 if 0 < pg.persistence < 1 else None)
        out["regime"] = ("elevated" if out["current_vol_annual"] > 1.2 * out["long_run_vol_annual"]
                         else "calm" if out["current_vol_annual"] < 0.8 * out["long_run_vol_annual"] else "normal")
        out["validation_v2"] = {
            "volatility_forecast_qlike": art["volatility_forecast_qlike"],
            "diebold_mariano_garch_vs_harx": art["diebold_mariano_garch_vs_harx"],
            "fund_var_backtest": art["fund_var_backtest"],
        }
        return out

    @staticmethod
    def _conditional_risk(port_daily: np.ndarray, port_daily_s=None) -> dict:
        """Risk v2 when trained, else GARCH-FHS on the portfolio's OWN daily
        returns. {} when the history is too short to fit honestly (< 500 days)."""
        from backend.ml import garch as G
        from backend.ml.data import load_artifact

        try:
            v2 = RiskEngine._conditional_risk_v2(port_daily_s)
            if v2:
                return v2
        except Exception as e:  # fall back to v1 rather than fail the page
            logger.warning("risk v2 failed, using GARCH-FHS: %s", e)

        r = np.asarray(port_daily, dtype=float)
        r = r[np.isfinite(r)]
        if len(r) < 500 or r.std() == 0:
            return {}
        try:
            p = G.fit_garch(r)
            out: dict = {
                "model": "GARCH(1,1) + filtered historical simulation",
                "current_vol_annual": None, "long_run_vol_annual": round(p.long_run_vol_daily * math.sqrt(252), 4),
                "persistence": round(p.persistence, 4),
                "half_life_days": round(math.log(0.5) / math.log(p.persistence), 1) if 0 < p.persistence < 1 else None,
            }
            for lvl in (0.95, 0.99):
                for h in (1, 10):
                    var, es, sig = G.fhs_var_es(r, p, lvl, h)
                    # Sign convention of this engine: losses are negative returns.
                    out[f"var_{int(lvl * 100)}_{h}d"] = round(-var, 4)
                    out[f"es_{int(lvl * 100)}_{h}d"] = round(-es, 4)
            out["current_vol_annual"] = round(sig * math.sqrt(252), 4)
            out["regime"] = (
                "elevated" if out["current_vol_annual"] > 1.2 * out["long_run_vol_annual"]
                else "calm" if out["current_vol_annual"] < 0.8 * out["long_run_vol_annual"] else "normal"
            )
            # The portfolio's own out-of-sample check (last ~250+ days).
            if len(r) >= 750:
                bt = G.rolling_backtest(r, level=0.95, window=500, refit_every=250)
                out["backtest_95"] = {k: bt[k] for k in ("garch_fhs", "historical")}
            art = load_artifact("risk_garch.json")
            if art:
                out["validation"] = art["validation"]["results"]
            return out
        except Exception as e:  # never let the add-on break the risk page
            logger.warning("conditional risk failed: %s", e)
            return {}

    @staticmethod
    def _resolve_weights(
        scheme_ids: list[str], weights: dict[str, float] | None
    ) -> tuple[dict[str, float], str]:
        """
        (weights, scheme_label). Mirrors analysis_service._effective_weights:
        weights must cover exactly the selected ids and sum to 1.0, else the
        engine falls back to equal weight and SAYS it did rather than
        presenting a silently different portfolio as the investor's own.
        """
        equal = 1.0 / len(scheme_ids) if scheme_ids else 0.0
        if not weights:
            return dict.fromkeys(scheme_ids, equal), "equal"
        if set(weights) != set(scheme_ids) or abs(sum(weights.values()) - 1.0) > 0.01:
            logger.warning(
                "Supplied weights do not match the selection (or do not sum to 1.0); "
                "falling back to equal weight for the risk computation."
            )
            return dict.fromkeys(scheme_ids, equal), "equal"
        return dict(weights), "investor"

    # ── Benchmark-relative metrics ─────────────────────────────────────────────
    @staticmethod
    def _benchmark_metrics(
        series: "pd.Series",
        nifty: "pd.Series | None",
        scheme_meta: dict,
    ) -> tuple[
        float | None, float | None, float | None,
        float | None, float | None, float | None, float | None,
    ]:
        """
        (beta, alpha, r_squared, tracking_error, information_ratio,
         up_capture, down_capture) against Nifty 50.

        THE BUG THIS REPLACES, which is worth stating plainly because it was
        invisible: beta was computed inline as

            pd.DataFrame({"scheme": returns_series,
                          "nifty":  pd.Series(nifty_ndarray)}).dropna()

        The scheme series carries a DatetimeIndex. Wrapping the benchmark
        ndarray in a bare `pd.Series` gives it a RangeIndex. A DataFrame built
        from those two UNIONS the indexes, so every row has a value in exactly
        one column, `dropna()` removes all of them, the `len >= 60` guard
        fails, and beta comes back None. Every time. On real data.

        It only ever "worked" in tests because synthetic returns also carry a
        RangeIndex, so the two happened to line up -- the failure was
        triggered by having GOOD data, which is the worst possible property
        for a bug to have. Verified live: beta=None on 747 real observations.

        Alignment is now explicit: both sides are reindexed on their shared
        dates, and if the indexes are genuinely incompatible (one synthetic,
        one real) the overlapping TAIL is compared positionally rather than
        silently returning nothing. Where there is still not enough overlap,
        the factsheet-disclosed beta is used and `r_squared` is None to mark
        that it was not derived here.
        """
        import pandas as pd

        from backend.analytics import risk_metrics as rm

        disclosed = scheme_meta.get("beta")
        fallback = (float(disclosed) if disclosed is not None else None, None, None,
                    None, None, None, None)

        if nifty is None or len(nifty) == 0:
            return fallback

        bench = nifty if isinstance(nifty, pd.Series) else pd.Series(nifty)

        aligned = pd.DataFrame({"p": series, "b": bench}).dropna()
        if len(aligned) < 60:
            # Incompatible index TYPES (real vs synthetic) rather than
            # genuinely disjoint dates: fall back to positional comparison of
            # the common tail, which is the honest reading of two series that
            # describe the same period but label it differently.
            n = min(len(series), len(bench))
            if n >= 60:
                aligned = pd.DataFrame({
                    "p": series.to_numpy(dtype=float)[-n:],
                    "b": bench.to_numpy(dtype=float)[-n:],
                })
            else:
                return fallback

        beta, alpha = rm.beta_alpha(aligned["p"], aligned["b"], RISK_FREE_RATE)
        if beta is None:
            return fallback

        corr = float(aligned["p"].corr(aligned["b"]))
        r2 = corr**2 if math.isfinite(corr) else None
        te = rm.tracking_error(aligned["p"], aligned["b"])
        ir = rm.information_ratio(aligned["p"], aligned["b"])
        up_cap, down_cap = rm.up_down_capture(aligned["p"], aligned["b"])
        return beta, alpha, r2, te, ir, up_cap, down_cap

    # ── Per-scheme metrics ─────────────────────────────────────────────────────
    def _compute_scheme_metrics(
        self, sid: str, nifty_returns: "pd.Series | None" = None
    ) -> tuple[PortfolioMetrics, "pd.Series | None"]:
        """
        Compute risk metrics for a single scheme.

        `nifty_returns` is a pandas Series and must KEEP ITS INDEX. The
        previous signature took a bare ndarray, which is what silently broke
        beta: see `_benchmark_metrics`.

        Returns (PortfolioMetrics, daily_returns Series or None).
        """
        scheme = self._schemes.get(sid, {})
        name = scheme.get("name", sid)
        holdings = self._holdings.get(sid, [])

        returns_series = None
        is_synthetic = True
        n_obs = 0
        source_label = "unknown"

        if self._loader:
            try:
                from backend.core.async_utils import run_async_safely
                rd = run_async_safely(
                    self._loader.get_scheme_returns_with_fallback(
                        {**scheme, "id": sid}, holdings, period=self._price_period
                    )
                )
                if len(rd.returns) >= 60:
                    returns_series = rd.returns
                    is_synthetic = rd.is_synthetic
                    n_obs = len(rd.returns)
                    source_label = getattr(rd, "source", "unknown")
            except Exception as e:
                logger.warning(f"Could not fetch returns for {sid}: {e}")

        if returns_series is None:
            # Use risk metrics from schemes_metadata if available
            vol = scheme.get("std_deviation")
            sharpe = scheme.get("sharpe_ratio")
            beta = scheme.get("beta")

            # If we have vol from metadata, back-derive a synthetic return series
            if vol is not None:
                from backend.data_loaders.market_data import _synthetic_returns
                rd = _synthetic_returns(sid, n_days=756)  # 3 years
                returns_series = rd.returns
                is_synthetic = True
                n_obs = len(rd.returns)
                source_label = "synthetic"
            else:
                from backend.data_loaders.market_data import _synthetic_returns
                rd = _synthetic_returns(sid, n_days=756)
                returns_series = rd.returns
                is_synthetic = True
                n_obs = len(rd.returns)
                source_label = "synthetic"

        import pandas as pd

        from backend.analytics import activeness as _activeness
        from backend.analytics import risk_metrics as rm

        series = (
            returns_series
            if isinstance(returns_series, pd.Series)
            else pd.Series(returns_series)
        )
        r = series.to_numpy(dtype=float)

        # ── Return, in BOTH spaces, never conflated ────────────────────────
        # `cagr` is what compounded; `annualized_arithmetic_return` is the
        # mean of a single year and the correct input to a simulator. The
        # old code computed mean(log)*252 and used that one number as both.
        geo_return = rm.cagr(series)
        arith_return = rm.annualized_arithmetic_return(series)
        ann_vol = float(r.std() * math.sqrt(TRADING_DAYS))
        # Sharpe is quoted against the COMPOUND return, matching how Indian
        # factsheets and SEBI's own disclosure framing read it.
        sharpe = (geo_return - RISK_FREE_RATE) / ann_vol if ann_vol > 0 else 0.0

        mdd = self._max_drawdown(np.exp(np.cumsum(r)))

        # Each scheme is measured against ITS OWN declared benchmark. Passing
        # one market-wide series for all of them is what made beta, alpha,
        # tracking error and the SEBI-mandated Information Ratio meaningless
        # for the 29 of 35 schemes that are not benchmarked to Nifty 50.
        from backend.data_loaders.benchmark_registry import resolve as _resolve_bm

        mapping = _resolve_bm(scheme.get("benchmark"))
        bench_series = self._benchmark_series(mapping.ticker)

        # SYMMETRY GUARD. `_benchmark_series` already refuses a synthetic
        # BENCHMARK; the scheme side needs the same refusal. Correlating a
        # generated scheme series against a real index produces confident
        # nonsense -- measured on nippon-flexi-cap-fund, R-squared 0.00 and a
        # 21% tracking error, which would have been published as "genuinely
        # active" when it only meant "these two series are unrelated".
        if is_synthetic:
            bench_series = None
            mapping = type(mapping)(
                declared=mapping.declared, ticker=None, quality="none",
                note=(
                    "This scheme's own return history is synthetic (no real NAV or "
                    "holdings data was available), so comparing it to a real index "
                    "would measure the simulation, not the manager. "
                    "Benchmark-relative metrics are withheld."
                ),
            )

        if mapping.usable and bench_series is None:
            # Registry had a proxy but the data did not arrive.
            bench_note = (
                f"{mapping.note} (Series unavailable at request time, so "
                f"benchmark-relative metrics are withheld.)"
            )
            bench_used, bench_quality = None, "none"
        else:
            bench_note = mapping.note
            bench_used = mapping.ticker if bench_series is not None else None
            bench_quality = mapping.quality if bench_series is not None else "none"

        beta, alpha, r2, te, info_ratio, up_cap, down_cap = self._benchmark_metrics(
            series, bench_series, scheme
        )

        # VaR / CVaR
        v95p, cv95p = self._parametric_var_cvar(r, CONFIDENCE_95)
        v99p, cv99p = self._parametric_var_cvar(r, CONFIDENCE_99)
        v95h, cv95h = rm.historical_var_cvar(r, CONFIDENCE_95)
        v99h, cv99h = rm.historical_var_cvar(r, CONFIDENCE_99)
        v95cf = rm.cornish_fisher_var(r, CONFIDENCE_95)
        v99cf = rm.cornish_fisher_var(r, CONFIDENCE_99)
        backtest = rm.kupiec_pof_test(r, v95cf, CONFIDENCE_95)

        # Forward simulation takes the ARITHMETIC mean (see _monte_carlo_gbm).
        mc_returns = self._monte_carlo_gbm(arith_return, ann_vol)
        mc_sorted = np.sort(mc_returns)
        n_mc = len(mc_sorted)

        return PortfolioMetrics(
            scheme_id=sid,
            scheme_name=name,
            # Kept as the COMPOUND rate: this is the field every existing
            # caller and the frontend already render as "return", and the
            # compound rate is what an investor actually earned.
            annualized_return=round(geo_return, 4),
            annualized_volatility=round(ann_vol, 4),
            sharpe_ratio=round(sharpe, 3),
            max_drawdown=round(mdd, 4),
            beta_vs_nifty=round(beta, 3) if beta is not None else None,
            var_95_parametric=round(v95p, 4),
            cvar_95_parametric=round(cv95p, 4),
            var_95_historical=round(v95h, 4),
            cvar_95_historical=round(cv95h, 4),
            var_99_parametric=round(v99p, 4),
            cvar_99_parametric=round(cv99p, 4),
            var_99_historical=round(v99h, 4),
            cvar_99_historical=round(cv99h, 4),
            mc_p10_return=round(float(mc_sorted[int(0.10 * n_mc)]), 4),
            mc_p25_return=round(float(mc_sorted[int(0.25 * n_mc)]), 4),
            mc_p50_return=round(float(mc_sorted[int(0.50 * n_mc)]), 4),
            mc_p75_return=round(float(mc_sorted[int(0.75 * n_mc)]), 4),
            mc_p90_return=round(float(mc_sorted[int(0.90 * n_mc)]), 4),
            mc_ruin_probability=round(float((mc_returns < -0.50).mean()), 4),
            sortino_ratio=round(rm.sortino_ratio(series, RISK_FREE_RATE), 3),
            calmar_ratio=round(rm.calmar_ratio(series), 3),
            downside_deviation=round(rm.downside_deviation(series), 4),
            ulcer_index=round(rm.ulcer_index(series), 3),
            alpha_vs_nifty=round(alpha, 4) if alpha is not None else None,
            r_squared_vs_nifty=round(r2, 3) if r2 is not None else None,
            tracking_error=round(te, 4) if te is not None else None,
            information_ratio=round(info_ratio, 3) if info_ratio is not None else None,
            up_capture=round(up_cap, 3) if up_cap is not None else None,
            down_capture=round(down_cap, 3) if down_cap is not None else None,
            var_95_cornish_fisher=round(v95cf, 4),
            var_99_cornish_fisher=round(v99cf, 4),
            var_backtest_95=backtest,
            rolling_stability=rm.stability_of_metrics(series),
            arithmetic_return=round(arith_return, 4),
            geometric_return=round(geo_return, 4),
            benchmark_declared=scheme.get("benchmark") or "",
            benchmark_used=bench_used,
            benchmark_quality=bench_quality,
            benchmark_note=bench_note,
            activeness=_activeness.assess(
                tracking_error=te,
                r_squared=r2,
                expense_ratio=(
                    float(scheme["expense_ratio_direct"]) / 100.0
                    if scheme.get("expense_ratio_direct") is not None else None
                ),
                benchmark_quality=bench_quality,
            ),
            expense_ratio_direct=scheme.get("expense_ratio_direct"),
            expense_ratio_regular=scheme.get("expense_ratio_regular"),
            data_points=n_obs,
            is_synthetic=is_synthetic,
            data_source=source_label,
        ), series

    # ── Public API ─────────────────────────────────────────────────────────────
    def compute_risk(
        self, scheme_ids: list[str], weights: dict[str, float] | None = None
    ) -> PortfolioRiskResult:
        """
        Comprehensive risk analytics for the selected schemes.

        `weights` are FRACTIONS SUMMING TO 1.0, matching SchemeSelection.
        Omitted or malformed, they fall back to equal weight and the result
        says so via `weighting_scheme`.

        WHY THIS IS NOW AN ARGUMENT. The risk dashboard used to compute an
        equal-weight portfolio unconditionally, while ARISE projected the
        investor's ACTUAL allocation. The same person, on two pages of the
        same product, was shown risk and retirement figures for two different
        portfolios -- and an investor holding 90/10 was told the volatility of
        a 50/50. ARISE's own module docstring states the principle this
        violated: "ARISE analyzes ONLY the schemes the investor actually
        selected, at the weights they actually chose."
        """
        import pandas as pd

        from backend.analytics import risk_metrics as rm

        conditional_risk: dict = {}

        # The PORTFOLIO-level benchmark. A multi-scheme portfolio has no single
        # declared benchmark of its own, so a broad-market index is the honest
        # choice -- but it is named in the response (`portfolio_benchmark`)
        # rather than left for the reader to assume. Nifty 500 covers far more
        # of the investable universe than Nifty 50 and is the benchmark the
        # largest group of these schemes actually declares.
        #
        # Kept as a SERIES: converting it to a bare ndarray is what destroyed
        # the index that beta alignment depends on.
        nifty_r: pd.Series | None = self._benchmark_series(PORTFOLIO_BENCHMARK_TICKER)
        if nifty_r is None and self._loader:
            try:
                nd = self._loader.get_nifty_returns(period=self._price_period)
                nifty_r = (
                    nd.returns
                    if isinstance(nd.returns, pd.Series)
                    else pd.Series(nd.returns)
                )
            except Exception:
                pass

        w_map, weighting_scheme = self._resolve_weights(scheme_ids, weights)

        from backend.core.config import settings as _settings
        haircut = float(getattr(_settings, "MODEL_CONSERVATISM_HAIRCUT", 0.0) or 0.0)

        # Per-scheme metrics
        scheme_metrics: list[PortfolioMetrics] = []
        returns_arrays: dict[str, pd.Series] = {}

        for sid in scheme_ids:
            metrics, r = self._compute_scheme_metrics(sid, nifty_r)
            scheme_metrics.append(metrics)
            if r is not None:
                returns_arrays[sid] = r

        # Portfolio-level correlation + covariance
        all_data: list[tuple[str, str]] = []
        if len(returns_arrays) >= 2:
            df = pd.DataFrame({sid: pd.Series(r) for sid, r in returns_arrays.items()}).dropna()
            n_obs = len(df)

            ret_matrix = df.values  # (T × N)

            # Ledoit-Wolf covariance (daily)
            lw_cov_daily = self._ledoit_wolf(ret_matrix)
            lw_cov_annual = lw_cov_daily * TRADING_DAYS

            # Correlation matrix
            stds = np.sqrt(np.diag(lw_cov_daily))
            stds_outer = np.outer(stds, stds)
            corr_matrix = np.where(stds_outer > 0, lw_cov_daily / stds_outer, 0)
            np.fill_diagonal(corr_matrix, 1.0)

            # ── Weighted portfolio, at the investor's own allocation ──────
            present_ids = list(returns_arrays.keys())
            present_idx = [i for i, sid in enumerate(scheme_ids) if sid in returns_arrays]
            # Renormalise over the schemes that actually produced returns, so
            # one unavailable scheme rescales the rest instead of quietly
            # leaving the weights summing to less than 1.
            raw_w = np.array([w_map[scheme_ids[i]] for i in present_idx], dtype=float)
            w_present = raw_w / raw_w.sum() if raw_w.sum() > 0 else np.ones(len(raw_w)) / len(raw_w)
            cov_present = (
                lw_cov_annual[np.ix_(present_idx, present_idx)]
                if len(present_idx) > 1
                else lw_cov_annual
            )

            port_var = float(w_present @ cov_present @ w_present)
            port_vol = math.sqrt(port_var)

            # Portfolio daily returns at those weights -- the real series, from
            # which every portfolio statistic below is derived rather than
            # being averaged out of the per-scheme numbers.
            port_daily_s = (df[present_ids] * w_present).sum(axis=1)
            port_daily = port_daily_s.to_numpy(dtype=float)

            port_ret = rm.cagr(port_daily_s)
            port_arith = rm.annualized_arithmetic_return(port_daily_s)
            port_sharpe = (port_ret - RISK_FREE_RATE) / port_vol if port_vol > 0 else 0.0

            v95p, cv95p = self._parametric_var_cvar(port_daily, CONFIDENCE_95)
            port_mdd = self._max_drawdown(np.exp(np.cumsum(port_daily)))

            port_sortino = rm.sortino_ratio(port_daily_s, RISK_FREE_RATE)
            port_calmar = rm.calmar_ratio(port_daily_s)
            port_cf_var = rm.cornish_fisher_var(port_daily, CONFIDENCE_95)
            port_backtest = rm.kupiec_pof_test(port_daily, port_cf_var, CONFIDENCE_95)
            conditional_risk = self._conditional_risk(port_daily, port_daily_s)
            (p_beta, p_alpha, _p_r2, p_te, p_ir, _u, _d) = self._benchmark_metrics(
                port_daily_s, nifty_r, {}
            )

            # Weighted, not simple, average of the component volatilities.
            w_avg_vol = float(
                np.sum(w_present * np.array([
                    m.annualized_volatility for m in scheme_metrics
                    if m.scheme_id in returns_arrays
                ]))
            )
            div_ratio = port_vol / w_avg_vol if w_avg_vol > 0 else 1.0

            is_synth_any = any(m.is_synthetic for m in scheme_metrics)
            data_src = "synthetic_calibrated" if is_synth_any else "real_nse_prices"

            # ── Risk-balanced reference allocation ─────────────────────────
            # Uses the SAME Ledoit-Wolf covariance already computed above, so
            # the suggestion is consistent with the risk numbers beside it.
            from backend.analytics.hrp import allocation_suggestion

            suggested = allocation_suggestion(
                cov_present if len(present_idx) > 1 else lw_cov_annual,
                [scheme_ids[i] for i in present_idx],
                current_weights={scheme_ids[i]: float(w_present[k])
                                 for k, i in enumerate(present_idx)},
            )

        else:
            # Single scheme or no aligned data
            suggested = {
                "available": False,
                "reason": "A risk-balanced allocation needs at least two schemes "
                          "with overlapping return history.",
                "weights": {},
            }
            n_obs = scheme_metrics[0].data_points if scheme_metrics else 0
            m = scheme_metrics[0] if scheme_metrics else None
            port_vol = m.annualized_volatility if m else 0.0
            port_ret = m.annualized_return if m else 0.0
            port_sharpe = m.sharpe_ratio if m else 0.0
            port_mdd = m.max_drawdown if m else 0.0
            v95p = m.var_95_parametric if m else 0.0
            cv95p = m.cvar_95_parametric if m else 0.0
            w_avg_vol = port_vol
            div_ratio = 1.0
            lw_cov_annual = np.array([[port_vol**2]])
            corr_matrix = np.array([[1.0]])
            data_src = "insufficient_data"
            port_arith = m.arithmetic_return if m else 0.0
            port_sortino = m.sortino_ratio if m else 0.0
            port_calmar = m.calmar_ratio if m else 0.0
            port_cf_var = m.var_95_cornish_fisher if m else 0.0
            port_backtest = m.var_backtest_95 if m else {}
            p_beta = m.beta_vs_nifty if m else None
            p_alpha = m.alpha_vs_nifty if m else None
            p_te = m.tracking_error if m else None
            p_ir = m.information_ratio if m else None

        # Format matrices as nested lists for JSON serialization
        n_ids = len(scheme_ids)
        if corr_matrix.shape[0] == n_ids:
            corr_list = [[round(float(corr_matrix[i, j]), 4) for j in range(n_ids)] for i in range(n_ids)]
            cov_list = [[round(float(lw_cov_annual[i, j]), 6) for j in range(n_ids)] for i in range(n_ids)]
        else:
            corr_list = [[1.0]]
            cov_list = [[round(port_vol**2, 6)]]

        return PortfolioRiskResult(
            scheme_ids=scheme_ids,
            scheme_metrics=scheme_metrics,
            portfolio_annualized_return=round(port_ret, 4),
            portfolio_volatility=round(port_vol, 4),
            portfolio_sharpe=round(port_sharpe, 3),
            portfolio_max_drawdown=round(port_mdd, 4),
            portfolio_var_95=round(v95p, 4),
            portfolio_cvar_95=round(cv95p, 4),
            correlation_matrix=corr_list,
            correlation_labels=[self._schemes.get(s, {}).get("name", s)[:30] for s in scheme_ids],
            covariance_matrix=cov_list,
            weighted_avg_vol=round(w_avg_vol, 4),
            diversification_ratio=round(div_ratio, 4),
            data_source=data_src,
            n_observations=n_obs,
            weights={k: round(v, 6) for k, v in w_map.items()},
            weighting_scheme=weighting_scheme,
            portfolio_benchmark=(
                PORTFOLIO_BENCHMARK_NAME if nifty_r is not None else None
            ),
            suggested_allocation=suggested,
            portfolio_sortino=round(port_sortino, 3),
            portfolio_calmar=round(port_calmar, 3),
            portfolio_beta=round(p_beta, 3) if p_beta is not None else None,
            portfolio_alpha=round(p_alpha, 4) if p_alpha is not None else None,
            portfolio_tracking_error=round(p_te, 4) if p_te is not None else None,
            portfolio_information_ratio=round(p_ir, 3) if p_ir is not None else None,
            portfolio_var_95_cornish_fisher=round(port_cf_var, 4),
            portfolio_var_backtest_95=port_backtest,
            conditional_risk=conditional_risk,
            # The conservatism margin, stated in full: what the corrected
            # maths produced, what was deducted, and what remains. Hiding the
            # deduction inside the headline number is exactly how the old
            # accidental bias went unnoticed for so long.
            expected_return_unbiased=round(port_arith, 4),
            conservatism_haircut=round(haircut, 4),
            expected_return_after_haircut=round(port_arith - haircut, 4),
        )
