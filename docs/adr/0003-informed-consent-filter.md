# 0003 — Informed-consent filter

**Status:** Accepted

## Context
All 20 equity schemes are SEBI riskometer 6. A hard risk gate left conservative and moderate investors with no equity schemes at all.

## Decision
Risk and goal mismatches become advisory flags (`risk_flag`, `risk_flag_level`). Hard blocks remain only for objective or legal facts: age, income, amount limits, already held, and 80C eligibility for `tax_saving`.

## Consequences
Every scheme stays visible with a reason. `filter_summary.by_risk` is always 0 by design. Regression-guarded in `tests/test_profile_schemes.py`.
