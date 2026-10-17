"""
Phase-3 analysis: overlap, PLOS, risk, ARISE.

Two structural decisions dominate this module.

1. EVERY ENGINE CALL IS OFFLOADED TO A WORKER THREAD.
   The engines are synchronous and genuinely CPU-heavy -- ARISE runs
   n_simulations x n_years x 4 strategies of Student-t paths; RiskEngine does
   a Ledoit-Wolf shrinkage plus a 10,000-path GBM Monte Carlo per scheme.
   Calling them directly from an async route would peg the event loop and
   stall every other request in the process (including health checks) for
   seconds. `anyio.to_thread.run_sync` hands them to a worker thread instead.

   This also makes `core.async_utils.run_async_safely` -- which the engines
   call internally to reach the async market-data loaders -- take its cheap
   path: the worker thread has no running event loop, so it can just
   `asyncio.run(...)` rather than bouncing onto the bridge thread.

2. ARISE'S INPUTS ARE DERIVED EXPLICITLY, AND ANY ESTIMATE IS LABELLED.
   The Deep Profile carries 10 fields; ARISE needs a few things the profile
   does not literally store (notably an ongoing SIP amount). Rather than
   quietly inventing a number, every derived input is computed in
   `_derive_arise_inputs`, returned alongside the result as `assumptions`,
   and overridable per-request. See that function for the reasoning on each.
"""
from __future__ import annotations

import functools
import logging
from dataclasses import asdict, is_dataclass
from typing import Any

import anyio

from backend.core.config import settings
from backend.core.errors import InsufficientDataError, ValidationError
from backend.models import InvestorProfile, SchemeSelection
from backend.services import engine_registry

logger = logging.getLogger(__name__)


