"""
Phase-4 PDF reports.

The report is generated from the SAME service calls the API serves, so a
report can never disagree with the dashboard the investor just looked at. It
is not a separate "reporting pipeline" with its own copy of the maths.

Generation runs through the async service layer and writes a real PDF to
`settings.REPORTS_DIR`. The row is created `pending` first and flipped to
`completed`/`failed` at the end, so a crashed worker leaves an auditable
failed row rather than a silently missing file.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.errors import NotFoundError, PermissionError_
from backend.models import InvestorProfile, Report, SchemeSelection, User
from backend.services import analysis_service

logger = logging.getLogger(__name__)


async def create_report(
    db: AsyncSession,
    user: User,
    profile: InvestorProfile,
    selection: SchemeSelection,
) -> Report:
    report = Report(
        user_id=user.id,
        status="generating",
        scheme_ids=list(selection.selected_scheme_ids),
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    try:
        overlap = await analysis_service.compute_overlap(selection)
        plos = await analysis_service.compute_plos(selection)
        risk = await analysis_service.compute_risk(selection)

        path = _render_pdf(
            report_id=report.id,
            user=user,
            profile=profile,
            selection=selection,
            overlap=overlap,
            plos=plos,
            risk=risk,
        )
        report.file_path = str(path)
        report.file_size_bytes = path.stat().st_size
        report.status = "completed"
    except Exception as e:
        logger.exception("Report generation failed for user %s", user.id)
        report.status = "failed"
        # Truncated: an engine traceback can be long, and the column is a
        # user-visible field, not a log sink.
        report.error_message = f"{type(e).__name__}: {e}"[:500]

    await db.commit()
    await db.refresh(report)
    return report


def _render_pdf(
    *,
    report_id: uuid.UUID,
    user: User,
    profile: InvestorProfile,
    selection: SchemeSelection,
    overlap: dict[str, Any],
    plos: dict[str, Any],
    risk: dict[str, Any],
) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    out_dir = Path(settings.REPORTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report_id}.pdf"

    styles = getSampleStyleSheet()
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]
    body = styles["BodyText"]
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.grey)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        title=f"Portfolio Overlap & Risk Report - {profile.full_name or user.email}",
        author="Nexus Portfolio Intelligence",
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    def table(rows: list[list[str]], widths: list[float] | None = None) -> Table:
        t = Table(rows, colWidths=widths, hAlign="LEFT")
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B3D6B")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F6FA")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return t

    story: list[Any] = []
    story.append(Paragraph("Portfolio Overlap &amp; Risk Report", h1))
    story.append(
        Paragraph(
            f"Prepared for <b>{profile.full_name or user.email}</b> &nbsp;|&nbsp; "
            f"Generated {datetime.now(UTC):%d %b %Y %H:%M UTC}",
            small,
        )
    )
    story.append(Spacer(1, 8))

    # ── Investor profile ──────────────────────────────────────────────────────
    story.append(Paragraph("Investor Profile", h2))
    story.append(
        table(
            [
                ["Field", "Value"],
                ["Age", str(profile.age)],
                ["Occupation", profile.occupation or "-"],
                ["Monthly income", f"Rs {profile.monthly_income:,}" if profile.monthly_income else "-"],
                ["Amount invested", f"Rs {profile.investment_amount:,}" if profile.investment_amount else "-"],
                ["Goal", (profile.goal or "-").replace("_", " ").title()],
                ["Risk appetite", (profile.risk_appetite or "-").replace("_", " ").title()],
                ["Target retirement age", str(profile.target_retirement_age)],
                ["Years to retirement", str(profile.years_to_retirement)],
            ],
            widths=[60 * mm, 100 * mm],
        )
    )
    story.append(Spacer(1, 10))

    # ── Selected portfolio ────────────────────────────────────────────────────
    story.append(Paragraph("Selected Portfolio", h2))
    weights = selection.weights or {}
    rows = [["Scheme", "Weight"]]
    for node in overlap.get("nodes", []):
        w = weights.get(node["id"], 0.0)
        rows.append([node["name"], f"{w * 100:.1f}%"])
    story.append(table(rows, widths=[125 * mm, 35 * mm]))
    story.append(Spacer(1, 10))

    # ── Overlap ───────────────────────────────────────────────────────────────
    story.append(Paragraph("Holdings Overlap", h2))
    story.append(
        Paragraph(
            f"Average pairwise Jaccard overlap across the selection: "
            f"<b>{overlap.get('avg_pairwise_jaccard', 0):.1%}</b>. "
            f"Edges are shown above a {overlap.get('overlap_threshold', 0):.0%} threshold.",
            body,
        )
    )
    edges = sorted(
        overlap.get("edges", []), key=lambda e: e["jaccard_score"], reverse=True
    )[:12]
    if edges:
        rows = [["Scheme A", "Scheme B", "Jaccard", "Weighted", "Shared"]]
        for e in edges:
            rows.append(
                [
                    e["source_id"],
                    e["target_id"],
                    f"{e['jaccard_score']:.1%}",
                    f"{e['weighted_overlap']:.1%}",
                    str(e["shared_stocks"]),
                ]
            )
        story.append(Spacer(1, 4))
        story.append(table(rows, widths=[48 * mm, 48 * mm, 22 * mm, 22 * mm, 20 * mm]))
    else:
        story.append(
            Paragraph(
                "No pair in this selection exceeds the overlap threshold - "
                "holdings-level diversification looks healthy.",
                body,
            )
        )
    story.append(PageBreak())

    # ── PLOS ──────────────────────────────────────────────────────────────────
    story.append(Paragraph("Portfolio Level Overlap Score (PLOS)", h2))
    story.append(
        Paragraph(
            f"Portfolio PLOS: <b>{plos.get('portfolio_plos', 0):.1f}/100</b> "
            f"(diversification score {plos.get('diversification_score', 0):.1f}). "
            f"{plos.get('interpretation', '')}<br/>"
            f"Data source: <i>{plos.get('data_source', 'n/a')}</i>.",
            body,
        )
    )
    pairs = sorted(
        plos.get("pair_scores", []), key=lambda p: p["plos_score"], reverse=True
    )[:12]
    if pairs:
        rows = [["Scheme A", "Scheme B", "PLOS", "Correlation", "Quality"]]
        for p in pairs:
            corr = p.get("real_correlation")
            rows.append(
                [
                    p["scheme_a_name"][:34],
                    p["scheme_b_name"][:34],
                    f"{p['plos_score']:.1f}",
                    f"{corr:.2f}" if corr is not None else "-",
                    p.get("data_quality", "-"),
                ]
            )
        story.append(Spacer(1, 4))
        story.append(table(rows, widths=[52 * mm, 52 * mm, 18 * mm, 22 * mm, 18 * mm]))
    story.append(Spacer(1, 10))

    # ── Risk ──────────────────────────────────────────────────────────────────
    story.append(Paragraph("Risk Analytics", h2))
    story.append(
        Paragraph(
            f"Portfolio return <b>{risk.get('portfolio_annualized_return', 0):.2%}</b> | "
            f"volatility <b>{risk.get('portfolio_volatility', 0):.2%}</b> | "
            f"Sharpe <b>{risk.get('portfolio_sharpe', 0):.2f}</b> | "
            f"max drawdown <b>{risk.get('portfolio_max_drawdown', 0):.2%}</b><br/>"
            f"Daily VaR(95%) <b>{risk.get('portfolio_var_95', 0):.2%}</b> | "
            f"CVaR(95%) <b>{risk.get('portfolio_cvar_95', 0):.2%}</b><br/>"
            f"Diversification ratio <b>{risk.get('diversification_ratio', 0):.3f}</b> "
            f"(below 1.0 means the blend is genuinely reducing risk versus its parts).",
            body,
        )
    )
    metrics = risk.get("scheme_metrics", [])
    if metrics:
        rows = [["Scheme", "Return", "Vol", "Sharpe", "MaxDD", "VaR95", "Synthetic"]]
        for m in metrics:
            rows.append(
                [
                    m["scheme_name"][:38],
                    f"{m['annualized_return']:.1%}",
                    f"{m['annualized_volatility']:.1%}",
                    f"{m['sharpe_ratio']:.2f}",
                    f"{m['max_drawdown']:.1%}",
                    f"{m['var_95_parametric']:.2%}",
                    "yes" if m["is_synthetic"] else "no",
                ]
            )
        story.append(Spacer(1, 4))
        story.append(
            table(
                rows,
                widths=[54 * mm, 18 * mm, 16 * mm, 16 * mm, 18 * mm, 20 * mm, 18 * mm],
            )
        )

    story.append(Spacer(1, 12))
    synthetic_any = any(m.get("is_synthetic") for m in metrics)
    story.append(
        Paragraph(
            "<b>Data provenance.</b> Figures are computed from the schemes you "
            "actually selected, using the real-data fallback chain (AMFI NAV history "
            "&rarr; yfinance holdings reconstruction &rarr; duration/YTM debt model &rarr; "
            "calibrated synthetic). "
            + (
                "One or more schemes in this report fell back to a modelled return "
                "series because live market data was unavailable; those rows are "
                "marked 'Synthetic = yes' above and should be read as estimates."
                if synthetic_any
                else "All schemes in this report used non-synthetic return series."
            ),
            small,
        )
    )
    story.append(
        Paragraph(
            "This document is generated for informational purposes and is not "
            "investment advice. Mutual fund investments are subject to market risks; "
            "read all scheme-related documents carefully.",
            small,
        )
    )

    doc.build(story)
    return path


async def get_report(db: AsyncSession, user: User, report_id: uuid.UUID) -> Report:
    report = await db.get(Report, report_id)
    if report is None:
        raise NotFoundError("Report not found.")
    if report.user_id != user.id:
        # Reports contain the investor's income, corpus, and holdings. A
        # guessable-UUID IDOR here would be a direct PII leak.
        raise PermissionError_("This report does not belong to you.")
    return report


async def list_reports(db: AsyncSession, user: User) -> list[Report]:
    result = await db.execute(
        select(Report)
        .where(Report.user_id == user.id)
        .order_by(Report.created_at.desc())
    )
    return list(result.scalars())
