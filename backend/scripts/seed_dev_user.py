"""
Seed a LOCAL development investor so the app screens have real data to show.

    backend/.venv/bin/python scripts/seed_dev_user.py   # API must be on :8000

Drives the real HTTP API end to end -- register (or log in if the account
exists), deep profile, scheme selection, allocation -- exactly as the frontend
does, so every analysis screen renders live engine output rather than sample
data. Idempotent: safe to re-run.

DEVELOPMENT ONLY. The credentials below exist solely for a local database and
must never be used against a shared or production deployment.

The portfolio is chosen to show the product's core finding: Large Cap and
Quant share 41% of their money (weighted overlap) despite only 15 common
holdings, alongside a growth fund and a bond fund for diversification.
"""
from __future__ import annotations

import sys

import httpx

API = "http://localhost:8000/api/v1"
DEV_EMAIL = "dev.investor@example.com"
DEV_PASSWORD = "Nexus-Dev-Local-2026!"
DEV_NAME = "Dev Investor"

PROFILE = {
    "full_name": DEV_NAME,
    "date_of_birth": "1986-04-12",
    "occupation": "Software Architect",
    "monthly_income": 250_000,
    "total_corpus": 4_000_000,
    "investment_amount": 1_500_000,
    "goal": "growth",
    "risk_appetite": "moderate",
    "target_retirement_age": 60,
    "monthly_expenses_in_retirement": 120_000,
    "monthly_sip": 60_000,
    "sip_step_up_pct": 0.10,
}

WEIGHTS = {
    "nippon-large-cap-fund": 0.35,
    "nippon-quant-fund": 0.25,
    "nippon-flexi-cap-fund": 0.20,
    "nippon-corporate-bond-fund": 0.20,
}


def main() -> int:
    with httpx.Client(base_url=API, timeout=120) as c:
        # OAuth2 password flow: form-encoded `username`/`password`, not JSON.
        r = c.post("/auth/login", data={"username": DEV_EMAIL, "password": DEV_PASSWORD})
        if r.status_code != 200:
            r = c.post(
                "/auth/register",
                json={"email": DEV_EMAIL, "password": DEV_PASSWORD, "full_name": DEV_NAME},
            )
            if r.status_code != 201:
                print("register failed:", r.status_code, r.text)
                return 1
            print("registered", DEV_EMAIL)
        else:
            print("logged in", DEV_EMAIL)
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = c.get("/profile", headers=h)
        r = (c.put if r.status_code == 200 else c.post)("/profile", json=PROFILE, headers=h)
        if r.status_code not in (200, 201):
            print("profile failed:", r.status_code, r.text)
            return 1
        print("profile ok  age", r.json().get("age"), " years_to_retirement", r.json().get("years_to_retirement"))

        r = c.post("/schemes/select", json={"selected_scheme_ids": list(WEIGHTS)}, headers=h)
        if r.status_code != 200:
            print("select failed:", r.status_code, r.text)
            return 1
        r = c.post("/schemes/allocate", json={"weights": WEIGHTS}, headers=h)
        if r.status_code != 200:
            print("allocate failed:", r.status_code, r.text)
            return 1
        print("portfolio ok", r.json()["weights"])

        # Warm and verify every analysis endpoint the screens call.
        for path, method in [("/analysis/overlap", "get"), ("/analysis/plos", "get"),
                             ("/analysis/risk", "get"), ("/analysis/arise", "post"),
                             ("/market/dashboard", "get")]:
            r = getattr(c, method)(path, headers=h, **({"json": {}} if method == "post" else {}))
            print(f"  {r.status_code}  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
