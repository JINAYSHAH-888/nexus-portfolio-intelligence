"""
Risk metric functions computed directly from real historical return series.

Every function here takes actual price/return data and returns a statistic
— no market assumptions, no hardcoded expected returns. These are the
building blocks DuckDB / the analytics layer uses to describe the investor's
ACTUAL selected schemes, never a hypothetical market.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)

TRADING_DAYS = 252


def cagr(returns: pd.Series) -> float:
    """
    Compound Annual Growth Rate from a daily log-return series.
    CAGR = (end_value / start_value) ^ (1 / years) - 1
    """
    if returns.empty:
        return 0.0
    total_log_return = returns.sum()
    n_years = len(returns) / TRADING_DAYS
    if n_years <= 0:
        return 0.0
    total_return = math.exp(total_log_return) - 1
    return float((1 + total_return) ** (1 / n_years) - 1)


def annualized_volatility(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    return float(returns.std() * math.sqrt(TRADING_DAYS))


def max_drawdown(returns: pd.Series) -> float:
    """Maximum peak-to-trough decline from a daily log-return series."""
    if returns.empty:
        return 0.0
    cumulative = np.exp(returns.cumsum())
    peak = np.maximum.accumulate(cumulative)
    drawdowns = (cumulative - peak) / np.where(peak > 0, peak, 1)
    return float(drawdowns.min())


def downside_deviation(returns: pd.Series, target: float = 0.0) -> float:
    """
    Annualized downside deviation — like volatility, but only counts
    returns below the target (0 by default). Used for Sortino ratio.
    """
    if returns.empty:
        return 0.0
    downside = returns[returns < target]
    if downside.empty:
        return 0.0
    return float(downside.std() * math.sqrt(TRADING_DAYS))


def sharpe_ratio(returns: pd.Series, risk_free_rate: float) -> float:
    """
    Sharpe ratio using the ACTUAL historical CAGR and volatility of the
    given return series against a supplied risk-free rate (see
    get_risk_free_rate_proxy in portfolio_statistics.py for how that rate
    is itself derived from real data rather than hardcoded).

    Uses an epsilon threshold (not exact zero) to guard against dividing
    by floating-point noise on near-constant return series (e.g. an
    overnight/liquid fund with tiny but non-exactly-zero daily variance),
    which would otherwise produce an absurd, misleading ratio.
    """
    vol = annualized_volatility(returns)
    if vol < 1e-8:
        return 0.0
    return float((cagr(returns) - risk_free_rate) / vol)


def sortino_ratio(returns: pd.Series, risk_free_rate: float) -> float:
    """Like Sharpe, but penalizes only downside volatility. Same epsilon
    guard as sharpe_ratio against near-zero denominators."""
    dd = downside_deviation(returns)
    if dd < 1e-8:
        return 0.0
    return float((cagr(returns) - risk_free_rate) / dd)


def rolling_cagr(returns: pd.Series, window_years: float = 3.0) -> pd.Series:
    """Rolling CAGR over a trailing window (in years) of daily returns."""
    window_days = int(window_years * TRADING_DAYS)
    if len(returns) < window_days:
        return pd.Series(dtype=float)

    def _window_cagr(x: np.ndarray) -> float:
        total_return = np.exp(x.sum()) - 1
        return (1 + total_return) ** (1 / window_years) - 1

    return returns.rolling(window_days).apply(_window_cagr, raw=True)


def annual_returns_from_daily(
    returns: pd.Series, min_trading_days_full_year: int = 200
) -> pd.Series:
    """
    Aggregate a daily log-return series into calendar-year total returns.
    This is the REAL historical annual return sequence used for block
    bootstrap resampling in the Monte Carlo engine — not a synthetic draw.

    BUGFIX: a scheme's price history rarely starts on Jan 1 or ends on
    Dec 31, so the first and/or last calendar-year group is often a stub
    (e.g. 2 months of trading days). Summing that stub's daily log-returns
    and reporting it as if it were a full annual return understates or
    overstates that year's true magnitude and — worse — hands the block
    bootstrap a fake "year" that isn't comparable to the real full-year
    observations. This is especially distorting for newly-listed schemes
    with only 1-2 years of history, where a stub can be 1 of only 2-3
    data points the whole simulation is built from. We drop any calendar
    year with fewer than `min_trading_days_full_year` (~80% of a normal
    ~252 trading-day year) observed trading days. If that leaves nothing
    (e.g. sub-year history), we fall back to the unfiltered series rather
    than returning empty — callers should already be checking
    n_years_history / MIN_YEARS_HISTORY_RELIABLE for that case.
    """
    if returns.empty:
        return pd.Series(dtype=float)
    df = returns.to_frame("r")
    df.index = pd.to_datetime(df.index)
    day_counts = df.groupby(df.index.year)["r"].count()
    yearly_log = df.groupby(df.index.year)["r"].sum()
    yearly_total_return = np.exp(yearly_log) - 1

    full_years = day_counts[day_counts >= min_trading_days_full_year].index
    filtered = yearly_total_return.loc[yearly_total_return.index.isin(full_years)]
    if filtered.empty:
        logger.warning(
            "annual_returns_from_daily: no calendar year had >= %d trading "
            "days (stub-only history); returning unfiltered partial-year "
            "returns as a fallback — treat downstream results as low "
            "confidence.",
            min_trading_days_full_year,
        )
        return yearly_total_return
    return filtered


def tracking_error(portfolio_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    """Annualized standard deviation of (portfolio - benchmark) daily returns."""
    aligned = pd.DataFrame({"p": portfolio_returns, "b": benchmark_returns}).dropna()
    if aligned.empty:
        return 0.0
    diff = aligned["p"] - aligned["b"]
    return float(diff.std() * math.sqrt(TRADING_DAYS))


def beta_alpha(
    portfolio_returns: pd.Series, benchmark_returns: pd.Series, risk_free_rate: float
) -> tuple[float | None, float | None]:
    """
    CAPM beta and (annualized) alpha of the portfolio versus a benchmark,
    both derived from real aligned daily return series.
    """
    aligned = pd.DataFrame({"p": portfolio_returns, "b": benchmark_returns}).dropna()
    if len(aligned) < 60:
        return None, None
    cov = np.cov(aligned["p"], aligned["b"])
    if cov[1, 1] == 0:
        return None, None
    beta = float(cov[0, 1] / cov[1, 1])
    port_cagr = cagr(aligned["p"])
    bench_cagr = cagr(aligned["b"])
    alpha = float(port_cagr - (risk_free_rate + beta * (bench_cagr - risk_free_rate)))
    return beta, alpha


# ══════════════════════════════════════════════════════════════════════════════
#  Institutional risk reporting
#
#  Everything below is computed from the SAME aligned daily return series the
#  functions above use -- no new data source, no new assumption. They exist
#  because volatility and Sharpe alone describe a portfolio badly: they punish
#  upside and downside identically, say nothing about how long a loss lasts,
#  and (for Sharpe) assume a normal distribution that Indian equity returns
#  demonstrably do not have.
# ══════════════════════════════════════════════════════════════════════════════
def calmar_ratio(returns: pd.Series) -> float:
    """
    CAGR divided by the magnitude of max drawdown.

    Answers the question volatility cannot: "what did I earn per unit of the
    worst loss I actually had to sit through?" An investor abandons a plan at
    the drawdown, not at the standard deviation.
    """
    if returns.empty:
        return 0.0
    mdd = abs(max_drawdown(returns))
    if mdd < 1e-8:
        return 0.0
    return float(cagr(returns) / mdd)


def ulcer_index(returns: pd.Series) -> float:
    """
    RMS of the drawdown path, not just its worst point.

    Two funds can share a -30% max drawdown where one recovered in a quarter
    and the other stayed under water for three years. Max drawdown scores them
    identically; the Ulcer Index does not, because it integrates depth over
    time.
    """
    if returns.empty:
        return 0.0
    equity = np.exp(returns.cumsum())
    peak = np.maximum.accumulate(equity)
    dd_pct = (equity - peak) / np.where(peak > 0, peak, 1) * 100.0
    return float(np.sqrt(np.mean(dd_pct ** 2)))


def information_ratio(portfolio_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    """Active return per unit of tracking error -- the standard measure of
    whether active management earned its deviation from the benchmark."""
    te = tracking_error(portfolio_returns, benchmark_returns)
    if te < 1e-8:
        return 0.0
    aligned = pd.DataFrame({"p": portfolio_returns, "b": benchmark_returns}).dropna()
    if aligned.empty:
        return 0.0
    return float((cagr(aligned["p"]) - cagr(aligned["b"])) / te)


def up_down_capture(
    portfolio_returns: pd.Series, benchmark_returns: pd.Series
) -> tuple[float | None, float | None]:
    """
    (up_capture, down_capture) as RATIOS, where 1.0 = matched the benchmark.

    The pair most Indian factsheets quote, and the one that separates two
    funds with an identical beta: capturing 95% of rallies while taking only
    80% of falls is a completely different product from 110%/115%, even
    though a single beta can describe both.

    Computed as the ratio of MEAN returns over the benchmark's up and down
    days, not by compounding each leg.

    WHY NOT COMPOUNDED. Morningstar's convention compounds each leg, but it
    does so on MONTHLY data. Applied to a multi-year DAILY series the same
    formula saturates: a fund taking exactly half of each down day's loss,
    compounded over 600 down days, reports a down-capture of 0.81 rather than
    0.50, because exp(0.5*S)-1 over exp(S)-1 tends to 1 as the cumulative S
    grows large and negative. The ratio would then measure the length of the
    sample more than the behaviour of the fund.

    The mean-ratio form is scale-stable, returns exactly 0.50 for that fund
    at any sample length, and is a recognised variant of the same statistic.
    Stated here because it differs from the compounded convention a reader
    may be expecting.
    """
    aligned = pd.DataFrame({"p": portfolio_returns, "b": benchmark_returns}).dropna()
    if aligned.empty:
        return None, None

    def _capture(mask: pd.Series) -> float | None:
        sub = aligned[mask]
        if len(sub) < 20:            # too few periods to mean anything
            return None
        bench = float(sub["b"].mean())
        if abs(bench) < 1e-12:
            return None
        return float(float(sub["p"].mean()) / bench)

    return _capture(aligned["b"] > 0), _capture(aligned["b"] < 0)


def cornish_fisher_var(returns: pd.Series | np.ndarray, confidence: float) -> float:
    """
    VaR adjusted for the skew and excess kurtosis actually present in the
    sample, via the Cornish-Fisher expansion of the Gaussian quantile.

    Gaussian VaR assumes returns are normal. Indian equity returns are
    negatively skewed and fat-tailed, so Gaussian VaR understates the loss
    exactly where it matters -- the left tail it exists to measure. This
    keeps the closed-form speed of the parametric estimate while letting the
    sample's own third and fourth moments move the quantile.

    `confidence` is the tail probability (0.05 for 95% VaR). Returns a daily
    return, negative for a loss.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return 0.0
    mu, sigma = float(r.mean()), float(r.std())
    if sigma < 1e-12:
        return float(mu)

    s = float(stats.skew(r))
    k = float(stats.kurtosis(r))          # already EXCESS kurtosis
    z = float(stats.norm.ppf(confidence))
    z_cf = (
        z
        + (z**2 - 1) * s / 6.0
        + (z**3 - 3 * z) * k / 24.0
        - (2 * z**3 - 5 * z) * s**2 / 36.0
    )
    return float(mu + sigma * z_cf)


