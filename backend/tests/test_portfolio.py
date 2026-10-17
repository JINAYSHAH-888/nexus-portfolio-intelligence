"""
The transaction ledger, real P&L, Indian capital-gains tax, XIRR, the real SIP,
and the distributed rate limiter.

This is money code. The tax rules are pinned by hand-computed expected values,
not by whatever the implementation happens to return.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.analytics.portfolio_accounting import (
    InsufficientUnitsError,
    Lot,
    compute_tax,
    is_long_term,
    match_fifo,
    xirr,
)

D = Decimal


# ══════════════════════════════════════════════════════════════════════════════
# Holding-period rules
# ══════════════════════════════════════════════════════════════════════════════
class TestHoldingPeriod:
    def test_equity_long_term_above_12_months(self):
        buy = date(2023, 1, 1)
        assert not is_long_term(is_equity=True, buy_date=buy, sell_date=date(2023, 12, 31))
        assert not is_long_term(is_equity=True, buy_date=buy, sell_date=buy + timedelta(days=365))
        assert is_long_term(is_equity=True, buy_date=buy, sell_date=buy + timedelta(days=366))

    def test_debt_bought_after_apr_2023_is_never_long_term(self):
        """
        s.50AA: units in a 'specified mutual fund' acquired on/after 1 Apr 2023
        are deemed SHORT-TERM however long they are held. This is the rule most
        hand-rolled P&L code still gets wrong -- it keeps applying an indexed
        20% LTCG that no longer exists.
        """
        buy = date(2023, 4, 1)
        assert not is_long_term(is_equity=False, buy_date=buy, sell_date=date(2033, 4, 1))

    def test_debt_bought_before_apr_2023_is_grandfathered(self):
        buy = date(2023, 3, 31)
        assert not is_long_term(is_equity=False, buy_date=buy, sell_date=buy + timedelta(days=730))
        assert is_long_term(is_equity=False, buy_date=buy, sell_date=buy + timedelta(days=731))


# ══════════════════════════════════════════════════════════════════════════════
# FIFO lot matching
# ══════════════════════════════════════════════════════════════════════════════
class TestFifo:
    def _lots(self):
        return [
            Lot("t1", "s", D("100"), D("10.0000"), date(2022, 1, 1)),
            Lot("t2", "s", D("100"), D("20.0000"), date(2023, 1, 1)),
        ]

    def test_oldest_lot_is_consumed_first(self):
        gains, remaining = match_fifo(
            self._lots(),
            sell_units=D("100"),
            sell_nav=D("30.0000"),
            sell_date=date(2024, 6, 1),
            is_equity=True,
        )
        assert len(gains) == 1
        g = gains[0]
        assert g.buy_nav == D("10.0000")          # the 2022 lot, not the 2023 one
        assert g.cost == D("1000.00")
        assert g.proceeds == D("3000.00")
        assert g.gain == D("2000.00")
        assert len(remaining) == 1
        assert remaining[0].nav == D("20.0000")

    def test_sell_spanning_two_lots_splits_correctly(self):
        gains, remaining = match_fifo(
            self._lots(),
            sell_units=D("150"),
            sell_nav=D("30.0000"),
            sell_date=date(2024, 6, 1),
            is_equity=True,
        )
        assert len(gains) == 2
        assert gains[0].units == D("100.0000") and gains[0].gain == D("2000.00")
        assert gains[1].units == D("50.0000")
        # 50 units bought at 20, sold at 30 -> 500
        assert gains[1].gain == D("500.00")
        assert len(remaining) == 1
        assert remaining[0].units == D("50.0000")

    def test_partial_consumption_preserves_the_original_buy_date(self):
        """
        Resetting the lot's date on partial consumption would silently restart
        the holding-period clock and turn LTCG into STCG.
        """
        _gains, remaining = match_fifo(
            self._lots(),
            sell_units=D("50"),
            sell_nav=D("30"),
            sell_date=date(2024, 6, 1),
            is_equity=True,
        )
        assert remaining[0].txn_date == date(2022, 1, 1)
        assert remaining[0].units == D("50.0000")

    def test_holding_period_is_taken_per_lot(self):
        """A single sell can realise LTCG on the old lot and STCG on the new one."""
        lots = [
            Lot("t1", "s", D("100"), D("10"), date(2022, 1, 1)),   # >1y -> LTCG
            Lot("t2", "s", D("100"), D("20"), date(2024, 5, 1)),   # <1y -> STCG
        ]
        gains, _ = match_fifo(
            lots, sell_units=D("150"), sell_nav=D("30"),
            sell_date=date(2024, 6, 1), is_equity=True,
        )
        assert gains[0].gain_type == "ltcg"
        assert gains[1].gain_type == "stcg"

    def test_overselling_is_rejected(self):
        with pytest.raises(InsufficientUnitsError):
            match_fifo(
                self._lots(), sell_units=D("500"), sell_nav=D("30"),
                sell_date=date(2024, 6, 1), is_equity=True,
            )

    def test_a_loss_is_recorded_as_a_negative_gain(self):
        gains, _ = match_fifo(
            [Lot("t1", "s", D("100"), D("30"), date(2022, 1, 1))],
            sell_units=D("100"), sell_nav=D("10"),
            sell_date=date(2024, 1, 1), is_equity=True,
        )
        assert gains[0].gain == D("-2000.00")


# ══════════════════════════════════════════════════════════════════════════════
# Tax — hand-computed expectations
# ══════════════════════════════════════════════════════════════════════════════
class TestTax:
    def _gain(self, amount, *, equity=True, gtype="ltcg"):
        gains, _ = match_fifo(
            [Lot("t", "s", D("1"), D("0.0001"), date(2020, 1, 1))],
            sell_units=D("1"), sell_nav=D("1"), sell_date=date(2024, 1, 1),
            is_equity=equity,
        )
        g = gains[0]
        g.gain = D(str(amount))
        g.gain_type = gtype
        g.is_equity = equity
        return g

    def test_equity_ltcg_below_the_exemption_is_untaxed(self):
        t = compute_tax([self._gain(100_000)], slab_rate=D("0.30"))
        assert t.equity_ltcg == D("100000")
        assert t.equity_ltcg_taxable == D("0")
        assert t.tax_equity_ltcg == D("0.00")

    def test_equity_ltcg_above_the_exemption(self):
        # Rs 3,25,000 gain - Rs 1,25,000 exemption = Rs 2,00,000 @ 12.5% = Rs 25,000
        t = compute_tax([self._gain(325_000)], slab_rate=D("0.30"))
        assert t.equity_ltcg_taxable == D("200000")
        assert t.tax_equity_ltcg == D("25000.00")

    def test_the_exemption_is_an_annual_aggregate_not_per_scheme(self):
        """
        Three schemes, Rs 1,00,000 LTCG each. Applying the exemption per scheme
        would tax nothing. Correctly aggregated: 3,00,000 - 1,25,000 = 1,75,000
        @ 12.5% = Rs 21,875.
        """
        gains = [self._gain(100_000) for _ in range(3)]
        t = compute_tax(gains, slab_rate=D("0.30"))
        assert t.equity_ltcg == D("300000")
        assert t.tax_equity_ltcg == D("21875.00")

    def test_equity_stcg_is_20_percent(self):
        t = compute_tax([self._gain(100_000, gtype="stcg")], slab_rate=D("0.30"))
        assert t.tax_equity_stcg == D("20000.00")  # 20%, not the pre-2024 15%

    def test_debt_gains_are_taxed_at_the_slab_rate(self):
        t = compute_tax(
            [self._gain(100_000, equity=False, gtype="stcg")], slab_rate=D("0.30")
        )
        assert t.tax_debt_slab == D("30000.00")
        assert t.tax_equity_ltcg == D("0.00")

    def test_a_loss_does_not_produce_negative_tax(self):
        t = compute_tax([self._gain(-50_000, gtype="stcg")], slab_rate=D("0.30"))
        assert t.equity_stcg == D("-50000")
        assert t.tax_equity_stcg == D("0.00")  # floored; set-off is a separate feature
        assert t.total_tax == D("0.00")


# ══════════════════════════════════════════════════════════════════════════════
# XIRR
# ══════════════════════════════════════════════════════════════════════════════
class TestXirr:
    def test_simple_doubling_over_one_year(self):
        r = xirr([(date(2023, 1, 1), -1000.0), (date(2024, 1, 1), 2000.0)])
        assert r == pytest.approx(1.0, abs=0.01)  # +100%

    def test_flat_return_is_zero(self):
        r = xirr([(date(2023, 1, 1), -1000.0), (date(2024, 1, 1), 1000.0)])
        assert r == pytest.approx(0.0, abs=0.001)

    def test_a_loss_gives_a_negative_rate(self):
        r = xirr([(date(2023, 1, 1), -1000.0), (date(2024, 1, 1), 500.0)])
        assert r < 0

    def test_sip_xirr_beats_the_naive_total_return(self):
        """
        THE REASON XIRR EXISTS. 12 monthly SIPs of 1,000 (12,000 invested) worth
        13,000 today is a naive +8.3%. But the average rupee was only invested
        ~6 months, so the true annualised return is far higher. A dashboard
        reporting 8.3% would understate the fund's actual performance.
        """
        flows = [(date(2023, m, 1), -1000.0) for m in range(1, 13)]
        flows.append((date(2024, 1, 1), 13000.0))
        r = xirr(flows)
        naive = 13000 / 12000 - 1  # 0.0833
        assert r is not None
        assert r > naive * 1.5

    def test_no_sign_change_returns_none(self):
        # All outflows: there is no root. Inventing one would be fabrication.
        assert xirr([(date(2023, 1, 1), -100.0), (date(2023, 6, 1), -100.0)]) is None

    def test_single_flow_returns_none(self):
        assert xirr([(date(2023, 1, 1), -100.0)]) is None


# ══════════════════════════════════════════════════════════════════════════════
# The ledger over HTTP
# ══════════════════════════════════════════════════════════════════════════════
LARGE_CAP = "nippon-large-cap-fund"


async def _buy(client, headers, *, scheme=LARGE_CAP, units="100", nav="50", d="2022-01-10", ref=None):
    body = {
        "scheme_id": scheme,
        "txn_type": "buy",
        "units": units,
        "nav": nav,
        "txn_date": d,
    }
    if ref:
        body["external_ref"] = ref
    return await client.post("/api/v1/portfolio/transactions", json=body, headers=headers)


async def test_pnl_is_now_available_once_transactions_exist(client, profiled):
    h = profiled["headers"]

    # Empty ledger -> still honest, but now it's an empty state with a next action.
    r = await client.get("/api/v1/portfolio/pnl", headers=h)
    assert r.json()["pnl_available"] is False
    assert "POST /api/v1/portfolio/transactions" in r.json()["reason"]

    r = await _buy(client, h, units="100", nav="50")
    assert r.status_code == 201, r.text

    r = await client.get("/api/v1/portfolio/pnl", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["pnl_available"] is True

    s = body["summary"]
    assert s["total_invested"] == pytest.approx(5000.0)
    assert s["current_cost_basis"] == pytest.approx(5000.0)
    # Valued at the scheme's real factsheet NAV, so gain is whatever that implies
    # -- but value and gain must reconcile exactly.
    assert s["current_value"] == pytest.approx(
        body["holdings"][0]["units"] * body["holdings"][0]["current_nav"], rel=1e-6
    )
    assert s["unrealised_gain"] == pytest.approx(
        s["current_value"] - s["current_cost_basis"], abs=0.01
    )
    assert s["xirr"] is not None


async def test_units_and_cost_basis_reconcile_across_multiple_buys(client, profiled):
    h = profiled["headers"]
    await _buy(client, h, units="100", nav="50", d="2022-01-10")   # 5,000
    await _buy(client, h, units="50", nav="60", d="2023-01-10")    # 3,000

    r = await client.get("/api/v1/portfolio/pnl", headers=h)
    hold = r.json()["holdings"][0]
    assert hold["units"] == pytest.approx(150.0)
    assert hold["cost_basis"] == pytest.approx(8000.0)
    # Weighted average, not a simple mean of 50 and 60.
    assert hold["avg_cost_nav"] == pytest.approx(8000 / 150, abs=0.001)
    assert hold["open_lots"] == 2


async def test_sell_realises_fifo_gains_and_reduces_the_position(client, profiled):
    h = profiled["headers"]
    await _buy(client, h, units="100", nav="10", d="2022-01-10")
    await _buy(client, h, units="100", nav="20", d="2023-01-10")

    r = await client.post(
        "/api/v1/portfolio/transactions",
        json={
            "scheme_id": LARGE_CAP,
            "txn_type": "sell",
            "units": "100",
            "nav": "30",
            "txn_date": "2024-06-01",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text

    body = (await client.get("/api/v1/portfolio/pnl", headers=h)).json()

    realised = body["realised_gains"]
    assert len(realised) == 1
    assert realised[0]["gain"] == pytest.approx(2000.0)   # FIFO: the 10-NAV lot
    assert realised[0]["gain_type"] == "ltcg"             # equity, held >12m

    assert body["holdings"][0]["units"] == pytest.approx(100.0)
    assert body["summary"]["realised_gain"] == pytest.approx(2000.0)

    # Rs 2,000 LTCG sits under the Rs 1.25L exemption -> no tax.
    assert body["tax"]["tax_equity_ltcg"] == pytest.approx(0.0)


async def test_cannot_sell_more_units_than_held(client, profiled):
    h = profiled["headers"]
    await _buy(client, h, units="10", nav="50")
    r = await client.post(
        "/api/v1/portfolio/transactions",
        json={
            "scheme_id": LARGE_CAP,
            "txn_type": "sell",
            "units": "1000",
            "nav": "60",
            "txn_date": "2024-01-01",
        },
        headers=h,
    )
    assert r.status_code == 422
    assert "only" in r.json()["message"]


async def test_external_ref_makes_the_post_idempotent(client, profiled):
    """
    A retried POST from a flaky mobile connection must not book the purchase
    twice -- a duplicated buy silently doubles the cost basis and corrupts every
    tax number downstream.
    """
    h = profiled["headers"]
    r1 = await _buy(client, h, units="100", nav="50", ref="FOLIO123-TXN1")
    r2 = await _buy(client, h, units="100", nav="50", ref="FOLIO123-TXN1")

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]  # same row, not a duplicate

    body = (await client.get("/api/v1/portfolio/pnl", headers=h)).json()
    assert body["summary"]["total_invested"] == pytest.approx(5000.0)  # NOT 10,000


async def test_amount_inconsistent_with_units_times_nav_is_rejected(client, profiled):
    r = await client.post(
        "/api/v1/portfolio/transactions",
        json={
            "scheme_id": LARGE_CAP,
            "txn_type": "buy",
            "units": "100",
            "nav": "50",
            "amount": "999999",   # should be ~5,000
            "txn_date": "2023-01-01",
        },
        headers=profiled["headers"],
    )
    assert r.status_code == 422
    assert "contract note" in r.json()["message"]


async def test_future_dated_transaction_is_rejected(client, profiled):
    future = (date.today() + timedelta(days=1)).isoformat()
    r = await _buy(client, profiled["headers"], d=future)
    assert r.status_code == 422


async def test_transactions_are_per_user(client, profiled, registered):
    """A ledger is financial PII. It must never cross users."""
    await _buy(client, profiled["headers"], units="100", nav="50")

    other = await client.post(
        "/api/v1/auth/register",
        json={"email": "other@e.com", "password": "Str0ngPass1!", "full_name": "O"},
    )
    oh = {"Authorization": f"Bearer {other.json()['access_token']}"}

    r = await client.get("/api/v1/portfolio/transactions", headers=oh)
    assert r.json()["count"] == 0

    r = await client.get("/api/v1/portfolio/pnl", headers=oh)
    assert r.json()["pnl_available"] is False


async def test_deleting_a_transaction_replays_the_ledger(client, profiled):
    """
    Positions are recomputed from the ledger, never kept as a running balance --
    so a correction propagates to P&L and tax immediately.
    """
    h = profiled["headers"]
    r = await _buy(client, h, units="100", nav="50")
    txn_id = r.json()["id"]
    await _buy(client, h, units="100", nav="60", d="2023-02-01")

    body = (await client.get("/api/v1/portfolio/pnl", headers=h)).json()
    assert body["summary"]["total_invested"] == pytest.approx(11000.0)

    r = await client.delete(f"/api/v1/portfolio/transactions/{txn_id}", headers=h)
    assert r.status_code == 204

    body = (await client.get("/api/v1/portfolio/pnl", headers=h)).json()
    assert body["summary"]["total_invested"] == pytest.approx(6000.0)
    assert body["holdings"][0]["units"] == pytest.approx(100.0)


async def test_debt_gain_is_taxed_at_slab_not_ltcg(client, profiled):
    """s.50AA: a debt fund bought after Apr-2023 and held 2+ years is STILL slab-rate."""
    h = profiled["headers"]
    await client.post(
        "/api/v1/portfolio/transactions",
        json={
            "scheme_id": "nippon-corporate-bond-fund",
            "txn_type": "buy",
            "units": "10000",
            "nav": "10",
            "txn_date": "2023-05-01",
        },
        headers=h,
    )
    await client.post(
        "/api/v1/portfolio/transactions",
        json={
            "scheme_id": "nippon-corporate-bond-fund",
            "txn_type": "sell",
            "units": "10000",
            "nav": "13",
            "txn_date": "2026-05-01",   # 3 years later
        },
        headers=h,
    )
    body = (await client.get("/api/v1/portfolio/pnl?slab_rate=0.30", headers=h)).json()

    assert body["realised_gains"][0]["gain_type"] == "stcg"  # deemed short-term
    assert body["tax"]["debt_gains_at_slab"] == pytest.approx(30000.0)
    assert body["tax"]["tax_debt_slab"] == pytest.approx(9000.0)  # 30%
    assert body["tax"]["tax_debt_ltcg"] == pytest.approx(0.0)


# ══════════════════════════════════════════════════════════════════════════════
# The real SIP
# ══════════════════════════════════════════════════════════════════════════════
async def test_arise_uses_the_profiles_real_sip_instead_of_estimating(client, selected):
    h = selected["headers"]

    r = await client.post(
        "/api/v1/analysis/arise", json={"n_simulations": 100}, headers=h
    )
    a = r.json()["assumptions"]
    assert a["monthly_sip_is_estimated"] is True   # no SIP on the profile yet

    r = await client.put(
        "/api/v1/profile", json={"monthly_sip": 65_000, "sip_step_up_pct": 0.10}, headers=h
    )
    assert r.status_code == 200
    assert r.json()["monthly_sip"] == 65_000

    r = await client.post(
        "/api/v1/analysis/arise", json={"n_simulations": 100}, headers=h
    )
    a = r.json()["assumptions"]
    assert a["monthly_sip"] == 65_000
    assert a["monthly_sip_is_estimated"] is False
    assert a["monthly_sip_source"] == "profile.monthly_sip (investor-supplied)"
    # The investor's own escalation is honoured when the request doesn't override.
    assert a["step_up"] == pytest.approx(0.10)


async def test_request_sip_still_overrides_the_profile(client, selected):
    h = selected["headers"]
    await client.put("/api/v1/profile", json={"monthly_sip": 65_000}, headers=h)

    r = await client.post(
        "/api/v1/analysis/arise",
        json={"n_simulations": 100, "monthly_sip": 90_000},
        headers=h,
    )
    a = r.json()["assumptions"]
    assert a["monthly_sip"] == 90_000
    assert a["monthly_sip_source"] == "client-supplied (request)"


async def test_sip_cannot_exceed_income(client, registered):
    from tests.conftest import VALID_PROFILE

    r = await client.post(
        "/api/v1/profile",
        json={**VALID_PROFILE, "monthly_sip": 500_000},  # income is 250,000
        headers=registered["headers"],
    )
    assert r.status_code == 422
    assert "cannot exceed" in str(r.json()["details"])


# ══════════════════════════════════════════════════════════════════════════════
# Rate limiter
# ══════════════════════════════════════════════════════════════════════════════
class TestRateLimiter:
    async def test_falls_back_locally_when_redis_is_down(self):
        """
        REDIS_ENABLED=false in tests, so this exercises the fallback path. A
        limiter that 500s when its cache dies has turned a minor degradation
        into an outage.
        """
        from backend.infrastructure import rate_limit

        key = "rl:test:fallback-user"
        results = [await rate_limit.check(key, limit=3, window_s=60.0) for _ in range(5)]

        assert all(r.backend == "local" for r in results)
        assert [r.allowed for r in results] == [True, True, True, False, False]
        assert results[-1].retry_after > 0

    async def test_buckets_are_independent(self):
        """Hammering /auth must not exhaust a user's budget for /analysis."""
        from backend.infrastructure import rate_limit

        for _ in range(3):
            await rate_limit.check("rl:auth:bucket-user", limit=3, window_s=60.0)

        blocked = await rate_limit.check("rl:auth:bucket-user", limit=3, window_s=60.0)
        other = await rate_limit.check("rl:analysis:bucket-user", limit=3, window_s=60.0)

        assert blocked.allowed is False
        assert other.allowed is True

    def test_authenticated_users_are_keyed_by_id_not_ip(self):
        """
        IP alone is wrong in both directions: a NATed office throttles itself,
        while an attacker with a proxy pool rotates around it.
        """
        from backend.infrastructure import rate_limit

        assert rate_limit.client_id("1.2.3.4", "user-abc") == "user:user-abc"
        assert rate_limit.client_id("1.2.3.4", None) == "ip:1.2.3.4"

    def test_expensive_endpoints_get_tighter_buckets(self):
        from backend.core.config import settings
        from backend.infrastructure import rate_limit

        auth_limit, auth_bucket = rate_limit.limit_for("/api/v1/auth/login")
        analysis_limit, analysis_bucket = rate_limit.limit_for("/api/v1/analysis/arise")
        default_limit, _ = rate_limit.limit_for("/api/v1/schemes/filtered")

        assert auth_bucket == "auth"
        assert analysis_bucket == "analysis"
        assert analysis_limit < default_limit   # ARISE is the costliest call
        assert auth_limit < default_limit       # credential stuffing
        assert default_limit == settings.RATE_LIMIT_DEFAULT_PER_MINUTE