def _to_dict(obj: Any) -> Any:
    """Dataclass -> dict, recursively; passthrough for everything else."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return obj


def _pair_to_list(pair: tuple[str, str] | None) -> list[str] | None:
    """
    The engines return `tuple[str, str] | None` for most/least-overlapping
    pairs, but the response schemas declare `list[str] | None`. Pydantic would
    coerce a tuple anyway, but being explicit keeps the OpenAPI schema honest.
    """
    return list(pair) if pair else None


def _effective_weights(selection: SchemeSelection) -> dict[str, float]:
    """
    Weights as FRACTIONS summing to 1.0 -- the contract
    PortfolioStatisticsBuilder.build() enforces (it raises if
    abs(sum - 1.0) > 0.01).

    A selection made via POST /schemes/select without explicit weights is
    stored equal-weighted, but we recompute defensively here: if a scheme was
    later removed, stored weights could be stale or fail to sum to 1.
    """
    ids = list(selection.selected_scheme_ids or [])
    if not ids:
        raise ValidationError("No schemes selected.")

    weights = dict(selection.weights or {})
    if set(weights) != set(ids) or abs(sum(weights.values()) - 1.0) > 0.01:
        equal = 1.0 / len(ids)
        weights = dict.fromkeys(ids, equal)
    return weights


# ── Overlap ───────────────────────────────────────────────────────────────────
async def compute_overlap(selection: SchemeSelection) -> dict[str, Any]:
    engine = engine_registry.get_overlap_engine()
    ids = list(selection.selected_scheme_ids)

    result = await anyio.to_thread.run_sync(engine.compute_overlap, ids)

    payload = _to_dict(result)
    payload["most_overlapping_pair"] = _pair_to_list(result.most_overlapping_pair)
    payload["least_overlapping_pair"] = _pair_to_list(result.least_overlapping_pair)
    return payload


# ── PLOS ──────────────────────────────────────────────────────────────────────
async def compute_plos(selection: SchemeSelection) -> dict[str, Any]:
    engine = engine_registry.get_plos_engine()
    ids = list(selection.selected_scheme_ids)

    weights = _effective_weights(selection)
    result = await anyio.to_thread.run_sync(lambda: engine.compute_plos(ids, weights))

    payload = _to_dict(result)
    payload["most_overlapping_pair"] = _pair_to_list(result.most_overlapping_pair)
    payload["least_overlapping_pair"] = _pair_to_list(result.least_overlapping_pair)
    return payload


# ── Risk ──────────────────────────────────────────────────────────────────────
async def compute_risk(selection: SchemeSelection) -> dict[str, Any]:
    """
    Risk analytics at the investor's ACTUAL allocation.

    This used to call `engine.compute_risk(ids)` with no weights, so the risk
    dashboard always described an equal-weight portfolio while ARISE -- two
    functions down, from the same SchemeSelection -- projected the real one.
    An investor holding 90/10 was shown the volatility, Sharpe and drawdown of
    a 50/50 they did not own. Measured on two real schemes, that reported an
    11.92% CAGR and 0.459 Sharpe for a portfolio actually returning 9.82% at
    0.329.

    `_effective_weights` is the same resolver ARISE uses, so both pages now
    answer about the same portfolio by construction.
    """
    engine = engine_registry.get_risk_engine()
    ids = list(selection.selected_scheme_ids)
    weights = _effective_weights(selection)

    result = await anyio.to_thread.run_sync(
        functools.partial(engine.compute_risk, ids, weights)
    )
    return _to_dict(result)


# ── ARISE ─────────────────────────────────────────────────────────────────────
# A documented, overridable estimate -- NOT a silent fabrication. The Deep
# Profile captures income, corpus, and the amount being invested now, but not
# an ongoing monthly SIP, which ARISE's accumulation phase needs. 20% of gross
# monthly income is used as the default when the client doesn't supply one.
# It is reported back in `assumptions` on every response, and any caller who
# knows the real figure should pass `monthly_sip` explicitly.
DEFAULT_SIP_RATE_OF_INCOME = 0.20
# The ARISERequest schema default. Used to detect 'the caller did not override
# step_up', so profile.sip_step_up_pct can take precedence when they didn't.
DEFAULT_STEP_UP = 0.08


def _derive_arise_inputs(
    profile: InvestorProfile,
    *,
    monthly_sip: int | None,
    step_up: float,
    life_expectancy: int,
) -> tuple[dict[str, Any], dict[str, Any], float]:
    """
    Map the Deep Profile onto run_monte_carlo()'s kwargs.

    Returns (inputs, assumptions, effective_step_up).

    SIP precedence, most-trusted first:
        1. `monthly_sip` supplied on the request      -> exact, client-supplied
        2. `profile.monthly_sip`                       -> exact, the investor told us
        3. 20% of gross monthly income                 -> ESTIMATE, labelled as such

    Tier 2 is the one that matters: the profile now HAS a SIP field, so a real
    deployment never has to run on tier 3. The estimate survives only as a
    backstop for legacy profiles created before the field existed, and it is
    always reported in `assumptions` so the investor can see it was guessed.
    """
    age = profile.age
    retire_age = profile.target_retirement_age

    if age is None or retire_age is None:
        raise ValidationError(
            "Profile is missing date_of_birth or target_retirement_age; "
            "ARISE cannot project a retirement horizon without both."
        )
    if retire_age <= age:
        raise ValidationError(
            f"target_retirement_age ({retire_age}) must exceed current age ({age})."
        )
    if life_expectancy <= retire_age:
        raise ValidationError(
            f"life_expectancy ({life_expectancy}) must exceed "
            f"target_retirement_age ({retire_age}); otherwise there is no "
            f"decumulation phase to simulate."
        )

    assumptions: dict[str, Any] = {}

    # The corpus ARISE compounds is the money actually going into the selected
    # schemes (`investment_amount`), not the investor's entire net worth
    # (`total_corpus`), which may sit in property, EPF, or cash this portfolio
    # doesn't model.
    current_portfolio_value = float(profile.investment_amount or 0)
    assumptions["current_portfolio_value_source"] = (
        "profile.investment_amount -- the amount being invested into the selected "
        "schemes, not profile.total_corpus (which includes assets this portfolio "
        "does not model)."
    )

    if monthly_sip is not None:
        sip = float(monthly_sip)
        assumptions["monthly_sip_source"] = "client-supplied (request)"
        assumptions["monthly_sip_is_estimated"] = False
    elif profile.monthly_sip is not None:
        sip = float(profile.monthly_sip)
        assumptions["monthly_sip_source"] = "profile.monthly_sip (investor-supplied)"
        assumptions["monthly_sip_is_estimated"] = False
    else:
        sip = float((profile.monthly_income or 0) * DEFAULT_SIP_RATE_OF_INCOME)
        assumptions["monthly_sip_source"] = (
            f"ESTIMATE: {DEFAULT_SIP_RATE_OF_INCOME:.0%} of gross monthly income "
            f"(Rs {profile.monthly_income:,}). This profile has no monthly_sip on "
            f"record -- set it via PUT /api/v1/profile, or pass `monthly_sip` in "
            f"this request, to project on a real figure instead of an estimate."
        )
        assumptions["monthly_sip_is_estimated"] = True

    assumptions["monthly_sip"] = sip

    # Step-up: the request wins; otherwise the investor's own recorded escalation.
    if profile.sip_step_up_pct is not None and step_up == DEFAULT_STEP_UP:
        effective_step_up = float(profile.sip_step_up_pct)
        assumptions["step_up_source"] = "profile.sip_step_up_pct"
    else:
        effective_step_up = step_up
        assumptions["step_up_source"] = "request (or default)"
    assumptions["step_up"] = effective_step_up

    # ARISE wants today's-money monthly spending in retirement, which is
    # precisely what this profile field is; it inflates it internally.
    current_expense_monthly = float(profile.monthly_expenses_in_retirement or 0)

    inputs = {
        "current_age": age,
        "retire_age": retire_age,
        "life_expectancy": life_expectancy,
        "current_portfolio_value": current_portfolio_value,
        "monthly_sip": sip,
        "current_expense_monthly": current_expense_monthly,
    }
    return inputs, assumptions, effective_step_up


def _run_arise_sync(
    *, scheme_ids: list[str], weights: dict[str, float], mc_kwargs: dict[str, Any]
) -> Any:
    """
    Sync body executed on a worker thread: build real portfolio statistics,
    then simulate.

    The ordering matters and is load-bearing. PortfolioStatisticsBuilder.build()
    is where the shrinkage/evidence-scaled cap is applied to portfolio_cagr and
    portfolio_volatility, and run_monte_carlo() anchors its Student-t AR(1)
    process on exactly those attributes. CLAUDE.md §10 flags the bug where a
    shrunk number was *reported* but the simulation consumed the raw one -- so
    the object ARISE receives must be the built one, unmodified.
    """
    from backend.engines.arise_engine import run_monte_carlo
    from backend.ml.arise_models import equity_prior_adjustment
    from backend.ml.data import load_artifact
    from backend.ml.regime import load_regime_stress_shocks

    builder = engine_registry.get_stats_builder()
    stats = builder.build(scheme_ids=scheme_ids, weights=weights)

    # Valuation-aware prior: only the PRIOR part of the equity sleeve's mean
    # moves (the sample part is the funds' own history), amortised over the
    # projection. See backend/ml/arise_models.py for the evidence.
    cal = load_artifact("arise_calibration.json")
    val_adj = 0.0
    if cal:
        equity_w = sum(
            wt for sid, wt in stats.weights.items()
            if builder._schemes.get(sid, {}).get("instrument_type") != "debt"
        )
        horizon = mc_kwargs["life_expectancy"] - mc_kwargs["current_age"]
        val_adj = equity_w * (1 - stats.sample_weight_cagr) * equity_prior_adjustment(cal["valuation"], horizon)
    mc_kwargs = {**mc_kwargs, "valuation_adjustment": val_adj}
    result = run_monte_carlo(portfolio_stats=stats, **mc_kwargs)

    # Regime stress scenario: same investor, same return level, same seed --
    # only the shock SHAPE changes to the learned regime model, in which crash
    # years cluster as they did in 2000-03 and 2008. Reported beside the main
    # estimate, never instead of it (the main model won the out-of-sample test).
    stress = None
    shocks, art = load_regime_stress_shocks()
    if shocks is not None:
        s = run_monte_carlo(portfolio_stats=stats, **{**mc_kwargs, "leaderboard": False}, shock_fn=shocks)
        stress = {
            "success_estimate": s.success_rate_estimate,
            "success_low": s.success_rate_low,
            "success_high": s.success_rate_high,
            "terminal_p10": s.terminal_p10,
            "terminal_p50": s.terminal_p50,
            "model": art["model"],
            "regimes": art["regimes"],
            "validation": art["validation"],
            "shrinkage_backtest": art.get("shrinkage_backtest"),
        }
    return result, stats, stress, cal


async def run_arise(
    profile: InvestorProfile,
    selection: SchemeSelection,
    *,
    n_simulations: int,
    life_expectancy: int,
    slab_rate: float,
    strategy: str,
    step_up: float,
    monthly_sip: int | None = None,
    spending_model: str = "constant_real",
    glide_mode: str = "declining",
    cape: float | None = None,
) -> dict[str, Any]:
    scheme_ids = list(selection.selected_scheme_ids)
    weights = _effective_weights(selection)

    inputs, assumptions, effective_step_up = _derive_arise_inputs(
        profile,
        monthly_sip=monthly_sip,
        step_up=step_up,
        life_expectancy=life_expectancy,
    )

    n_simulations = min(n_simulations, settings.ARISE_MAX_SIMULATIONS)

    mc_kwargs: dict[str, Any] = {
        **inputs,
        "slab_rate": slab_rate,
        "strategy": strategy,
        "step_up": effective_step_up,
        "n_simulations": n_simulations,
        "seed": settings.ARISE_SEED,
        "spending_model": spending_model,
        "glide_mode": glide_mode,
        "cape": cape,
        "leaderboard": True,
    }

    try:
        result, stats, stress, cal = await anyio.to_thread.run_sync(
            lambda: _run_arise_sync(
                scheme_ids=scheme_ids, weights=weights, mc_kwargs=mc_kwargs
            )
        )
    except ValueError as e:
        # PortfolioStatisticsBuilder raises ValueError when no fallback tier
        # could produce usable returns for ANY selected scheme. That is an
        # explainable data state, not a crash -- surface it as a 422 carrying
        # the engine's own reason.
        raise InsufficientDataError(str(e)) from e

    payload = _to_dict(result)
    payload["regime_stress"] = stress
    if cal:
        payload["valuation_context"] = {
            "dividend_yield": cal["valuation"]["current_dividend_yield"],
            "forecast_5y": cal["valuation"]["forecast_5y"],
            "hist_mean": cal["valuation"]["model"]["hist_mean"],
            "evidence_weight": cal["valuation"]["evidence_weight"],
            "oos": cal["valuation"]["oos"],
        }
        payload["calibration"] = cal["calibration"]
        payload["volatility_raw"] = stats.portfolio_volatility_raw
        payload["volatility_prior"] = stats.volatility_prior

    # ── What the plan choice costs ────────────────────────────────────────
    # Direct vs regular is the one lever here that requires no view on markets
    # and whose payoff is certain. Both expense ratios were already in the
    # scheme metadata and nothing surfaced the difference.
    try:
        import json as _json

        from backend.analytics.plan_costs import plan_cost_comparison
        from backend.core.config import settings as _settings

        with open(_settings.SCHEMES_PATH, encoding="utf-8") as _f:
            _rows = _json.load(_f)
        _rows = _rows if isinstance(_rows, list) else list(_rows.values())
        payload["plan_cost_comparison"] = plan_cost_comparison(
            scheme_meta=_rows,
            weights=weights,
            monthly_sip=float(inputs.get("monthly_sip", 0.0)),
            years=max(0, int(result.retire_age) - int(result.current_age)),
            # The portfolio's own projected return, BEFORE the fee -- so the
            # only thing separating the two projections is the expense ratio.
            gross_return=float(stats.portfolio_cagr),
            step_up=effective_step_up,
            lump_sum=float(inputs.get("current_portfolio_value", 0.0) or 0.0),
        )
    except Exception as e:  # pragma: no cover - enrichment must never 500 a run
        logger.warning("plan cost comparison unavailable: %s", e)

    # Provenance the investor is entitled to see: what the simulation actually
    # consumed, including whether the shrinkage cap bound.
    payload["assumptions"] = assumptions
    payload["shrinkage_diagnostics"] = {
        "portfolio_cagr_raw": stats.portfolio_cagr_raw,
        # What the simulation consumed: shrunk CAGR plus the (reported)
        # valuation adjustment -- never a number the paths did not use.
        "portfolio_cagr_used": result.portfolio_cagr_used,
        "valuation_adjustment": result.valuation_adjustment,
        "prior_cagr_used": stats.prior_cagr_used,
        "sample_weight_cagr": stats.sample_weight_cagr,
        "return_capped": stats.return_capped,
    }
    return payload


# ── Prices for selected holdings ──────────────────────────────────────────────
async def get_prices_for_selection(selection: SchemeSelection, limit: int = 25) -> dict[str, Any]:
    """
    Delayed NSE quotes for the top holdings across the selected schemes.

    Debt schemes hold bonds, not listed equities, so they contribute no
    tickers -- an all-debt selection legitimately returns an empty quote list
    rather than an error.
    """
    from backend.data_loaders.live_price_loader import is_market_open

    overlap = engine_registry.get_overlap_engine()
    loader = engine_registry.get_market_loader()
    live = engine_registry.get_live_price_loader()

    # Aggregate holdings weight across the selection, then take the largest.
    agg: dict[str, float] = {}
    for sid in selection.selected_scheme_ids:
        for h in overlap._holdings.get(sid, []):  # noqa: SLF001 - read-only, loaded from JSON
            name = h.get("name")
            if name:
                agg[name] = agg.get(name, 0.0) + float(h.get("weight") or 0.0)

    ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)

    ticker_to_name: dict[str, str] = {}
    for name, _w in ranked:
        ticker = loader.stock_name_to_ticker(name)
        if ticker and ticker not in ticker_to_name:
            ticker_to_name[ticker] = name
        if len(ticker_to_name) >= limit:
            break

    quotes: dict[str, Any] = {}
    if ticker_to_name:
        quotes = await live.get_bulk_quotes(list(ticker_to_name))

    prices = [
        {
            "ticker": t,
            "stock_name": ticker_to_name[t],
            "portfolio_weight": round(agg[ticker_to_name[t]], 4),
            "ltp": q.ltp,
            "change": q.change,
            "change_pct": q.change_pct,
            "volume": q.volume,
            "timestamp": q.timestamp,
            "is_live": q.is_live,
        }
        for t, q in quotes.items()
    ]
    prices.sort(key=lambda p: p["portfolio_weight"], reverse=True)

    return {
        "scheme_ids": list(selection.selected_scheme_ids),
        "market_open": is_market_open(),
        # yfinance is delayed, never a true tick -- LiveQuote.is_live is
        # hardcoded False upstream. Say so rather than implying real-time data.
        "data_delayed": True,
        "requested_tickers": len(ticker_to_name),
        "returned_quotes": len(prices),
        "prices": prices,
    }


# ── Compare ("what if my portfolio were this instead?") ────────────────────────
def _validate_candidate_scheme_ids(scheme_ids: list[str]) -> None:
    """Same unknown-ID check /schemes/select applies, reused here so a
    hypothetical comparison can't silently compute overlap/PLOS against a
    scheme that doesn't exist."""
    engine = engine_registry.get_filter_engine()
    unknown = [sid for sid in scheme_ids if engine.get_scheme_by_id(sid) is None]
    if unknown:
        raise ValidationError(
            f"Unknown scheme ID(s): {', '.join(unknown)}.",
            details={"unknown_scheme_ids": unknown},
        )