def historical_var_cvar(
    returns: pd.Series | np.ndarray, confidence: float
) -> tuple[float, float]:
    """
    Non-parametric VaR and CVaR, with the VaR quantile INTERPOLATED.

    The naive `sorted[int(confidence * n)]` form jumps discretely as one
    observation enters or leaves the window, so a VaR can move materially on
    a day the market barely did. CVaR here is the mean of everything at or
    beyond the VaR -- including the VaR observation itself, which the naive
    slice drops.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return 0.0, 0.0
    var = float(np.quantile(r, confidence, method="linear"))
    tail = r[r <= var]
    cvar = float(tail.mean()) if tail.size else var
    return var, cvar


def kupiec_pof_test(
    returns: pd.Series | np.ndarray, var_estimate: float, confidence: float
) -> dict[str, float | int | bool | None]:
    """
    Kupiec proportion-of-failures test: does this VaR actually hold up?

    A 95% VaR claims losses exceed it on ~5% of days. If a model's 95% VaR is
    breached 14% of the time it is not a conservative model, it is a wrong
    one -- and nothing else in a risk report will reveal that, because every
    other number is computed from the same assumption that produced the VaR.
    This is the only statistic here that tests the report rather than the
    portfolio.

    LR_POF = -2 ln[ (1-p)^(T-N) p^N / ((1-N/T)^(T-N) (N/T)^N ) ] ~ chi2(1)

    `passed` is True when the breach rate is statistically indistinguishable
    from the promised rate at 95% confidence. None where the sample is too
    short for the asymptotic chi-square to mean anything.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    t = int(r.size)
    n = int((r < var_estimate).sum())
    observed = (n / t) if t else 0.0

    out: dict[str, float | int | bool | None] = {
        "observations": t,
        "breaches": n,
        "expected_breaches": round(confidence * t, 1),
        "observed_rate": round(observed, 4),
        "expected_rate": confidence,
        "lr_statistic": None,
        "p_value": None,
        "passed": None,
    }
    # Both degenerate cases (no breaches, all breaches) make the likelihood
    # ratio undefined rather than extreme; say so instead of emitting inf.
    if t < 100 or n == 0 or n == t:
        return out

    p = confidence
    ll_null = (t - n) * math.log(1 - p) + n * math.log(p)
    ll_alt = (t - n) * math.log(1 - observed) + n * math.log(observed)
    lr = -2.0 * (ll_null - ll_alt)
    p_value = float(1.0 - stats.chi2.cdf(lr, df=1))

    out["lr_statistic"] = round(float(lr), 4)
    out["p_value"] = round(p_value, 4)
    out["passed"] = bool(p_value > 0.05)
    return out


