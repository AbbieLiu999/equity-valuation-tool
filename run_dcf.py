"""Run a DCF for one company using its assumptions file.

Usage: python run_dcf.py AAPL
       python run_dcf.py AAPL assumptions/AAPL_bull.toml

Reads assumptions/AAPL.toml unless another file is given. Needs internet for the risk-free rate,
credit spread and beta, unless they are set in the assumptions file.
"""
import math
import sys
import tomllib
from pathlib import Path

import pandas as pd

import db
import market_data
from dcf import (DCFInputs, calculate_wacc, growth_path, historical_references,
                 implied_growth, sanity_checks, sensitivity, validate_assumptions, value)
from metrics import add_derived_metrics, balance_sheet_bridge, is_financial


def pct(rate):
    return "n/a" if math.isnan(rate) else f"{rate:.1%}"


def use_or_fetch(assumed, fetch, label, ticker):
    """Use the value set in the assumptions file, or fetch it. Stop with a clear message on failure."""
    if assumed is not None:
        return assumed, "set in assumptions file"
    try:
        return fetch()
    except Exception as error:
        sys.exit(f"Could not fetch {label} ({error}). Set it in assumptions/{ticker}.toml instead.")


# ---------- Load assumptions and data ----------
ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL"
# An optional second argument picks another assumptions file, e.g. a bull case.
path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("assumptions") / f"{ticker}.toml"
if not path.exists():
    sys.exit(f"No assumptions for {ticker}. Copy assumptions/TEMPLATE.toml to {path} and fill it in.")
with open(path, "rb") as file:
    a = tomllib.load(file)
try:
    validate_assumptions(a)
except ValueError as error:
    sys.exit(str(error))

history = add_derived_metrics(db.load_financials(ticker))
company = db.load_company(ticker)
if is_financial(company["sic"]):
    sys.exit("A free cash flow DCF doesn't apply to financial companies like banks and insurers.")
price = db.load_latest_price(ticker)
shares = db.load_latest(ticker, "shares_outstanding")
if price is None or shares is None:
    sys.exit("No share price or share count stored yet. Run update_data.py first.")

latest = history.iloc[-1]
growth, ops, rates = a["growth"], a["operations"], a["discount_rate"]

# ---------- Discount rate ----------
risk_free, risk_free_source = use_or_fetch(
    rates.get("risk_free_rate"), market_data.get_risk_free_rate, "the risk-free rate", ticker)
beta, beta_source = use_or_fetch(
    rates.get("beta"), lambda: market_data.get_beta(ticker), "beta", ticker)
if "pretax_cost_of_debt" in rates:
    cost_of_debt, debt_source = rates["pretax_cost_of_debt"], "set in assumptions file"
else:
    spread, spread_source = use_or_fetch(
        None, lambda: market_data.get_credit_spread(rates["credit_rating"]), "the credit spread", ticker)
    cost_of_debt = risk_free + spread
    debt_source = f"risk-free rate + {rates['credit_rating']} spread of {spread:.2%} ({spread_source})"

items, missing = balance_sheet_bridge(latest)
debt = items["long_term_debt"] + items["short_term_debt"] + items["finance_lease_liabilities"]
market_cap = price["close"] * shares["value"]
w = calculate_wacc(risk_free, beta, rates["equity_risk_premium"], cost_of_debt,
                   ops["tax_rate"], market_cap, debt)

# ---------- Run the model ----------
inputs = DCFInputs(
    base_revenue=latest["revenue"],
    base_margin=latest["operating_margin"],
    base_year_end=history.index[-1],
    growth_rates=growth_path(growth["near_term"], growth["terminal"], a["horizon_years"]),
    ebit_margin_target=ops["ebit_margin_target"],
    tax_rate=ops["tax_rate"],
    d_and_a_pct=ops["d_and_a_pct_revenue"],
    capex_pct=ops["capex_pct_revenue"],
    nwc_pct=ops["nwc_pct_revenue_change"],
    wacc=w["wacc"],
    terminal_growth=growth["terminal"],
    terminal_roic=growth["terminal_roic"],
    valuation_date=pd.Timestamp(price["date"]),
    net_claims=sum(items.values()),
    shares=shares["value"],
)
try:
    result = value(inputs)
except ValueError as error:
    sys.exit(str(error))
ref = historical_references(history)

# ---------- Print ----------
print(f"{company['name']}: DCF valuation")
print(f"Valuation date {price['date']}, price {price['close']:.2f} USD. Amounts in USD billions.\n")

