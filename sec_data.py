"""Fetch and clean annual financial data from SEC EDGAR."""
from datetime import date

import pandas as pd
import requests

# Replace with your own name and email (SEC requires contact info).
HEADERS = {"User-Agent": "Dan Liu abbieliu666@gmail.com"}

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


def get_company_facts(cik):
    """Download all XBRL financial facts the company has filed."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def is_annual(entry):
    """True if the entry comes from a 10-K and covers a full year.

    Balance sheet items (like cash) are a single point in time, so they
    have no "start" date and are always kept.
    """
    if not entry["form"].startswith("10-K"):
        return False
    if "start" not in entry:
        return True
    start = date.fromisoformat(entry["start"])
    end = date.fromisoformat(entry["end"])
    return 340 <= (end - start).days <= 390


def get_annual_values(facts, tags):
    """Return {period_end: value} for one metric, merging several possible tags."""
    us_gaap = facts["facts"].get("us-gaap", {})
    values = {}
    for tag in tags:
        entries = us_gaap.get(tag, {}).get("units", {}).get("USD", [])
        annual = [e for e in entries if is_annual(e)]
        # Oldest filing first, so later filings (restatements) overwrite earlier ones.
        annual.sort(key=lambda e: e["filed"])
        tag_values = {}
        for e in annual:
            tag_values[e["end"]] = e["val"]
        # A higher-priority tag wins; lower-priority tags only fill gaps.
        for end, val in tag_values.items():
            values.setdefault(end, val)
    return values


def get_financials(ticker):
    """Build a table of annual financials: one row per fiscal year."""
    facts = get_company_facts(get_cik(ticker))
    data = {metric: get_annual_values(facts, tags) for metric, tags in METRIC_TAGS.items()}
    df = pd.DataFrame(data)
    df.index = pd.to_datetime(df.index)
    df.index.name = "period_end"
    # Some companies (like Microsoft) don't report total D&A as one tag,
    # so fill the gap by adding depreciation and intangible amortization.
    components = df["depreciation"] + df["amortization"]
    df["d_and_a"] = df["d_and_a"].fillna(components)
    # Keep only fiscal year ends (rows where revenue exists), oldest to newest.
    return df.sort_index().dropna(subset=["revenue"])


if __name__ == "__main__":
    df = get_financials("NVDA")
    print("NVIDIA, last 5 fiscal years (USD billions):")
    print((df.tail(5) / 1e9).round(1).T)