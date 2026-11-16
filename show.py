"""Show a company's financials and key metrics from the local database.

Usage: python show.py MSFT
"""
import sys

import db
from metrics import add_derived_metrics, is_financial

AMOUNTS = ["revenue", "ebitda", "net_income", "operating_cash_flow", "capex", "fcf"]
RATIOS = [
    "revenue_growth",
    "operating_margin",
    "ebitda_margin",
    "net_margin",
    "fcf_margin",
    "fcf_conversion",
]

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
df = add_derived_metrics(raw).tail(5)

print("\nKey figures (USD billions):")
print((df[AMOUNTS] / 1e9).round(1).T)
print("\nRatios (%):")
print((df[RATIOS] * 100).round(1).T)