print(f"Assumptions ({path})")
print(f"  Horizon      {a['horizon_years']} years. {a.get('horizon_source', '')}")
print(f"  Growth       near term {', '.join(pct(g) for g in growth['near_term'])}; "
      f"terminal {pct(growth['terminal'])}, return on new capital {pct(growth['terminal_roic'])}")
print(f"               {growth.get('source', '')}")
print(f"  Operations   EBIT margin to {pct(ops['ebit_margin_target'])}, tax {pct(ops['tax_rate'])}, "
      f"D&A {pct(ops['d_and_a_pct_revenue'])}, capex {pct(ops['capex_pct_revenue'])} of revenue, "
      f"working capital {pct(ops['nwc_pct_revenue_change'])} of revenue increase")
print(f"               {ops.get('source', '')}")
print(f"  Last 3 years EBIT margin {pct(ref['ebit_margin'])}, D&A {pct(ref['d_and_a_pct'])}, "
      f"capex {pct(ref['capex_pct'])}, tax rate {pct(ref['tax_rate'])}\n")

print("Discount rate")
print(f"  Risk-free rate         {pct(risk_free):>6}   {risk_free_source}")
print(f"  Beta                   {beta:>6.2f}   {beta_source}")
print(f"  Equity risk premium    {pct(rates['equity_risk_premium']):>6}   {rates.get('source', '')}")
print(f"  Cost of equity         {pct(w['cost_of_equity']):>6}")
print(f"  Pre-tax cost of debt   {pct(cost_of_debt):>6}   {debt_source}")
print(f"                                  historical interest rate: {pct(ref['interest_rate'])}")
print(f"  Debt weight            {pct(w['debt_weight']):>6}")
print(f"  WACC                   {pct(w['wacc']):>6}\n")

f = result["forecast"]
table = pd.DataFrame({
    "revenue": f["revenue"] / 1e9,
    "growth (%)": f["growth"] * 100,
    "EBIT margin (%)": f["ebit_margin"] * 100,
    "NOPAT": f["nopat"] / 1e9,
    "+ D&A": f["d_and_a"] / 1e9,
    "- capex": f["capex"] / 1e9,
    "- working capital": f["nwc_change"] / 1e9,
    "= FCFF": f["fcff"] / 1e9,
}).T
# Format as text so each row can have its own number of decimals.
text = table.map(lambda x: f"{x:,.1f}")
text.loc["discount factor"] = [f"{x:.3f}" for x in f["discount_factor"]]
print("Forecast")
print(text.to_string())

debt_like = sum(v for v in items.values() if v > 0)
cash_like = -sum(v for v in items.values() if v < 0)
upside = result["per_share"] / price["close"] - 1
print("\nValuation")
print(f"  PV of forecast cash flows   {result['pv_forecast'] / 1e9:>10,.1f}")
print(f"  PV of terminal value        {result['pv_terminal'] / 1e9:>10,.1f}"
      f"   ({result['terminal_share']:.0%} of enterprise value)")
print(f"  = Enterprise value          {result['enterprise_value'] / 1e9:>10,.1f}")
print(f"  - Debt, leases, minority    {debt_like / 1e9:>10,.1f}")
print(f"  + Cash and securities       {cash_like / 1e9:>10,.1f}")
print(f"  = Equity value              {result['equity_value'] / 1e9:>10,.1f}")
print(f"\n  Value per share   {result['per_share']:.2f} USD vs price {price['close']:.2f} USD"
      f"  ({upside:+.1%})")
print(f"  Implied terminal EV/EBITDA   {result['implied_exit_multiple']:.1f}x")
print(f"  Share of NOPAT reinvested     final forecast year {pct(result['final_year_reinvestment_rate'])}, "
      f"terminal {pct(result['terminal_reinvestment_rate'])}")

grid = sensitivity(inputs)
grid.index = [f"WACC {w_:.1%}" for w_ in grid.index]
grid.columns = [f"g {g_:.1%}" for g_ in grid.columns]
print("\nSensitivity: value per share (USD), WACC down the side, terminal growth across")
print(grid.map(lambda x: "n/a" if math.isnan(x) else f"{x:,.0f}").to_string())

implied = implied_growth(inputs, price["close"])
print("\nReverse DCF")
if implied is None:
    print("  The implied growth rate is outside the range searched (-50% to +100% a year).")
else:
    print(f"  To justify {price['close']:.2f} USD, revenue would need to grow about {implied:.1%} a year "
          f"for {a['horizon_years']} years,")
    print("  keeping your margin, reinvestment and terminal assumptions.")

warnings = sanity_checks(inputs, result, risk_free)
if missing:
    warnings.append(f"No balance sheet data found, counted as 0: {', '.join(missing)}")
if warnings:
    print("\nChecks")
    for warning in warnings:
        print(f"  - {warning}")