def rolling_volatility(returns: pd.Series, window_years: float = 1.0) -> pd.Series:
    """Trailing annualized volatility, for judging whether a single
    full-sample number is describing a stable process or averaging over two
    different regimes."""
    window = int(window_years * TRADING_DAYS)
    if len(returns) < window or window < 2:
        return pd.Series(dtype=float)
    return returns.rolling(window).std() * math.sqrt(TRADING_DAYS)


def stability_of_metrics(returns: pd.Series, window_years: float = 1.0) -> dict[str, float]:
    """
    How much the headline numbers MOVE across rolling windows.

    A 14% volatility computed once over three years is a point estimate
    presented as a fact. If the rolling 1-year volatility ranged 9%-26% over
    those same three years, the point estimate is an average of regimes the
    investor never actually experienced, and the spread belongs in the report
    next to it.
    """
    rv = rolling_volatility(returns, window_years).dropna()
    rc = rolling_cagr(returns, window_years).dropna()
    out: dict[str, float] = {}
    if not rv.empty:
        out["vol_min"] = round(float(rv.min()), 4)
        out["vol_max"] = round(float(rv.max()), 4)
        out["vol_spread"] = round(float(rv.max() - rv.min()), 4)
    if not rc.empty:
        out["cagr_min"] = round(float(rc.min()), 4)
        out["cagr_max"] = round(float(rc.max()), 4)
        out["worst_rolling_year"] = round(float(rc.min()), 4)
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Return-space conversions
#
#  THE BUG CLASS THESE EXIST TO END. Three different quantities in this
#  codebase were all called "the return":
#
#    log drift      m  = mean(log(1+r)) * 252      -- continuously compounded
#    geometric mean g  = exp(m) - 1                -- the CAGR actually realised
#    arithmetic mean a = exp(m + s^2/2) - 1        -- the mean of ONE year's draw
#
#  They differ by roughly sigma^2/2 each, which is 1-3 percentage points a year
#  for Indian equity. Mixing them silently produced TWO opposite errors here:
#
#    * risk_engine fed a LOG drift to a GBM that subtracts sigma^2/2 again,
#      double-counting the Ito correction and making every forward percentile
#      band too pessimistic;
#    * arise_engine fed a GEOMETRIC CAGR to a process that adds its own
#      volatility drag on top, so paths realised ~1.4pp/yr BELOW the CAGR the
#      engine was explicitly anchored to.
#
#  Both are the error Kitces and Boldin describe for retirement Monte Carlo
#  (Boldin migrated its engine from CAGR to an arithmetic input for exactly
#  this reason; Voyant calls it a "double-correction"). Converting through
#  these named functions makes the units explicit at every call site.
# ══════════════════════════════════════════════════════════════════════════════
def geometric_to_arithmetic(geometric_mean: float, volatility: float) -> float:
    """
    Compound (CAGR) -> the arithmetic mean to DRAW from in a simulation.

    Exact under a lognormal parameterisation:
        1 + a = sqrt((1 + g)^2 + sigma^2)

    preferred over the familiar first-order `a ~= g + sigma^2/2` because that
    approximation drifts at the volatilities Indian small-cap funds actually
    run at (sigma > 0.25), which is precisely where the correction matters.

    A simulator that adds its own volatility must be fed THIS, not the CAGR;
    otherwise the drag is applied twice and the paths realise less than the
    CAGR the model was anchored to.
    """
    if volatility <= 0:
        return float(geometric_mean)
    return float(math.sqrt((1.0 + geometric_mean) ** 2 + volatility**2) - 1.0)


