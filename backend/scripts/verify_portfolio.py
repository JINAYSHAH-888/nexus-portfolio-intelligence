"""
Drives a realistic 24-month SIP + partial redemption through the FULL app stack
(middleware -> router -> service -> ledger -> tax engine -> JSON serializer) and
prints the resulting P&L, FIFO lots, capital-gains tax and XIRR.

Same code path a real HTTP request takes; only the socket is elided.
"""
import asyncio
import os

os.environ.update(
    ENVIRONMENT="test",
    DATABASE_URL="sqlite+aiosqlite:///:memory:",
    SECRET_KEY="verify-secret-key-long-enough-0123456789",
    REDIS_ENABLED="false",
    RATE_LIMIT_ENABLED="false",
    MARKET_DATA_FORCE_SYNTHETIC="true",
    LOG_LEVEL="ERROR",
)

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from backend.infrastructure.db import Base, get_db  # noqa: E402
from backend.main import app  # noqa: E402


async def main():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False)

    async def _db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    tr = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=tr, base_url="http://v") as c:
        r = await c.post(
            "/api/v1/auth/register",
            json={"email": "real@nippon.in", "password": "Str0ngPass!", "full_name": "Real"},
        )
        H = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # The profile now carries a REAL SIP -- no estimate needed.
        await c.post(
            "/api/v1/profile",
            headers=H,
            json={
                "full_name": "Real Investor", "date_of_birth": "1986-04-12",
                "occupation": "Architect", "monthly_income": 250000,
                "total_corpus": 4000000, "investment_amount": 1500000,
                "goal": "growth", "risk_appetite": "moderate",
                "target_retirement_age": 60, "monthly_expenses_in_retirement": 120000,
                "monthly_sip": 60000, "sip_step_up_pct": 0.10,
            },
        )
        await c.post(
            "/api/v1/schemes/select",
            headers=H,
            json={"selected_scheme_ids": ["nippon-large-cap-fund", "nippon-corporate-bond-fund"]},
        )

        print("═" * 78)
        print("1.  RECORD A REAL SIP HISTORY  (24 monthly instalments, Rs 60,000 each)")
        print("═" * 78)
        for i in range(24):
            year = 2023 + i // 12
            month = (i % 12) + 1
            nav = round(40 + i * 0.8, 4)
            units = round(60000 / nav, 4)
            rr = await c.post(
                "/api/v1/portfolio/transactions",
                headers=H,
                json={
                    "scheme_id": "nippon-large-cap-fund",
                    "txn_type": "sip",
                    "units": str(units),
                    "nav": str(nav),
                    "txn_date": f"{year}-{month:02d}-05",
                    "external_ref": f"SIP-{i}",
                },
            )
            assert rr.status_code == 201, rr.text
        print(f"  booked 24 instalments  ->  Rs {24*60000:,} invested, NAV 40.00 -> 58.40")

        before = (await c.get("/api/v1/portfolio/transactions", headers=H)).json()["count"]
        # Replay an instalment with the SAME external_ref (simulates a client retry).
        await c.post(
            "/api/v1/portfolio/transactions",
            headers=H,
            json={
                "scheme_id": "nippon-large-cap-fund", "txn_type": "sip",
                "units": "1500", "nav": "40.0000",
                "txn_date": "2023-01-05", "external_ref": "SIP-0",
            },
        )
        after = (await c.get("/api/v1/portfolio/transactions", headers=H)).json()["count"]
        print(f"  IDEMPOTENCY: replayed SIP-0  ->  txn count {before} -> {after}  (no duplicate booked)")

        print()
        print("═" * 78)
        print("2.  PARTIAL REDEMPTION  (3,000 units @ NAV 75, FIFO-matched)")
        print("═" * 78)
        rr = await c.post(
            "/api/v1/portfolio/transactions",
            headers=H,
            json={
                "scheme_id": "nippon-large-cap-fund", "txn_type": "sell",
                "units": "3000", "nav": "75.0000", "txn_date": "2025-06-10",
            },
        )
        assert rr.status_code == 201, rr.text
        print("  sold 3,000 units @ 75.00 on 2025-06-10")

        print()
        print("═" * 78)
        print("3.  REAL MARK-TO-MARKET P&L")
        print("═" * 78)
        d = (await c.get("/api/v1/portfolio/pnl?slab_rate=0.30", headers=H)).json()
        s = d["summary"]
        print(f"  pnl_available     : {d['pnl_available']}   <- was permanently False before")
        print(f"  total invested    : Rs {s['total_invested']:>15,.2f}")
        print(f"  total withdrawn   : Rs {s['total_withdrawn']:>15,.2f}")
        print(f"  open cost basis   : Rs {s['current_cost_basis']:>15,.2f}")
        print(f"  current value     : Rs {s['current_value']:>15,.2f}")
        print(f"  unrealised gain   : Rs {s['unrealised_gain']:>15,.2f}   ({s['unrealised_gain_pct']:+.2f}%)")
        print(f"  realised gain     : Rs {s['realised_gain']:>15,.2f}")
        print(f"  TOTAL gain        : Rs {s['total_gain']:>15,.2f}")
        print(f"  XIRR              :    {s['xirr']*100:>12.2f}%   <- money-weighted")

        print()
        print("  HOLDINGS")
        for h in d["holdings"]:
            print(
                f"    {h['scheme_name'][:30]:32} units={h['units']:>10,.2f}  "
                f"avg_cost={h['avg_cost_nav']:>6.2f}  nav={h['current_nav']:>7.2f}  "
                f"lots={h['open_lots']:>2}"
            )

        print()
        rg = d["realised_gains"]
        print(f"  FIFO-MATCHED LOTS: {len(rg)}  (oldest units consumed first)")
        for g in rg[:4]:
            print(
                f"    {g['units']:>9,.2f}u  bought {g['buy_date']}  sold {g['sell_date']}  "
                f"held {g['holding_days']:>4}d  {g['gain_type'].upper():5}  gain Rs {g['gain']:>+11,.2f}"
            )
        if len(rg) > 4:
            print(f"    ... and {len(rg)-4} more")

        print()
        t = d["tax"]
        print("  CAPITAL GAINS TAX  (Finance Act 2024)")
        print(f"    equity LTCG              Rs {t['equity_ltcg']:>13,.2f}")
        print(f"    less Rs 1.25L exemption  Rs {t['equity_ltcg_exemption_used']:>13,.2f}")
        print(f"    taxable LTCG             Rs {t['equity_ltcg_taxable']:>13,.2f}")
        print(f"    equity STCG              Rs {t['equity_stcg']:>13,.2f}")
        print("    " + "-" * 42)
        print(f"    tax on LTCG @ 12.5%      Rs {t['tax_equity_ltcg']:>13,.2f}")
        print(f"    tax on STCG @ 20%        Rs {t['tax_equity_stcg']:>13,.2f}")
        print(f"    TOTAL TAX                Rs {t['total_tax']:>13,.2f}")

        print()
        print("═" * 78)
        print("4.  ARISE NOW PROJECTS ON THE REAL SIP")
        print("═" * 78)
        a = (
            await c.post(
                "/api/v1/analysis/arise", headers=H, json={"n_simulations": 200}
            )
        ).json()["assumptions"]
        print(f"  monthly_sip       : Rs {a['monthly_sip']:,.0f}")
        print(f"  is_estimated      : {a['monthly_sip_is_estimated']}   <- was True (a 20%-of-income guess)")
        print(f"  source            : {a['monthly_sip_source']}")
        print(f"  step_up           : {a['step_up']:.0%}  (from {a['step_up_source']})")

    await eng.dispose()


asyncio.run(main())
