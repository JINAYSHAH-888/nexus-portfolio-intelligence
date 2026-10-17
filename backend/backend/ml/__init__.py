"""
Learned models trained offline on real downloaded market history
(backend/data/market_history, see scripts/fetch_market_history.py).

Training happens in scripts/train_models.py and writes small JSON artifacts
to backend/data/models/. Engines only READ those artifacts at request time --
nothing is fitted inside an API call, so results stay deterministic and fast,
and an engine falls back to its previous behaviour if an artifact is missing.

Every model here is shipped only with an out-of-sample comparison against the
simpler model it would replace, recorded in the artifact's "validation" block.
"""
