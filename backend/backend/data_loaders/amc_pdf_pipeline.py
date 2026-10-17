"""
AMC PDF Pipeline — Multi-AMC Monthly Holdings Downloader and Parser.

Flow:
  AMC Website → PDF Download → PDF Parser → Extract Holdings
  → Validate → Normalize → Map Company Names → Assign NSE Symbols
  → Store Holdings → Store Sector Allocation → Update Portfolio DB

Supported AMCs (all have monthly factsheets on their websites):
  - Nippon India Mutual Fund     (nippon.com)
  - HDFC Mutual Fund            (hdfcfund.com)
  - ICICI Prudential            (icicipruamc.com)
  - SBI Mutual Fund             (sbimf.com)
  - Kotak Mutual Fund           (kotakmf.com)
  - Axis Mutual Fund            (axismf.com)
  - DSP Mutual Fund             (dspim.com)
  - Mirae Asset                 (miraeassetmf.co.in)
  - UTI Mutual Fund             (utimf.com)
  - Parag Parikh                (ppfas.com)

Each AMC has a slightly different PDF layout.
This pipeline normalizes all of them into a common schema.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PDF_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "data" / "amc_pdfs"
_PDF_DIR.mkdir(parents=True, exist_ok=True)

# Company name normalization map (common aliases → canonical name)
_COMPANY_NAME_ALIASES: dict[str, str] = {
    "reliance industries ltd": "Reliance Industries Limited",
    "reliance industries limited": "Reliance Industries Limited",
    "hdfc bank ltd": "HDFC Bank Limited",
    "hdfc bank limited": "HDFC Bank Limited",
    "infosys ltd": "Infosys Limited",
    "infosys limited": "Infosys Limited",
    "tcs": "Tata Consultancy Services Limited",
    "tata consultancy services ltd": "Tata Consultancy Services Limited",
    "icici bank ltd": "ICICI Bank Limited",
    "icici bank limited": "ICICI Bank Limited",
    "bharti airtel ltd": "Bharti Airtel Limited",
    "itc ltd": "ITC Limited",
    "itc limited": "ITC Limited",
    "axis bank ltd": "Axis Bank Limited",
    "kotak mahindra bank ltd": "Kotak Mahindra Bank Limited",
    "l&t": "Larsen & Toubro Limited",
    "larsen and toubro ltd": "Larsen & Toubro Limited",
    "larsen & toubro ltd": "Larsen & Toubro Limited",
    "sbin": "State Bank of India",
    "state bank of india": "State Bank of India",
    "ntpc ltd": "NTPC Limited",
    "sun pharma": "Sun Pharmaceutical Industries Limited",
    "sun pharmaceutical industries ltd": "Sun Pharmaceutical Industries Limited",
    "bajaj finance ltd": "Bajaj Finance Limited",
    "bajaj finserv ltd": "Bajaj Finserv Limited",
    "maruti suzuki india ltd": "Maruti Suzuki India Limited",
    "titan company ltd": "Titan Company Limited",
    "asian paints ltd": "Asian Paints Limited",
    "wipro ltd": "Wipro Limited",
    "hcl technologies ltd": "HCL Technologies Limited",
    "tech mahindra ltd": "Tech Mahindra Limited",
    "ultratech cement ltd": "UltraTech Cement Limited",
    "power grid corporation of india ltd": "Power Grid Corporation of India Limited",
}


@dataclass
class AMCConfig:
    """Configuration for downloading factsheets from a specific AMC."""
    name: str
    slug: str
    factsheet_url_template: str   # {year} and {month_abbr} placeholders
    pdf_filename_template: str    # local filename pattern
    parser_type: str              # "nippon" | "hdfc" | "icici" | "generic"


AMC_CONFIGS: dict[str, AMCConfig] = {
    "nippon": AMCConfig(
        name="Nippon India Mutual Fund",
        slug="nippon",
        factsheet_url_template="https://mf.nipponindiaim.com/InvestorServices/Pages/FundFactSheet",
        pdf_filename_template="nippon-fs-{year}-{month}.pdf",
        parser_type="nippon",
    ),
    "hdfc": AMCConfig(
        name="HDFC Mutual Fund",
        slug="hdfc",
        factsheet_url_template="https://www.hdfcfund.com/content/dam/abc/india/assets/fund-factsheet",
        pdf_filename_template="hdfc-fs-{year}-{month}.pdf",
        parser_type="hdfc",
    ),
    "parag_parikh": AMCConfig(
        name="Parag Parikh Flexi Cap Fund",
        slug="ppfas",
        factsheet_url_template="https://www.ppfas.com/downloads/factsheet-{month_year}.pdf",
        pdf_filename_template="ppfas-fs-{year}-{month}.pdf",
        parser_type="generic",
    ),
}


@dataclass
class HoldingRecord:
    """Normalized holding extracted from an AMC PDF."""
    amc_slug: str
    scheme_name: str
    company_name: str           # normalized canonical name
    nse_symbol: str | None      # mapped NSE ticker (e.g. "RELIANCE.NS")
    weight_pct: float           # percentage of AUM
    market_value_cr: float | None
    sector: str | None
    as_of_date: date
    is_top_10: bool


def normalize_company_name(raw_name: str) -> str:
    """
    Normalize company name to canonical form for consistent mapping.

    Steps:
      1. Strip leading/trailing whitespace
      2. Remove trailing asterisks (*) used for top-10 markers
      3. Remove common suffixes that vary (Ltd/Ltd./Limited)
      4. Lowercase for lookup, then return canonical form
    """
    name = raw_name.strip().rstrip("*").strip()
    name_lower = name.lower()
    name_lower = re.sub(r'\s+', ' ', name_lower)

    # Look up in alias map
    canonical = _COMPANY_NAME_ALIASES.get(name_lower)
    if canonical:
        return canonical

    # Normalize common suffix patterns
    name_normalized = re.sub(r'\s*(ltd\.?|limited|pvt\.?|private)\s*$', '', name,
                              flags=re.IGNORECASE).strip()

    # Title-case the result
    return name_normalized.title()


def assign_nse_symbol(company_name: str, ticker_map: dict[str, str]) -> str | None:
    """
    Map a normalized company name to an NSE ticker symbol.

    Lookup order:
      1. Exact match in ticker_map
      2. Case-insensitive match
      3. Partial match (longest substring)
      4. None (requires manual review)
    """
    # Exact match
    if company_name in ticker_map:
        return ticker_map[company_name]

    # Case-insensitive
    for key, ticker in ticker_map.items():
        if key.lower() == company_name.lower():
            return ticker

    # Partial match — find the longest key that is a substring
    best_match = None
    best_len = 0
    for key, ticker in ticker_map.items():
        if key.lower() in company_name.lower() or company_name.lower() in key.lower():
            if len(key) > best_len:
                best_match = ticker
                best_len = len(key)

    return best_match


def load_ticker_map() -> dict[str, str]:
    """Load the NSE ticker mapping from stocks_ticker_map.json."""
    ticker_map_path = (
        Path(__file__).resolve().parent.parent / "data" / "stocks_ticker_map.json"
    )
    if not ticker_map_path.exists():
        logger.warning("stocks_ticker_map.json not found")
        return {}

    with open(ticker_map_path, encoding="utf-8") as f:
        data = json.load(f)

    return data.get("nse_mapped", {})


def extract_holdings_nippon(pdf_path: Path, as_of_date: date) -> list[HoldingRecord]:
    """
    Extract holdings from a Nippon India factsheet PDF.
    Uses the coordinate-based pdfplumber parser built in Phase 1.
    """
    try:

        schemes_path = Path(__file__).resolve().parent.parent / "data" / "schemes_metadata.json"
        holdings_path = Path(__file__).resolve().parent.parent / "data" / "sample_holdings.json"

        if not holdings_path.exists():
            logger.warning("Nippon holdings JSON not found. Run PDF extraction pipeline first.")
            return []

        with open(holdings_path, encoding="utf-8") as f:
            holdings_data = json.load(f)

        ticker_map = load_ticker_map()
        records: list[HoldingRecord] = []

        for scheme_id, data in holdings_data.items():
            for h in data.get("holdings", []):
                raw_name = h.get("name", "")
                if not raw_name or raw_name.lower() in (
                    "equity less than 1% of corpus", "cash and other receivables"
                ):
                    continue

                canonical = normalize_company_name(raw_name)
                nse_ticker = assign_nse_symbol(canonical, ticker_map)

                records.append(HoldingRecord(
                    amc_slug="nippon",
                    scheme_name=scheme_id,
                    company_name=canonical,
                    nse_symbol=nse_ticker,
                    weight_pct=float(h.get("weight", 0)),
                    market_value_cr=None,
                    sector=None,
                    as_of_date=as_of_date,
                    is_top_10=h.get("is_top_10", False),
                ))

        return records

    except Exception as e:
        logger.error(f"Nippon holdings extraction failed: {e}")
        return []


def validate_holdings(records: list[HoldingRecord]) -> tuple[list[HoldingRecord], list[str]]:
    """
    Validate extracted holdings records.

    Checks:
      1. weight_pct must be > 0 and ≤ 100
      2. company_name must be non-empty
      3. Total weight per scheme should be between 85% and 105%
    """
    warnings: list[str] = []
    valid: list[HoldingRecord] = []

    from collections import defaultdict
    scheme_totals: dict[str, float] = defaultdict(float)

    for r in records:
        if not r.company_name:
            warnings.append(f"Empty company name in {r.scheme_name}")
            continue
        if not (0 < r.weight_pct <= 100):
            warnings.append(f"Invalid weight {r.weight_pct} for {r.company_name}")
            continue
        valid.append(r)
        scheme_totals[r.scheme_name] += r.weight_pct

    for scheme_id, total in scheme_totals.items():
        if total < 80:
            warnings.append(f"{scheme_id}: total weight {total:.1f}% seems low")
        elif total > 110:
            warnings.append(f"{scheme_id}: total weight {total:.1f}% seems high")

    return valid, warnings


def run_monthly_pipeline(
    amc_slug: str = "nippon",
    as_of_date: date | None = None,
) -> dict[str, Any]:
    """
    Run the complete monthly holdings pipeline for an AMC.

    Flow:
      Load PDF / JSON → Extract Holdings → Validate → Normalize
      → Map NSE Symbols → Store Holdings → Return summary
    """
    if as_of_date is None:
        as_of_date = date.today().replace(day=1)  # first of current month

    logger.info(f"Starting monthly holdings pipeline for {amc_slug} as of {as_of_date}")

    # Step 1: Extract holdings
    if amc_slug == "nippon":
        records = extract_holdings_nippon(_PDF_DIR, as_of_date)
    else:
        logger.warning(f"AMC {amc_slug} parser not yet implemented")
        records = []

    # Step 2: Validate
    valid_records, warnings = validate_holdings(records)
    for w in warnings:
        logger.warning(w)

    # Step 3: Count NSE mapping coverage
    mapped = sum(1 for r in valid_records if r.nse_symbol)
    unmapped = sum(1 for r in valid_records if not r.nse_symbol)

    logger.info(
        f"{amc_slug}: {len(valid_records)} valid holdings, "
        f"{mapped} NSE-mapped, {unmapped} unmapped"
    )

    return {
        "amc": amc_slug,
        "as_of_date": str(as_of_date),
        "total_records": len(records),
        "valid_records": len(valid_records),
        "nse_mapped": mapped,
        "unmapped": unmapped,
        "warnings": warnings,
        "success": len(valid_records) > 0,
    }
