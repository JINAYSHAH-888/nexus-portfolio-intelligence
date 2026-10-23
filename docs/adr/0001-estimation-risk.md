# 0001 — Model estimation risk in ARISE

**Status:** Accepted

## Context
ARISE returned 100% success for portfolios built on little or synthetic data. Monte Carlo treated the estimated mean return as known, so a short lucky history produced certainty. A probability of 100% is never defensible for a 30-year plan.

## Decision
- Each path draws its expected return from N(μ̂, σ/√(n_real + τ)), τ = 10, truncated at ±3 sd. Synthetic years count as zero.
- Report success as the Jeffreys posterior mean of Beta(k + ½, n − k + ½) with a 95% interval.
- Lifespan is drawn once per path so strategies share common random numbers.
- The frontend's `formatProbability` never prints 0% or 100%.

## Consequences
Thin-data portfolios show visibly lower, wider estimates that tighten as real history accumulates. New response fields are additive. Covered by `backend/tests/test_estimation_risk.py`.
