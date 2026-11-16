"""Show a company's financials, key metrics and valuation from the local database.

Usage: python show.py MSFT
"""
import math
import sys

import db
from metrics import add_derived_metrics, calculate_valuation, is_financial

AMOUNTS = ["revenue", "ebitda", "net_income", "operating_cash_flow", "capex", "fcf"]
RATIOS = [
    "revenue_growth",
    "operating_margin",
    "ebitda_margin",
    "net_margin",
    "fcf_margin",
    "fcf_conversion",
]
BRIDGE_LABELS = {
    "market_cap": "Market cap",
    "long_term_debt": "+ Long-term debt",
    "short_term_debt": "+ Short-term debt",
    "finance_lease_liabilities": "+ Finance leases",
    "minority_interest": "+ Minority interest",
    "cash": "- Cash",
    "investments": "- Marketable securities",
}


def fmt(value, suffix=""):
    """Format a number with one decimal, or "n/a" if it's missing."""
    return "n/a" if math.isnan(value) else f"{value:.1f}{suffix}"


ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
raw = db.load_financials(ticker)
company = db.load_company(ticker)
print(f"{company['name']} (SIC {company['sic']}: {company['sic_description']})")

if is_financial(company["sic"]):
    print("This is a financial company. EBITDA, free cash flow and EV-based")
    print("metrics don't apply to banks and insurers, so they are not shown.")
    sys.exit()

# Calculate on the full history first, then keep the last 5 years,
# so the oldest year shown still gets a growth rate.
full = add_derived_metrics(raw)
recent = full.tail(5)

print("\nKey figures (USD billions):")
print((recent[AMOUNTS] / 1e9).round(1).T)
print("\nRatios (%):")
print((recent[RATIOS] * 100).round(1).T)

# ---------- Valuation ----------
price = db.load_latest_price(ticker)
shares = db.load_latest(ticker, "shares_outstanding")
if price is None or shares is None:
    print("\nNo share price or share count stored yet. Run update_data.py to fetch them.")
    sys.exit()

result = calculate_valuation(full.iloc[-1], price["close"], shares["value"])
print(f"\nValuation (USD billions)")
print(f"  Price {price['close']:.2f} USD on {price['date']} ({price['source']}), "
      f"{shares['value'] / 1e9:.2f}B shares as of {shares['period_end']}")
print(f"  Balance sheet and earnings: fiscal year ending {full.index[-1].date()}\n")

# The +/- signs are in the labels, so show every amount as a positive number.
for name, value in result["bridge"].items():
    print(f"  {BRIDGE_LABELS[name]:<26}{abs(value) / 1e9:>10,.1f}")
print(f"  {'= Enterprise value':<26}{result['enterprise_value'] / 1e9:>10,.1f}\n")

m = result["multiples"]
print(f"  EV/EBITDA    {fmt(m['ev_to_ebitda'], 'x')}")
print(f"  EV/EBIT      {fmt(m['ev_to_ebit'], 'x')}")
print(f"  EV/Revenue   {fmt(m['ev_to_revenue'], 'x')}")
print(f"  P/E          {fmt(m['pe'], 'x')}")
print(f"  FCF yield    {fmt(m['fcf_yield'] * 100, '%')}")

if result["missing"]:
    print(f"\n  No data found, counted as 0: {', '.join(result['missing'])}")