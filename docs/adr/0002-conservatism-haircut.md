# 0002 — Conservatism as a named input

**Status:** Accepted

## Context
Fixing a statistical bias and adding caution are different things. Mixing them hides how much of a projection is evidence and how much is prudence.

## Decision
Bias is corrected properly (shrinkage, estimation risk). Any extra caution is a separate, named `conservatism_haircut`, and responses report `expected_return_unbiased`, the haircut, and `expected_return_after_haircut` side by side.

## Consequences
Users and reviewers can see — and argue with — the prudence applied. The landing page's "ledger" beat shows exactly this chain.
