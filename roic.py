"""Show a company's return on invested capital, as reported and with R&D treated as investment.

Usage: python roic.py AAPL

Useful for setting terminal_roic in the DCF assumptions.
"""
import math
import sys

import db
from metrics import add_derived_metrics, add_return_metrics, incremental_roic, is_financial


def pct(rate):
    return "n/a" if math.isnan(rate) else f"{rate:.1%}"


ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL"
history = add_return_metrics(add_derived_metrics(db.load_financials(ticker)))
company = db.load_company(ticker)
if is_financial(company["sic"]):
    sys.exit("ROIC on operating capital doesn't apply to financial companies like banks.")

recent = history.tail(5)
amounts = (recent[["nopat", "invested_capital", "rnd", "research_asset",
                   "nopat_adjusted", "invested_capital_adjusted"]] / 1e9).round(1)
ratios = (recent[["roic", "roic_adjusted"]] * 100).round(1)

print(f"{company['name']}: return on invested capital (USD billions)\n")
print(amounts.T.to_string())
print("\nROIC (%), on average invested capital")
print(ratios.T.to_string(na_rep="n/a"))

print("\nReturn on new capital over the last 5 years (extra NOPAT / extra invested capital)")
print(f"  As reported    {pct(incremental_roic(history, 'nopat', 'invested_capital', 5))}")
print(f"  R&D-adjusted   {pct(incremental_roic(history, 'nopat_adjusted', 'invested_capital_adjusted', 5))}")
print("\n  n/a: not enough history, or invested capital didn't grow (the ratio is then meaningless).")
