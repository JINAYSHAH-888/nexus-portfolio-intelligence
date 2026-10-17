"""
What the investor's choice of plan actually costs, in rupees.

THE GAP THIS CLOSES
-------------------
Every scheme in this platform exists in two forms: a DIRECT plan bought from
the AMC, and a REGULAR plan bought through a distributor whose commission is
embedded in the expense ratio. Across the 35 schemes here the averages are
0.58% and 1.19% a year -- a gap of roughly 61 basis points that compounds for
as long as the investor holds.

Both numbers were already in schemes_metadata.json and nothing surfaced the
difference. A difference of 0.61% a year sounds negligible and is not: on a
Rs 25,000/month SIP over 25 years it is several lakh rupees, and unlike market
returns it is CERTAIN. It is the single most reliable lever an Indian mutual
fund investor has, and it requires no view on markets at all.

WHAT THIS IS NOT
----------------
Not advice to switch. Switching plans is a redemption and repurchase: it can
realise capital gains, may attract exit load, and resets holding-period clocks
for LTCG. A distributor may also be providing advice the investor values. This
function states the COST of the choice; whether it is worth paying is the
investor's call, and the output says so.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _future_value_of_sip(
    monthly_sip: float, annual_rate: float, years: int, step_up: float = 0.0
) -> float:
    """FV of an annually stepped-up SIP, compounded yearly."""
    fv = 0.0
    sip_annual = monthly_sip * 12.0
    for year in range(years):
        fv += sip_annual * ((1.0 + annual_rate) ** (years - year))
        sip_annual *= 1.0 + step_up
    return fv


def plan_cost_comparison(
    *,
    scheme_meta: list[dict],
    weights: dict[str, float],
    monthly_sip: float,
    years: int,
    gross_return: float,
    step_up: float = 0.0,
    lump_sum: float = 0.0,
) -> dict:
    """
    Weighted direct vs regular expense ratios, and what the gap costs over
    `years` of accumulation.

    `gross_return` is the pre-fee return. Both plans are projected from the
    SAME gross figure so the ONLY difference between them is the fee -- which
    is the entire point of the comparison.
    """
    direct_num = regular_num = weight_used = 0.0
    missing: list[str] = []

    for s in scheme_meta:
        sid = s.get("id")
        w = float(weights.get(sid, 0.0))
        if w <= 0:
            continue
        d, r = s.get("expense_ratio_direct"), s.get("expense_ratio_regular")
        if d is None or r is None:
            missing.append(sid)
            continue
        direct_num += w * float(d) / 100.0
        regular_num += w * float(r) / 100.0
        weight_used += w

    if weight_used <= 0:
        return {
            "available": False,
            "reason": "No expense-ratio data for the selected schemes.",
        }

    # Renormalise over the schemes that HAVE fee data, so a missing scheme
    # does not silently drag the weighted average toward zero.
    ter_direct = direct_num / weight_used
    ter_regular = regular_num / weight_used

    fv_direct = _future_value_of_sip(monthly_sip, gross_return - ter_direct, years, step_up)
    fv_regular = _future_value_of_sip(monthly_sip, gross_return - ter_regular, years, step_up)
    if lump_sum > 0:
        fv_direct += lump_sum * ((1.0 + gross_return - ter_direct) ** years)
        fv_regular += lump_sum * ((1.0 + gross_return - ter_regular) ** years)

    gap = fv_direct - fv_regular
    return {
        "available": True,
        "weighted_ter_direct": round(ter_direct, 5),
        "weighted_ter_regular": round(ter_regular, 5),
        "ter_gap_pp_per_year": round(100.0 * (ter_regular - ter_direct), 3),
        "projected_corpus_direct": round(fv_direct, 0),
        "projected_corpus_regular": round(fv_regular, 0),
        "cost_of_regular_plan": round(gap, 0),
        "cost_as_pct_of_corpus": round(100.0 * gap / fv_regular, 2) if fv_regular > 0 else 0.0,
        "years": years,
        "schemes_without_fee_data": missing,
        "caveats": [
            "Both projections use the SAME gross return, so the only difference "
            "between them is the fee. This is a fee comparison, not a forecast.",
            "Not advice to switch. Moving from regular to direct is a redemption and "
            "repurchase: it can realise capital gains, may attract exit load, and "
            "resets the holding-period clock for LTCG.",
            "A distributor may be providing advice that is worth paying for. This "
            "states the cost of that choice, not whether to make it.",
        ],
    }
