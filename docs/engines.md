# Analytics engines

All engines live in `backend/backend/engines/` and are pure Python with no DB access.

## FilterEngine — `filter_engine.py`

Decides which of the 35 schemes suit a profile. **Risk and goal mismatches are advisory flags, not hard blocks**: every equity scheme carries SEBI riskometer 6 ("Very High"), so a hard gate would show conservative investors zero equity funds. Each scheme returns `risk_flag` and `risk_flag_level` (`match` | `mild` | `strong`); equity schemes also get a `subtier` from real factsheet standard deviation (Core ≤16%, Growth 16–18%, Aggressive >18%).

Hard blocks remain only for objective facts: age limits, minimum income, investment-amount limits, already-held schemes, and `tax_saving` goals on non-ELSS schemes (80C eligibility).

## OverlapEngine — `overlap_engine.py`

Holding names are normalised with `strip().rstrip("*").lower()`; "equity less than 1% of corpus" and "cash and other receivables" are excluded.

- **Jaccard** = |A ∩ B| / |A ∪ B| — shared *names*.
- **Weighted overlap** = Σ min(wₐ, w_b) over shared stocks — shared *money*.

Weighted overlap is the one that matters: Large Cap and Quant share only 15 names (Jaccard 0.283) but 41.07% of their weight. Outputs an N×N heatmap, a thresholded edge list for the network graph, and per-scheme summaries.

## PLOS — `plos_engine.py`

The Portfolio-Level Overlap Score: ten layers (holding, sector, factor and concentration overlaps among them), each contribution reported separately so the score is never a black box. Also produces a top-5 recommendation.

## RiskEngine — `risk_engine.py`

Ledoit–Wolf shrunk covariance; Sharpe, Sortino, Calmar; VaR and CVaR (historical, parametric and Cornish–Fisher); Kupiec POF backtest with a published pass/fail; beta; an HRP reference allocation; GBM Monte Carlo (1,000 paths).

## ARISE v4 — `arise_engine.py`

Retirement Monte Carlo.

- **Returns**: Student-t (df = 5) AR(1) mean-reverting process anchored on portfolio statistics that are already shrunk toward an asset-class prior and capped by evidence (`analytics/portfolio_statistics.py`).
- **Lifespan**: Gompertz draw, once per path (common random numbers across strategies).
- **Spending**: India-calibrated Blanchett smile (healthcare inflation gives a real late-life rise).
- **Strategies**: robotic, guardrails, VPW, Kitces ratchet — compared on a leaderboard with income volatility.
- **Tax**: LTCG ₹1,25,000 annual exemption, 12.5% above it; two-bucket liquidity sequencing.
- **Cross-check**: Milevsky reciprocal-gamma closed form (`milevsky_ruin_prob`) — a diagnostic, deliberately independent; it diverges when there is a long accumulation phase.

### Estimation risk

A probability of 100% claims certainty no model has. ARISE accounts for not knowing the true expected return:

1. Each path draws its own mean return with sd = σ / √(years_of_real_data + τ), τ = 10. Synthetic data counts as zero years, so thin evidence widens the spread. Draws are truncated at ±3 sd.
2. Success is reported as a **Jeffreys estimate**, Beta(k + ½, n − k + ½), with a 95% interval — it can approach but never reach 0 or 1.
3. The UI formats with `formatProbability`, which never prints 100% or 0%.

See [ADR 0001](adr/0001-estimation-risk.md) and [ADR 0002](adr/0002-conservatism-haircut.md).
