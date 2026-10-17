"""
Is this fund actually managed, and is its fee proportionate to the activeness
the investor is paying for?

WHY THIS IS RETURN-BASED AND NOT HOLDINGS-BASED
-----------------------------------------------
The canonical measure is Cremers & Petajisto's Active Share, which needs the
BENCHMARK'S CONSTITUENT WEIGHTS. This platform has the schemes' holdings but
not index constituents, so true Active Share is not computable here.

That is not the compromise it first appears. ESMA's own 2020 working paper on
closet indexing moved to return-based detection -- "In addition to the
portfolio-based Active Share we use three return-based metrics: Tracking
Error, R2 and Beta" -- and France's AMF built a return-based method
specifically to avoid depending on portfolio holdings data. Cremers and
Petajisto themselves positioned Active Share as a COMPLEMENT to tracking
error, not a replacement for it.

So this module uses what the risk engine already computes against each
scheme's OWN declared benchmark: tracking error, R-squared and beta.

THE THRESHOLDS, AND WHY THEY ARE A SIGNAL AND NOT A VERDICT
-----------------------------------------------------------
ESMA's 2016 screens (as reported by EFAMA and Fideres) combined active share
with tracking error under 4%, or under 3% together with R-squared above 95%,
and required the condition to hold in at least 3 of 5 years. The return-based
half of that is reproduced here.

Those thresholds are contested, and this module says so rather than implying
precision it does not have:

  * EFAMA argues a fixed threshold "raises serious questions" -- small-cap
    funds and funds with diversified benchmarks naturally score differently,
    and published thresholds invite managers to game them.
  * Fideres gives a fund with 22% active share and a 7.81% tracking error as
    proof that any single metric misleads.
  * Morningstar notes the line only works when the benchmark is well
    diversified.
  * ESMA's own screens required persistence across years. This platform
    computes over a single trailing window, which is weaker evidence.

The output is therefore a flag with its reasoning attached, never a
pronouncement that a fund IS a closet indexer.

THE NUMBER THAT IS ACTUALLY NEW
-------------------------------
`cost_per_unit_active_risk` = expense ratio / tracking error, i.e. what the
investor pays per unit of the deviation they are paying to receive. A fund
charging 1.2% for 2% of tracking error is a fundamentally different purchase
from one charging 0.6% for 8%, and no single existing metric says so. Both
inputs are already present (Phase 1 wired real benchmarks and real expense
ratios), so this falls out of work already done.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Return-based half of ESMA's 2016 closet-indexing screens.
TE_STRICT = 0.03          # 3% tracking error, paired with the R^2 test
TE_LOOSE = 0.04           # 4% tracking error on its own
R2_HIGH = 0.95            # "moves almost exactly with the index"

# Above this, paying an active fee for near-index exposure is hard to justify.
COST_PER_ACTIVE_RISK_EXPENSIVE = 0.40

VERDICT_CLOSET = "possible_closet_index"
VERDICT_ACTIVE = "active"
VERDICT_UNDETERMINED = "undetermined"


def assess(
    *,
    tracking_error: float | None,
    r_squared: float | None,
    expense_ratio: float | None,
    benchmark_quality: str = "none",
) -> dict:
    """
    One scheme's activeness assessment.

    `benchmark_quality` comes from data_loaders.benchmark_registry. Anything
    other than a real or price-proxy benchmark makes the tracking error
    unreliable -- a fund measured against a merely SIMILAR index will show
    deviation that belongs to the benchmark mismatch, not to the manager --
    so the verdict is withheld rather than guessed.
    """
    out: dict = {
        "verdict": VERDICT_UNDETERMINED,
        "tracking_error": tracking_error,
        "r_squared": r_squared,
        "expense_ratio": expense_ratio,
        "cost_per_unit_active_risk": None,
        "reasons": [],
        "caveats": [],
    }

    if benchmark_quality not in ("exact", "price_proxy"):
        out["reasons"].append(
            "No reliable benchmark for this scheme, so tracking error cannot be "
            "attributed to the manager rather than to a benchmark mismatch."
        )
        return out

    if tracking_error is None or r_squared is None:
        out["reasons"].append("Benchmark-relative statistics unavailable.")
        return out

    # ── Cost per unit of active risk ───────────────────────────────────────
    if expense_ratio is not None and tracking_error > 1e-6:
        ratio = float(expense_ratio) / float(tracking_error)
        out["cost_per_unit_active_risk"] = round(ratio, 3)
        if ratio > COST_PER_ACTIVE_RISK_EXPENSIVE:
            out["reasons"].append(
                f"Pays {expense_ratio * 100:.2f}% a year for {tracking_error * 100:.1f}% "
                f"of active risk ({ratio:.2f} of fee per unit of deviation)."
            )

    # ── ESMA-style return-based screens ────────────────────────────────────
    closet = False
    if tracking_error < TE_STRICT and r_squared > R2_HIGH:
        closet = True
        out["reasons"].append(
            f"Tracking error {tracking_error * 100:.1f}% is below {TE_STRICT * 100:.0f}% "
            f"while R-squared {r_squared:.2f} exceeds {R2_HIGH:.2f}: the scheme has moved "
            f"almost exactly with its benchmark."
        )
    elif tracking_error < TE_LOOSE:
        closet = True
        out["reasons"].append(
            f"Tracking error {tracking_error * 100:.1f}% is below {TE_LOOSE * 100:.0f}%, "
            f"ESMA's looser screen for potential closet indexing."
        )

    out["verdict"] = VERDICT_CLOSET if closet else VERDICT_ACTIVE
    if not closet:
        out["reasons"].append(
            f"Tracking error {tracking_error * 100:.1f}% exceeds the screens, so the "
            f"scheme is deviating meaningfully from its benchmark."
        )

    out["caveats"] = [
        "A SIGNAL, not a verdict. ESMA's screens also used holdings-based Active "
        "Share, which needs index constituent weights this platform does not have.",
        "ESMA required the condition to hold in at least 3 of 5 years. This is a "
        "single trailing window, which is weaker evidence.",
        "Thresholds are contested: EFAMA notes fixed cut-offs penalise some fund "
        "types unfairly and invite managers to game them.",
    ]
    if benchmark_quality == "price_proxy":
        out["caveats"].append(
            "Benchmark is a PRICE index standing in for a total-return index, so "
            "tracking error here also absorbs the benchmark's dividend yield."
        )
    return out
