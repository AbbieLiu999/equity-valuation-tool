"""Fetch and clean annual financial data from SEC EDGAR."""
from datetime import date

import requests

# Replace with your own name and email (SEC requires contact info).
HEADERS = {"User-Agent": "Your Name your.email@example.com"}

# Each metric maps to a list of XBRL tags, in priority order.
# Companies use different tags, and sometimes switch tags over the years.
METRIC_TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "d_and_a": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
    ],
    "depreciation": ["Depreciation"],
    "amortization": [
        "AmortizationOfIntangibleAssets",
        "FiniteLivedIntangibleAssetsAmortizationExpense",
    ],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    # Balance sheet items for the enterprise value bridge
    "short_term_investments": [
        "MarketableSecuritiesCurrent",
        "ShortTermInvestments",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "DebtSecuritiesCurrent",
    ],
    "long_term_investments": [
        "MarketableSecuritiesNoncurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
    ],
    "long_term_debt": ["LongTermDebt"],  # total, including the current portion
    "long_term_debt_noncurrent": ["LongTermDebtNoncurrent"],
    "long_term_debt_current": ["LongTermDebtCurrent"],
    "short_term_debt": ["CommercialPaper", "ShortTermBorrowings"],
    "finance_lease_liabilities": ["FinanceLeaseLiability"],
    "minority_interest": ["MinorityInterest"],
}


def get_cik(ticker):
    """Look up a company's 10-digit CIK from its stock ticker."""
    url = "https://www.sec.gov/files/company_tickers.json"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    for company in response.json().values():
        if company["ticker"] == ticker.upper():
            return str(company["cik_str"]).zfill(10)
    raise ValueError(f"Ticker not found: {ticker}")


def get_company_info(ticker, cik):
    """Get basic company details: name, industry (SIC code), fiscal year end."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()
    return {
        "cik": cik,
        "ticker": ticker.upper(),
        "name": data["name"],
        "sic": data["sic"],
        "sic_description": data["sicDescription"],
        "fiscal_year_end": data["fiscalYearEnd"],
    }


def get_company_facts(cik):
    """Download all XBRL financial facts the company has filed."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def is_annual(entry):
    """True if the entry can be used as annual data.

    Income and cash flow items cover a period, so they must come from a 10-K
    and span about a year. Balance sheet items are a single date, so the date
    alone identifies them: they're also accepted from 10-Qs, because a later
    quarterly report sometimes repeats the fiscal year end balance under a new
    tag (NVIDIA's marketable securities, for example). Quarter-end balances are
    dropped later, when load_financials keeps only fiscal year ends.
    """
    if "start" not in entry:
        return entry["form"].startswith(("10-K", "10-Q"))
    if not entry["form"].startswith("10-K"):
        return False
    start = date.fromisoformat(entry["start"])
    end = date.fromisoformat(entry["end"])
    return 340 <= (end - start).days <= 390


def get_annual_values(facts, tags):
    """Return {period_end: record} for one metric, merging several possible tags.

    Each record holds the value plus where it came from (tag and filing).
    """
    us_gaap = facts["facts"].get("us-gaap", {})
    values = {}
    for tag in tags:
        entries = us_gaap.get(tag, {}).get("units", {}).get("USD", [])
        annual = [e for e in entries if is_annual(e)]
        # Oldest filing first, so later filings (restatements) overwrite earlier ones.
        annual.sort(key=lambda e: e["filed"])
        tag_values = {}
        for e in annual:
            tag_values[e["end"]] = {
                "value": e["val"],
                "source_tag": tag,
                "accn": e["accn"],
                "filed": e["filed"],
            }
        # A higher-priority tag wins; lower-priority tags only fill gaps.
        for end, record in tag_values.items():
            values.setdefault(end, record)
    return values


def get_latest_shares(facts):
    """Return the most recent share count from a 10-K or 10-Q cover page, or None.

    This comes from the "dei" taxonomy (cover page information), not us-gaap,
    and its date is the cover page date, not a fiscal year end.
    """
    dei = facts["facts"].get("dei", {})
    entries = dei.get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    entries = [e for e in entries if e["form"].startswith(("10-K", "10-Q"))]
    if not entries:
        return None
    latest = max(entries, key=lambda e: (e["end"], e["filed"]))
    return {
        "period_end": latest["end"],
        "value": latest["val"],
        "source_tag": "EntityCommonStockSharesOutstanding",
        "accn": latest["accn"],
        "filed": latest["filed"],
    }


def get_financial_records(cik):
    """Fetch every metric for one company as a list of rows (long format)."""
    facts = get_company_facts(cik)
    rows = []
    for metric, tags in METRIC_TAGS.items():
        for end, record in get_annual_values(facts, tags).items():
            rows.append({"cik": cik, "period_end": end, "metric": metric, **record})
    shares = get_latest_shares(facts)
    if shares:
        rows.append({"cik": cik, "metric": "shares_outstanding", **shares})
    return rows


if __name__ == "__main__":
    rows = get_financial_records(get_cik("AAPL"))
    print(f"Apple: {len(rows)} rows. First three:")
    for row in rows[:3]:
        print(row)