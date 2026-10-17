"""Loaders for the downloaded market history. Pure pandas, cached per process."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "data"
HISTORY = DATA / "market_history"
MODELS = DATA / "models"


@lru_cache(maxsize=1)
def nav_daily() -> pd.DataFrame:
    """date x scheme_id Direct-Growth NAV (only AMFI codes verified against
    the factsheet NAV are present)."""
    return pd.read_csv(HISTORY / "nav_daily.csv.gz", index_col=0, parse_dates=True).sort_index()


@lru_cache(maxsize=1)
def index_daily() -> pd.DataFrame:
    return pd.read_csv(HISTORY / "index_daily.csv.gz", index_col=0, parse_dates=True).sort_index()


@lru_cache(maxsize=1)
def sensex_monthly_log_returns() -> pd.Series:
    s = pd.read_csv(HISTORY / "index_monthly.csv", index_col=0, parse_dates=True)["sensex"]
    s = s.resample("ME").last().dropna()
    return np.log(s).diff().dropna()


def nav_log_returns(min_obs: int = 250) -> pd.DataFrame:
    """Daily log returns per scheme on the scheme's own trading days. NAVs
    are published on AMFI business days, so no forward-fill is applied --
    a filled holiday would fake a zero return and bias correlations down."""
    nav = nav_daily()
    r = np.log(nav).diff()
    return r.loc[:, r.notna().sum() >= min_obs]


def market_log_returns() -> pd.DataFrame:
    idx = index_daily()
    return np.log(idx).diff()


def load_artifact(name: str) -> dict | None:
    p = MODELS / name
    if not p.exists():
        return None
    return json.loads(p.read_text())
