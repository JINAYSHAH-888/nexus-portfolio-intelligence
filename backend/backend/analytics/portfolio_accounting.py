"""
Portfolio accounting: FIFO lot matching, Indian capital-gains tax, XIRR.

Pure functions over Decimals. No DB, no HTTP, no engine imports -- so every
rule here is unit-testable in isolation, which is the only responsible way to
write code that computes someone's tax liability.

WHY Decimal AND NOT float
-------------------------
Units carry 3-4 decimals, NAVs 4. A monthly SIP over 20 years is 240 lots; float
error accumulates across them and the holdings stop reconciling with the AMC's
own statement. `Decimal` is exact. Floats appear exactly once, at the very end,
in the JSON boundary.

INDIAN MF CAPITAL GAINS -- the rules encoded below
--------------------------------------------------
These are tax FACTS, not market assumptions, and they changed materially in
Budget 2024 (effective 23 July 2024):

  EQUITY-oriented schemes (>=65% domestic equity)
    * held <= 12 months  -> STCG @ 20%          (was 15% pre-Jul-2024)
    * held  > 12 months  -> LTCG @ 12.5%, on gains above a ₹1,25,000
                            aggregate exemption per financial year
                            (was ₹1,00,000 @ 10%)

  DEBT schemes acquired ON/AFTER 1 April 2023 ("specified mutual funds",
  s.50AA) -> ALL gains are deemed short-term and taxed AT SLAB RATE, with no
  indexation and no LTCG treatment, no matter how long they are held. This is
  the rule that most home-grown P&L code gets wrong: it keeps applying a 20%
  post-indexation LTCG that no longer exists.

  DEBT acquired BEFORE 1 April 2023 -> grandfathered: >24 months qualifies as
  long-term @ 12.5% without indexation; otherwise slab.

The ₹1.25L LTCG exemption and the 12.5% rate agree with `arise_engine`'s
`LTCG_EXEMPTION = 125_000` / `LTCG_RATE = 0.125`, so the decumulation model and
the P&L endpoint cannot disagree about tax.

These constants are module-level and dated so a future rate change is a
one-line, reviewable edit -- not a hunt through business logic.

NOT TAX ADVICE. Surfaced to the investor as an indicative computation.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Literal

# ── Tax constants (Finance Act 2024, w.e.f. 23 July 2024) ─────────────────────
EQUITY_LTCG_RATE = Decimal("0.125")
EQUITY_LTCG_EXEMPTION = Decimal("125000")     # per financial year, aggregate
EQUITY_STCG_RATE = Decimal("0.20")
EQUITY_LTCG_MIN_DAYS = 365                    # >12 months

DEBT_LTCG_RATE = Decimal("0.125")             # grandfathered units only
DEBT_LTCG_MIN_DAYS = 730                      # >24 months
# On/after this date, debt MF gains are ALWAYS slab-rate (s.50AA).
SPECIFIED_MF_CUTOFF = date(2023, 4, 1)

TWO_DP = Decimal("0.01")
FOUR_DP = Decimal("0.0001")


def _money(d: Decimal) -> Decimal:
    return d.quantize(TWO_DP, rounding=ROUND_HALF_UP)


def _units(d: Decimal) -> Decimal:
    return d.quantize(FOUR_DP, rounding=ROUND_HALF_UP)


GainType = Literal["stcg", "ltcg"]


@dataclass
class Lot:
    """An open purchase lot: units bought on a date at a NAV."""

    txn_id: str
    scheme_id: str
    units: Decimal
    nav: Decimal
    txn_date: date

    @property
    def cost(self) -> Decimal:
        return _money(self.units * self.nav)


@dataclass
class RealisedGain:
    """One matched (buy lot -> sell) pair."""

    scheme_id: str
    units: Decimal
    buy_date: date
    sell_date: date
    buy_nav: Decimal
    sell_nav: Decimal
    cost: Decimal
    proceeds: Decimal
    gain: Decimal
    holding_days: int
    gain_type: GainType
    is_equity: bool


@dataclass
class SchemeHolding:
    """Current open position in one scheme."""

    scheme_id: str
    scheme_name: str
    instrument_type: str
    units: Decimal
    cost_basis: Decimal          # what they actually paid for the OPEN units
    avg_cost_nav: Decimal
    current_nav: Decimal
    current_value: Decimal
    unrealised_gain: Decimal
    unrealised_gain_pct: Decimal
    nav_source: str
    lots: list[Lot] = field(default_factory=list)

    @property
    def has_units(self) -> bool:
        return self.units > 0


class InsufficientUnitsError(ValueError):
    """A sell was recorded for more units than the investor actually holds."""


def is_long_term(*, is_equity: bool, buy_date: date, sell_date: date) -> bool:
    """Apply the correct holding-period rule for the asset class and vintage."""
    holding_days = (sell_date - buy_date).days

    if is_equity:
        return holding_days > EQUITY_LTCG_MIN_DAYS

    # Debt bought on/after 1 Apr 2023 can NEVER be long-term (s.50AA): every
    # gain is deemed short-term and taxed at slab. This is the rule most
    # hand-rolled P&L code still gets wrong.
    if buy_date >= SPECIFIED_MF_CUTOFF:
        return False

    return holding_days > DEBT_LTCG_MIN_DAYS


def match_fifo(
    lots: Iterable[Lot],
    *,
    sell_units: Decimal,
    sell_nav: Decimal,
    sell_date: date,
    is_equity: bool,
) -> tuple[list[RealisedGain], deque[Lot]]:
    """
    Consume the OLDEST lots first (FIFO), the method Indian MF registrars and
    the Income Tax Act both use for units.

    Returns (realised gains, remaining open lots). Partial lot consumption is
    supported -- a sell rarely lines up with a lot boundary.
    """
    open_lots: deque[Lot] = deque(sorted(lots, key=lambda x: (x.txn_date, x.txn_id)))
    remaining = sell_units
    gains: list[RealisedGain] = []

    available = sum((lot.units for lot in open_lots), Decimal(0))
    if remaining > available:
        raise InsufficientUnitsError(
            f"Cannot sell {remaining} units; only {available} are held."
        )

    while remaining > 0 and open_lots:
        lot = open_lots[0]
        take = min(lot.units, remaining)

        cost = _money(take * lot.nav)
        proceeds = _money(take * sell_nav)
        long_term = is_long_term(
            is_equity=is_equity, buy_date=lot.txn_date, sell_date=sell_date
        )

        gains.append(
            RealisedGain(
                scheme_id=lot.scheme_id,
                units=_units(take),
                buy_date=lot.txn_date,
                sell_date=sell_date,
                buy_nav=lot.nav,
                sell_nav=sell_nav,
                cost=cost,
                proceeds=proceeds,
                gain=_money(proceeds - cost),
                holding_days=(sell_date - lot.txn_date).days,
                gain_type="ltcg" if long_term else "stcg",
                is_equity=is_equity,
            )
        )

        if take == lot.units:
            open_lots.popleft()
        else:
            # Partial consumption: shrink the lot in place, preserving its
            # ORIGINAL buy date. Resetting the date here would silently reset
            # the holding-period clock and convert LTCG into STCG.
            open_lots[0] = Lot(
                txn_id=lot.txn_id,
                scheme_id=lot.scheme_id,
                units=_units(lot.units - take),
                nav=lot.nav,
                txn_date=lot.txn_date,
            )

        remaining -= take

    return gains, open_lots


@dataclass
class TaxSummary:
    equity_stcg: Decimal = Decimal(0)
    equity_ltcg: Decimal = Decimal(0)
    debt_stcg: Decimal = Decimal(0)   # slab-rate bucket
    debt_ltcg: Decimal = Decimal(0)

    equity_ltcg_exemption_used: Decimal = Decimal(0)
    equity_ltcg_taxable: Decimal = Decimal(0)

    tax_equity_stcg: Decimal = Decimal(0)
    tax_equity_ltcg: Decimal = Decimal(0)
    tax_debt_slab: Decimal = Decimal(0)
    tax_debt_ltcg: Decimal = Decimal(0)
    total_tax: Decimal = Decimal(0)


def compute_tax(gains: Iterable[RealisedGain], *, slab_rate: Decimal) -> TaxSummary:
    """
    Aggregate realised gains into an indicative tax liability.

    The ₹1.25L LTCG exemption is an ANNUAL AGGREGATE across all equity LTCG, not
    a per-transaction or per-scheme allowance -- applying it per scheme would
    understate tax for anyone holding several funds. Callers should pass gains
    for ONE financial year.

    Losses are carried into the bucket totals (they can be negative), but the
    tax on a negative bucket is floored at zero: set-off and carry-forward rules
    (s.70/74) are a genuinely separate feature and are NOT modelled here rather
    than being approximated wrongly.
    """
    s = TaxSummary()

    for g in gains:
        if g.is_equity:
            if g.gain_type == "ltcg":
                s.equity_ltcg += g.gain
            else:
                s.equity_stcg += g.gain
        else:
            if g.gain_type == "ltcg":
                s.debt_ltcg += g.gain
            else:
                s.debt_stcg += g.gain

    taxable_eq_ltcg = max(s.equity_ltcg - EQUITY_LTCG_EXEMPTION, Decimal(0))
    s.equity_ltcg_exemption_used = min(
        max(s.equity_ltcg, Decimal(0)), EQUITY_LTCG_EXEMPTION
    )
    s.equity_ltcg_taxable = taxable_eq_ltcg

    s.tax_equity_ltcg = _money(taxable_eq_ltcg * EQUITY_LTCG_RATE)
    s.tax_equity_stcg = _money(max(s.equity_stcg, Decimal(0)) * EQUITY_STCG_RATE)
    s.tax_debt_slab = _money(max(s.debt_stcg, Decimal(0)) * slab_rate)
    s.tax_debt_ltcg = _money(max(s.debt_ltcg, Decimal(0)) * DEBT_LTCG_RATE)

    s.total_tax = _money(
        s.tax_equity_ltcg + s.tax_equity_stcg + s.tax_debt_slab + s.tax_debt_ltcg
    )
    return s


# ── XIRR ──────────────────────────────────────────────────────────────────────
def xirr(cashflows: list[tuple[date, float]], *, guess: float = 0.1) -> float | None:
    """
    Money-weighted annualised return over irregularly-spaced cashflows.

    This is the number an investor actually experienced. A simple
    (value/cost - 1) is misleading for a SIP: money contributed last month has
    not had a year to compound, and a plain total-return figure silently
    pretends it has.

    Sign convention: outflows (buys) negative, inflows (sells + current value)
    positive.

    Solved by Newton-Raphson, falling back to bisection. Newton is fast but can
    diverge on the flat/pathological curves real portfolios produce (an almost
    fully-redeemed folio, a near-zero net gain); bisection is slower but cannot
    run away, and a wrong-but-confident return number is worse than none.

    Returns None when no sign change exists (all-buys, no value) -- there is no
    root to find, and inventing one would be fabrication.
    """
    if len(cashflows) < 2:
        return None

    flows = sorted(cashflows, key=lambda x: x[0])
    t0 = flows[0][0]
    amounts = [amt for _, amt in flows]

    if not (any(a > 0 for a in amounts) and any(a < 0 for a in amounts)):
        return None  # no sign change -> no IRR exists

    years = [((d - t0).days / 365.0) for d, _ in flows]

    def npv(rate: float) -> float:
        if rate <= -0.9999:
            return float("inf")
        return sum(a / ((1.0 + rate) ** t) for a, t in zip(amounts, years))

    # ── Newton-Raphson ────────────────────────────────────────────────────────
    rate = guess
    for _ in range(100):
        f = npv(rate)
        if abs(f) < 1e-7:
            return rate
        # Numerical derivative: the analytic one is easy but this is robust to
        # the degenerate t=0 term.
        h = 1e-6
        df = (npv(rate + h) - f) / h
        if abs(df) < 1e-12:
            break
        step = f / df
        new_rate = rate - step
        if new_rate <= -0.9999:
            break
        if abs(step) < 1e-9:
            return new_rate
        rate = new_rate

    # ── Bisection fallback ────────────────────────────────────────────────────
    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None  # not bracketed -> no reliable root

    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-7:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid

    return (lo + hi) / 2