def _build_ephemeral_selection(
    scheme_ids: list[str], weights: dict[str, float] | None
) -> SchemeSelection:
    """
    An UNPERSISTED SchemeSelection -- never added to a session, never
    committed. compute_overlap()/compute_plos() only ever read
    `.selected_scheme_ids` / `.weights` off the object; they have no idea
    (and no way to tell) whether it came from the database or was built in
    memory for a hypothetical. This is what lets /analysis/compare reuse the
    exact same, already-tested computation path as the real /analysis/overlap
    and /analysis/plos endpoints, with zero duplicated logic.
    """
    if weights is None:
        equal = 1.0 / len(scheme_ids)
        weights = dict.fromkeys(scheme_ids, equal)
    return SchemeSelection(selected_scheme_ids=list(scheme_ids), weights=dict(weights))


async def _comparison_block(selection: SchemeSelection) -> dict[str, Any]:
    overlap = await compute_overlap(selection)
    plos = await compute_plos(selection)
    return {
        "scheme_ids": list(selection.selected_scheme_ids),
        "weights": dict(selection.weights),
        "overlap": overlap,
        "plos": plos,
    }


async def compare_selection(
    *,
    candidate_scheme_ids: list[str],
    candidate_weights: dict[str, float] | None,
    current_selection: SchemeSelection | None,
) -> dict[str, Any]:
    """
    Computes overlap+PLOS for a hypothetical scheme combination, and -- when
    the investor already has a real saved selection -- for that too, so the
    frontend can render a direct before/after comparison in one response.

    Never touches the database: `current_selection`, if provided, is read
    only; the candidate is never persisted unless the investor separately
    calls POST /schemes/select to actually commit to it.
    """
    _validate_candidate_scheme_ids(candidate_scheme_ids)
    if candidate_weights is not None:
        covers = set(candidate_weights) == set(candidate_scheme_ids)
        if not covers:
            raise ValidationError(
                "weights must cover exactly the schemes in scheme_ids.",
            )
        total = sum(candidate_weights.values())
        if not (0.995 <= total <= 1.005):
            raise ValidationError(f"weights must sum to 1.0, got {total:.4f}.")

    candidate = _build_ephemeral_selection(candidate_scheme_ids, candidate_weights)
    result: dict[str, Any] = {"candidate": await _comparison_block(candidate)}

    if current_selection is not None and current_selection.selected_scheme_ids:
        result["current"] = await _comparison_block(current_selection)
    else:
        result["current"] = None

    return result