def arithmetic_to_geometric(arithmetic_mean: float, volatility: float) -> float:
    """Inverse of `geometric_to_arithmetic`: the compound rate a given
    one-year arithmetic mean actually delivers once volatility drags on it."""
    if volatility <= 0:
        return float(arithmetic_mean)
    inner = (1.0 + arithmetic_mean) ** 2 - volatility**2
    if inner <= 0:
        return -1.0
    return float(math.sqrt(inner) - 1.0)


def log_drift_from_arithmetic(arithmetic_mean: float, volatility: float) -> float:
    """
    Arithmetic annual mean -> the `m` of a GBM, i.e. the drift to put on
    log returns. This already CONTAINS the -sigma^2/2 Ito term, so a caller
    using it must not subtract one again.
    """
    g = arithmetic_to_geometric(arithmetic_mean, volatility)
    return float(math.log(max(1e-9, 1.0 + g)))


def annualized_arithmetic_return(returns: pd.Series | np.ndarray) -> float:
    """
    The arithmetic annual mean implied by a daily LOG return series.

    `cagr()` above gives the geometric rate; this gives the higher arithmetic
    one. Reporting the two under one name is what made them interchangeable
    in the first place.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return 0.0
    m = float(r.mean()) * TRADING_DAYS
    s = float(r.std()) * math.sqrt(TRADING_DAYS)
    return float(math.exp(m + 0.5 * s**2) - 1.0)
