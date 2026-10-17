# Learned and quant models

Each engine has its own models, trained offline on real downloaded history and judged out of sample against the model it replaces. Nothing is fitted during an API call. Engines read small JSON artifacts in `backend/backend/data/models/` and fall back to their previous behaviour if one is missing.

```bash
cd backend
.venv/bin/python scripts/fetch_market_history.py   # data (~3 min)
.venv/bin/python scripts/train_models.py           # all models (~5 min)
```

## Data (`backend/backend/data/market_history/`, see `manifest.json`)

| File | Source | Coverage |
|---|---|---|
| `nav_daily.csv.gz` | AMFI via mfapi.in, Direct-Growth | 34 of 35 schemes, 2013– · every AMFI code verified against the NAV printed on the scheme's own factsheet |
| `stocks_daily.csv.gz` | Yahoo `.NS`, adjusted close | 188 of 196 mapped holdings · 92–100% of each Indian fund's disclosed weight |
| `factors_daily.csv.gz` | Yahoo | 10 Nifty sector/size indices, India VIX, S&P 500, USD/INR, crude, gold |
| `nifty_ohlc.csv.gz` | Yahoo `^NSEI` | daily OHLC 2007– (Garman–Klass realised variance) |
| `index_daily.csv.gz`, `index_monthly.csv` | Yahoo | Nifty 50 / Midcap 50 daily; Sensex monthly 1997– |
| `nifty_valuation_monthly.csv` | NSE Indices (official) | Nifty 50 P/E, P/B, dividend yield, month-end 1999–2026 |

Real one-day NAV moves reach −25% (Yes Bank side-pocketing, 5 Mar 2020) and are kept: that is genuine credit risk.

## How "accurate" is measured

| Engine | Target | Score |
|---|---|---|
| PLOS | Future weekly-return correlation of each fund pair | R² on correlation, **rolling forward-in-time** (primary) and leave-one-scheme-out |
| Risk | Next-day / next-5-day realised variance; daily VaR & ES | QLIKE + Diebold–Mariano; Kupiec, Christoffersen, FZ0 on 13 real funds |
| ARISE | Distribution of each fund's next-3-year return | CRPS and coverage of the 50% / 80% ranges |

Scoring rules live in `backend/backend/ml/evaluation.py`.

## PLOS v3 (`ml/plos_model.py`, artifact `plos_v3.json`)

Ten layers: return correlation, residual correlation and factor similarity (NAV vs Nifty 50 / Midcap); **holdings-implied correlation** (today's weights × Ledoit–Wolf stock covariance); **sector style similarity** (Sharpe style analysis on 9 sector indices); four holdings-overlap layers; same category.

Model: linear in Fisher-z space, non-negative weights learned over 7 yearly origins (2,994 pair-years) and ridge-shrunk toward the v1 hand weights. Per-layer contributions are **exact Shapley values**.

| Model | Forward-in-time R² | Leave-one-scheme-out R² |
|---|---|---|
| v1 as shipped | 0.570 | 0.666 |
| v1 weights in Fisher-z | 0.629 | 0.693 |
| **v3 learned, shrunk (used)** | **0.626** | **0.701** |
| Gradient-boosted trees | 0.575 | 0.709 |

Trees won the cross-sectional test but were worst forward in time: they overfit each year's market-wide correlation level. "Funds you really hold" is 1/(w′Cw), the squared diversification ratio (Choueifaty & Coignard 2008). Meucci's minimum-torsion ENB was evaluated and rejected because it reports ~2.9 for two near-identical funds plus one independent fund.

## Risk v2 (`ml/vol_models.py`, artifacts `risk_v2.json`, `risk_market_var.csv.gz`)

Volatility forecasts, Nifty, 2013 onward, out of sample (QLIKE, lower is better):

| Model | 1 day | 5 days |
|---|---|---|
| GARCH-t (previous) | 0.421 | 0.268 |
| GJR-GARCH | 0.406 | 0.258 |
| HAR | 0.382 | 0.251 |
| **HAR-X: + India VIX, S&P 500, USD/INR, crude** | **0.336** | **0.206** |

Diebold–Mariano, GARCH vs HAR-X: stat 5.8 (1d), 5.0 (5d), p < 10⁻⁵. A gradient-boosted HAR-X did not beat the linear one.

Production VaR/ES: the portfolio's own GARCH variance blended 50/50 with HAR-X mapped through its beta; filtered historical tail at 95%, EVT (generalised Pareto) tail at 99%. On 13 funds × 21,450 days it improved the FZ0 VaR/ES score at both levels (95%: −3.754 → −3.780; 99%: −3.287 → −3.343), passed Kupiec for 13/13, and passed independence for 12/13 (95%) and 13/13 (99%). HAR-X alone scored best on FZ0 but its breaches clustered for 3 funds.

## ARISE v5 (`ml/arise_models.py`, artifacts `arise_calibration.json`, `arise_regime.json`)

Calibration on 390 three-year fund forecasts (2016–2023 origins; overlapping, ~2–3 independent periods):

| | 80% range holds | 50% range holds | CRPS |
|---|---|---|---|
| v4: AR(1) φ = −0.15, trailing vol | 67.4% | 37.4% | 0.1896 |
| **v5: φ = 0, vol blended 50/50 with 29-year market level** | **81.8%** | **47.4%** | **0.1878** |
| v5 + dividend-yield prior (used) | 82.8% | 47.2% | 0.1883 |

- φ = 0: Sensex variance ratios (VR(36m) = 0.77, s.e. 0.36) cannot tell mean reversion from noise.
- Valuation prior: next-5-year Nifty return ≈ −3.3% + 12.4% × dividend yield. Out-of-sample R² +0.46 (3y) and +0.45 (5y) against the historical mean; earnings yield failed (−0.52) due to COVID EPS and NSE's 2021 consolidated-EPS switch. Weight 4.6/(4.6+10) ≈ 0.32, first five years only.
- Joint equity–inflation bootstrap: not adopted. In India 2003–2025, FY Sensex returns and CPI are uncorrelated (r = −0.15, p = 0.49).
- Regime model: 2-state Student-t HMM, kept as a labelled stress scenario only. It lost to the iid Student-t at one-year forecasting over 21 rolling origins.

## Known limits

- Factsheet holdings are a 2026 snapshot, so the holdings-implied layer is stale for older training years.
- Price indices exclude dividends, except where noted (the valuation model adds the dividend yield).
- About two decades of Indian data contain few independent multi-year periods. Every multi-year claim above carries that caveat, and the models are shrunk accordingly.
