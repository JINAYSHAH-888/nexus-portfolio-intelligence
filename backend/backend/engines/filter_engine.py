"""
FilterEngine — the eligibility gate between Phase 1 (profiling) and Phase 3 (analysis).

Design principles:
  - Pure Python. No database access, no HTTP, no SQLAlchemy imports.
  - Deterministic: same inputs always produce the same FilterResult.
  - Schemes are loaded once from a JSON file and kept in memory.
  - Every rejection carries a human-readable reason — investors should
    understand *why* a scheme was hidden from them, not just that it was.

This engine is intentionally decoupled from the DB layer so it can be
unit-tested in isolation (see tests/test_filter_engine.py) without spinning
up a database or an HTTP client.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Risk hierarchy ──────────────────────────────────────────────────────────────
# Investor risk_appetite -> list of scheme risk_score_1_to_6 values they may access.
# This is a STRICT allow-list, not a threshold — e.g. "moderate" does NOT
# implicitly include score 4, even though 4 > 3. Nippon's dataset uses a
# 1(Low)..6(Very High) scale; investors only see schemes at or below their
# comfort tier.
RISK_ALLOWED: dict[str, list[int]] = {
    "conservative": [1, 2],
    "moderate": [1, 2, 3],
    "moderately_high": [1, 2, 3, 4],
    "aggressive": [1, 2, 3, 4, 5, 6],
}

RISK_LABELS: dict[int, str] = {
    1: "Low",
    2: "Low to Moderate",
    3: "Moderate",
    4: "Moderately High",
    5: "High",
    6: "Very High",
}

# ── Equity sub-tiering by REAL volatility (not the flat SEBI label) ────────────
# Every one of the 20 real Nippon equity schemes carries risk_score_1_to_6 == 6
# (Very High) -- a genuine SEBI riskometer fact, not an error. RISK_ALLOWED
# below still drives eligibility/disclosure uniformly for equity and debt
# (same score-based rule for both), but every equity scheme sharing risk_score
# 6 means the disclosure message would say the same thing for a 14%-vol
# large-cap fund and a 21%-vol thematic fund. This sub-tiering makes the
# equity disclosure message meaningful by using each scheme's own real
# disclosed std_deviation (13.62%-20.89% across these 20 schemes) to label
# it Core/Growth/Aggressive alongside the flagged risk score.
CORE_VOL_MAX = 16.0
GROWTH_VOL_MAX = 18.0


def equity_subtier(std_deviation: float | None) -> str:
    if std_deviation is None:
        return "Core"  # no disclosed volatility yet (e.g. a fund <3yrs old) -- default to the gentlest tier
    if std_deviation <= CORE_VOL_MAX:
        return "Core"
    if std_deviation <= GROWTH_VOL_MAX:
        return "Growth"
    return "Aggressive"


# ── Goal compatibility ──────────────────────────────────────────────────────
# A scheme's `suitable_for` tag reflects its OWN disclosed product-label
# objective (e.g. a debt fund's real SEBI product label says "Income over
# medium term" -- that text is never altered here). But an investor's GOAL
# is broader than any one product's own marketing language: a conservative
# investor pursuing "growth" is correctly served by income-oriented debt
# funds compounding over time when their risk tier doesn't admit equity --
# that IS how growth is achieved for that investor, even though the
# individual debt scheme's own label doesn't use the word "growth". Without
# this mapping, every risk tier below "aggressive" got ZERO eligible schemes
# for a "growth" goal once the fund universe narrowed to equity (all
# risk-score 6, aggressive-only per real riskometers) + debt (labelled
# "income", not "growth") -- a real usability bug, not a data problem.
#
# tax_saving is deliberately NOT broadened: Section 80C eligibility is a
# legal fact about the specific scheme (ELSS lock-in), not a preference: a
# non-ELSS fund can never satisfy a tax_saving goal, so it stays a strict
# exact match.
GOAL_COMPATIBLE_SUITABILITY: dict[str, set[str]] = {
    "growth": {"growth", "income"},
    "income": {"income", "safety"},
    "safety": {"safety", "income"},
    "tax_saving": {"tax_saving"},
}


# ── Dataclasses ───────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class SchemeInfo:
    """What the API returns for each eligible scheme."""

    id: str
    name: str
    category: str | None
    instrument_type: str
    risk_rating: str | None
    risk_score_1_to_6: int | None
    riskometer: str | None
    aum_cr: float | None
    nav_regular_growth: float | None
    expense_ratio_regular: float | None
    min_investment: int | None
    suitable_for: list[str] = field(default_factory=list)
    benchmark: str | None = None
    holdings_count: int = 0
    # ── Informed-consent disclosure (soft, never a hard block) ─────────────────
    subtier: str | None = None          # equity only: "Core" | "Growth" | "Aggressive"
    risk_flag: str | None = None        # None = matches investor's stated comfort
    risk_flag_level: str = "match"      # "match" | "mild" | "strong"


@dataclass(frozen=True, slots=True)
class FilteredOut:
    """A scheme that failed at least one eligibility rule, plus why."""

    scheme_id: str
    scheme_name: str
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FilterResult:
    """Complete output of a filter pass."""

    eligible_schemes: list[SchemeInfo]
    filtered_out: list[FilteredOut]
    eligible_count: int
    total_schemes: int
    filter_summary: dict[str, int]


# ── Engine ────────────────────────────────────────────────────────────────────
class FilterEngine:
    """
    Loads Nippon India scheme metadata once and applies eligibility rules
    against an investor's profile fields.

    Usage:
        engine = FilterEngine("backend/data/schemes_metadata.json")
        result = engine.filter_eligible_schemes(
            investor_age=39,
            monthly_income=250000,
            investment_amount=500000,
            goal="growth",
            risk_appetite="moderate",
        )
    """

    def __init__(self, schemes_json_path: str | Path) -> None:
        self._schemes_json_path = Path(schemes_json_path)
        self._schemes: list[dict[str, Any]] = self._load_schemes()

    # ── Loading ───────────────────────────────────────────────────────────────
    def _load_schemes(self) -> list[dict[str, Any]]:
        if not self._schemes_json_path.exists():
            raise FileNotFoundError(
                f"Schemes data file not found at: {self._schemes_json_path}"
            )
        with open(self._schemes_json_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError(
                f"Expected a JSON array of schemes in {self._schemes_json_path}, "
                f"got {type(data).__name__}."
            )
        return data

    @property
    def total_schemes(self) -> int:
        return len(self._schemes)

    def get_scheme_by_id(self, scheme_id: str) -> dict[str, Any] | None:
        """Lookup a single scheme by its ID. Returns None if not found."""
        for scheme in self._schemes:
            if scheme.get("id") == scheme_id:
                return scheme
        return None

    # ── Per-scheme rule evaluation ──────────────────────────────────────────────
    def _evaluate_scheme(
        self,
        scheme: dict[str, Any],
        *,
        investor_age: int | None,
        monthly_income: int | None,
        investment_amount: int,
        goal: str,
        risk_appetite: str,
        existing_holding_ids: set[str],
    ) -> tuple[list[str], list[str], str]:
        """
        Run all rules against one scheme.

        Returns (hard_reasons, soft_flag_notes, soft_flag_level):
          - hard_reasons: genuinely objective/legal blocks (age, income,
            investment amount, already-held, and tax_saving goal mismatch
            since ELSS 80(C) eligibility is a legal fact, not a preference).
            A non-empty list here means the scheme is excluded.
          - soft_flag_notes: risk and non-tax_saving goal mismatches. These
            do NOT exclude the scheme -- real Indian MF distribution
            discloses risk via the riskometer so the investor can decide,
            it doesn't hide products from certain risk profiles. The
            scheme stays visible with a plain-language note instead.
          - soft_flag_level: "match" | "mild" | "strong", for UI colour-coding.
        """
        hard: list[str] = []
        soft: list[str] = []
        level = "match"

        # ── Rule 1: Age (hard) ───────────────────────────────────────────────────
        min_age = scheme.get("min_age")
        max_age = scheme.get("max_age")
        if investor_age is not None:
            if min_age is not None and investor_age < min_age:
                hard.append(
                    f"Age: this scheme requires a minimum age of {min_age}, "
                    f"you are {investor_age}."
                )
            if max_age is not None and investor_age > max_age:
                hard.append(
                    f"Age: this scheme has a maximum age of {max_age}, "
                    f"you are {investor_age}."
                )

        # ── Rule 2: Income (hard) ────────────────────────────────────────────────
        min_monthly_income = scheme.get("min_monthly_income")
        if (
            monthly_income is not None
            and min_monthly_income is not None
            and monthly_income < min_monthly_income
        ):
            hard.append(
                f"Income: this scheme requires a minimum monthly income of "
                f"₹{min_monthly_income:,}, you reported ₹{monthly_income:,}."
            )

        # ── Rule 3: Investment amount (hard) ────────────────────────────────────
        min_investment = scheme.get("min_investment")
        max_investment = scheme.get("max_investment")
        if min_investment is not None and investment_amount < min_investment:
            hard.append(
                f"Investment amount: minimum investment for this scheme is "
                f"₹{min_investment:,}, you entered ₹{investment_amount:,}."
            )
        if max_investment is not None and investment_amount > max_investment:
            hard.append(
                f"Investment amount: maximum investment for this scheme is "
                f"₹{max_investment:,}, you entered ₹{investment_amount:,}."
            )

        # ── Rule 4: Risk match (SOFT -- disclosure, not a block) ────────────────
        # Uses the SAME risk_score_1_to_6 / RISK_ALLOWED check for both equity
        # and debt (so "conservative allows scores 1-2" etc. is one consistent
        # rule) -- for equity, real volatility (std_deviation) additionally
        # enriches the disclosure message with a concrete number and a
        # Core/Growth/Aggressive label, since every equity scheme shares the
        # same risk_score_1_to_6==6 "Very High" label but genuinely differs in
        # real volatility (13.62%-20.89% across these 20 schemes).
        instrument_type = scheme.get("instrument_type", "unknown")
        risk_score = scheme.get("risk_score_1_to_6")
        allowed_scores = RISK_ALLOWED.get(risk_appetite, [])
        if risk_score is not None and risk_score not in allowed_scores:
            scheme_risk_label = RISK_LABELS.get(risk_score, str(risk_score))
            if instrument_type == "equity":
                subtier = equity_subtier(scheme.get("std_deviation"))
                vol = scheme.get("std_deviation")
                vol_str = f"{vol:.1f}%" if vol is not None else "not yet published"
                soft.append(
                    f"Risk: this is a '{subtier}' equity scheme (real volatility "
                    f"{vol_str}, risk rating '{scheme_risk_label}'), above your "
                    f"stated '{risk_appetite}' comfort level. Real returns can "
                    f"swing significantly year to year -- review carefully "
                    f"before investing."
                )
                level = "strong" if subtier == "Aggressive" and risk_appetite == "conservative" else "mild"
            else:
                soft.append(
                    f"Risk: this scheme's real risk rating is '{scheme_risk_label}', "
                    f"above your stated '{risk_appetite}' comfort level -- review "
                    f"carefully before investing."
                )
                level = "mild" if level == "match" else level

        # ── Rule 5: Goal match ───────────────────────────────────────────────────
        # tax_saving is the ONE genuinely hard goal block: only ELSS carries the
        # actual Section 80(C) tax benefit -- a non-ELSS scheme literally cannot
        # deliver what a tax_saving goal requires, so this isn't paternalism,
        # it's a legal fact. Every other goal is a preference, disclosed softly.
        suitable_for = set(scheme.get("suitable_for") or [])
        if goal == "tax_saving" and "tax_saving" not in suitable_for:
            hard.append("Goal: only ELSS schemes qualify for Section 80(C) tax saving.")
        else:
            compatible = GOAL_COMPATIBLE_SUITABILITY.get(goal, {goal})
            if suitable_for.isdisjoint(compatible):
                kind = "equity" if instrument_type == "equity" else "debt"
                soft.append(
                    f"Goal: this {kind} scheme suits {sorted(suitable_for)}, you "
                    f"selected '{goal}' -- still a reasonable component of a "
                    f"broader portfolio, but check it matches your plan."
                )
                if level == "match":
                    level = "mild"

        # ── Rule 6: Already held (hard) ──────────────────────────────────────────
        if scheme.get("id") in existing_holding_ids:
            hard.append("Already held")

        return hard, soft, level

    # ── Reason -> rule bucket classification (for filter_summary) ──────────────
    @staticmethod
    def _classify_reason(reason: str) -> str:
        if reason.startswith("Age:"):
            return "by_age"
        if reason.startswith("Income:"):
            return "by_income"
        if reason.startswith("Investment amount:"):
            return "by_investment_amount"
        if reason.startswith("Risk:"):
            return "by_risk"
        if reason.startswith("Goal:"):
            return "by_goal"
        if reason == "Already held":
            return "already_held"
        return "other"  # pragma: no cover — defensive fallback, should be unreachable

    # ── Public API ───────────────────────────────────────────────────────────────
    def filter_eligible_schemes(
        self,
        *,
        investor_age: int | None,
        monthly_income: int | None,
        investment_amount: int,
        goal: str,
        risk_appetite: str,
        existing_holding_ids: list[str] | None = None,
    ) -> FilterResult:
        """
        Apply all 6 eligibility rules against every scheme.

        A scheme is eligible only if it passes ALL rules. The first rule
        that fails does not short-circuit evaluation — we collect every
        applicable failure reason so the investor sees the complete picture
        (e.g. a scheme might fail both risk AND goal simultaneously).

        Args:
            investor_age: computed from date_of_birth; None skips the age rule
            monthly_income: investor's gross monthly income in INR
            investment_amount: amount investor intends to invest now, in INR
            goal: one of "growth" | "income" | "safety" | "tax_saving"
            risk_appetite: one of "conservative" | "moderate" | "moderately_high" | "aggressive"
            existing_holding_ids: scheme IDs the investor already holds (optional)

        Returns:
            FilterResult with eligible_schemes, filtered_out, and a rule-by-rule
            filter_summary.

        Raises:
            ValueError: if risk_appetite is not a recognized value.
        """
        if risk_appetite not in RISK_ALLOWED:
            raise ValueError(
                f"Unknown risk_appetite '{risk_appetite}'. "
                f"Must be one of: {list(RISK_ALLOWED.keys())}"
            )

        held_ids = set(existing_holding_ids or [])

        eligible: list[SchemeInfo] = []
        filtered_out: list[FilteredOut] = []
        summary: dict[str, int] = {
            "by_age": 0,
            "by_income": 0,
            "by_investment_amount": 0,
            "by_risk": 0,          # hard risk blocks -- always 0 now (risk is disclosed, not blocked)
            "by_goal": 0,          # hard goal blocks -- only non-ELSS + tax_saving now
            "already_held": 0,
            "risk_flagged": 0,     # informational: eligible schemes outside stated risk comfort
            "goal_flagged": 0,     # informational: eligible schemes outside stated goal preference
        }

        for scheme in self._schemes:
            hard_reasons, soft_flags, flag_level = self._evaluate_scheme(
                scheme,
                investor_age=investor_age,
                monthly_income=monthly_income,
                investment_amount=investment_amount,
                goal=goal,
                risk_appetite=risk_appetite,
                existing_holding_ids=held_ids,
            )

            if not hard_reasons:
                if any(f.startswith("Risk:") for f in soft_flags):
                    summary["risk_flagged"] += 1
                if any(f.startswith("Goal:") for f in soft_flags):
                    summary["goal_flagged"] += 1

                instrument_type = scheme.get("instrument_type", "unknown")
                subtier = (
                    equity_subtier(scheme.get("std_deviation"))
                    if instrument_type == "equity" else None
                )
                eligible.append(
                    SchemeInfo(
                        id=scheme["id"],
                        name=scheme["name"],
                        category=scheme.get("category"),
                        instrument_type=instrument_type,
                        risk_rating=scheme.get("risk_rating"),
                        risk_score_1_to_6=scheme.get("risk_score_1_to_6"),
                        riskometer=scheme.get("riskometer"),
                        aum_cr=scheme.get("aum_cr"),
                        nav_regular_growth=scheme.get("nav_regular_growth"),
                        expense_ratio_regular=scheme.get("expense_ratio_regular"),
                        min_investment=scheme.get("min_investment"),
                        suitable_for=list(scheme.get("suitable_for") or []),
                        benchmark=scheme.get("benchmark"),
                        holdings_count=scheme.get("holdings_count", 0),
                        subtier=subtier,
                        risk_flag=(" ".join(soft_flags) if soft_flags else None),
                        risk_flag_level=flag_level,
                    )
                )
            else:
                filtered_out.append(
                    FilteredOut(
                        scheme_id=scheme["id"],
                        scheme_name=scheme["name"],
                        reasons=hard_reasons,
                    )
                )
                # Tally each distinct rule bucket that fired for this scheme.
                # A scheme failing both age and goal increments both counters —
                # by_x counters count "how many schemes this rule blocked",
                # not "how many schemes were blocked ONLY by this rule". This
                # means the by_x values can sum to more than total_filtered.
                seen_buckets: set[str] = set()
                for reason in hard_reasons:
                    bucket = self._classify_reason(reason)
                    if bucket in summary and bucket not in seen_buckets:
                        summary[bucket] += 1
                        seen_buckets.add(bucket)

        summary["total_filtered"] = len(filtered_out)

        return FilterResult(
            eligible_schemes=eligible,
            filtered_out=filtered_out,
            eligible_count=len(eligible),
            total_schemes=len(self._schemes),
            filter_summary=summary,
        )
