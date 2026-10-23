# 0006 — Generated, verified landing data and tokens

**Status:** Accepted

## Context
An earlier landing page quoted three different Monte Carlo path counts and hardcoded figures that drifted from the engine.

## Decision
Every figure on the landing page comes from `scripts/build-overlap-dataset.mjs`, which applies the engine's exact normalisation and asserts 10 known results. Design tokens come from `scripts/build-tokens.mjs`, gated on 16 WCAG checks. CI regenerates both and fails on any diff.

## Consequences
Copy cannot silently disagree with the backend. Changing data means re-running `make data tokens` and committing the output.
