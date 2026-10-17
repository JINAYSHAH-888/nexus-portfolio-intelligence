# 0007 — Models ship only if they win out of sample

**Status:** Accepted

## Context
Adding machine learning to PLOS, Risk and ARISE invites the reflex choice: the most complex model, or the one with the best in-sample fit. In this project the complex choice lost several times. Gradient-boosted trees overfit market years in PLOS. Regime-switching models lost to a Student-t in ARISE. A gradient-boosted volatility model lost to a linear HAR-X.

## Decision
Every model is trained offline by `scripts/train_models.py` and compared against the model it would replace on held-out data, using one scoring rule per engine (see `docs/ml-models.md`). The comparison is written into the model's artifact and shown on the relevant screen. Where samples are small, effects are shrunk: valuation weight n/(n+τ), PLOS weights toward the hand prior. A model that loses stays out of the main result or is shown only as a labelled scenario.

## Consequences
Some requested "ML" ends up as evidence that a simpler model is better, and the product says so. Retraining reruns every comparison, so a future data refresh can overturn any of these choices.
