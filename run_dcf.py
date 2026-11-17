"""Run a DCF for one company using its assumptions file.

Usage: python run_dcf.py AAPL
       python run_dcf.py AAPL assumptions/AAPL_bull.toml

Reads assumptions/AAPL.toml unless another file is given. Needs internet for the risk-free rate,
credit spread and beta, unless they are set in the assumptions file.
"""
import math
import sys
import textwrap
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


def num(value):
    return "n/a" if math.isnan(value) else f"{value:.2f}"


def print_note(text, indent=15):
    """Print a source note indented under its label, wrapping long lines.

    Notes can be one line or several (written with triple quotes in the TOML file).
    """
    for line in text.strip().splitlines():
        print(textwrap.fill(line, width=110, initial_indent=" " * indent,
                            subsequent_indent=" " * indent))


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
try:
    with open(path, "rb") as file:
        a = tomllib.load(file)
except tomllib.TOMLDecodeError as error:
    sys.exit(f"Could not read {path}: {error}")
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
rnd_pct = ops.get("rnd_pct_revenue")  # None means R&D is expensed, not capitalized
rnd_life = ops.get("rnd_life", 5)
rnd_history = tuple(history["rnd"].dropna().tail(rnd_life))
if rnd_pct is not None and len(rnd_history) < rnd_life:
    sys.exit(f"Capitalizing R&D over {rnd_life} years needs {rnd_life} years of R&D history, "
             f"but only {len(rnd_history)} were found. Lower rnd_life or remove rnd_pct_revenue.")
basis = "R&D capitalized" if rnd_pct is not None else "R&D expensed"

inputs = DCFInputs(
    base_revenue=latest["revenue"],
    base_margin=latest["operating_margin"],
    base_year_end=history.index[-1],
    growth_rates=growth_path(growth["near_term"], growth["terminal"], a["horizon_years"]),
    ebit_margin_target=ops["ebit_margin_target"],
    tax_rate=ops["tax_rate"],
    d_and_a_pct=ops["d_and_a_pct_revenue"],
    capex_pct=ops.get("capex_pct_revenue", 0.0),
    nwc_pct=ops.get("nwc_pct_revenue_change", 0.0),
    wacc=w["wacc"],
    terminal_growth=growth["terminal"],
    terminal_roic=growth["terminal_roic"],
    valuation_date=pd.Timestamp(price["date"]),
    net_claims=sum(items.values()),
    shares=shares["value"],
    rnd_pct=rnd_pct,
    rnd_life=rnd_life,
    rnd_history=rnd_history,
    sales_to_capital=ops.get("sales_to_capital"),
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
      f"terminal {pct(growth['terminal'])}, return on new capital {pct(growth['terminal_roic'])} ({basis})")
print_note(growth.get("source", ""))
if "sales_to_capital" in ops:
    reinvestment_text = (f"reinvestment = extra revenue / {ops['sales_to_capital']:.2f} "
                         f"(sales-to-capital, {basis})")
else:
    reinvestment_text = (f"capex {pct(ops['capex_pct_revenue'])} of revenue, working capital "
                         f"{pct(ops['nwc_pct_revenue_change'])} of revenue increase")
print(f"  Operations   EBIT margin to {pct(ops['ebit_margin_target'])}, tax {pct(ops['tax_rate'])}, "
      f"D&A {pct(ops['d_and_a_pct_revenue'])}, {reinvestment_text}")
if rnd_pct is not None:
    print(f"               R&D {pct(rnd_pct)} of revenue, capitalized over {rnd_life} years")
else:
    print("               R&D expensed (add rnd_pct_revenue to capitalize it)")
print_note(ops.get("source", ""))
print(f"  Last 3 years EBIT margin {pct(ref['ebit_margin'])}, D&A {pct(ref['d_and_a_pct'])}, "
      f"capex {pct(ref['capex_pct'])}, tax rate {pct(ref['tax_rate'])}, R&D {pct(ref['rnd_pct'])}")
print(f"  ROIC         last year {pct(ref['roic'])} as reported, {pct(ref['roic_adjusted'])} R&D capitalized; "
      f"on new capital over 5 years {pct(ref['roic_new'])} / {pct(ref['roic_new_adjusted'])}")
print(f"  Sales-to-capital on new capital over 5 years {num(ref['sales_to_capital_new'])} as reported, "
      f"{num(ref['sales_to_capital_new_adjusted'])} R&D capitalized\n")

print("Discount rate")
print(f"  Risk-free rate         {pct(risk_free):>6}   {risk_free_source}")
print(f"  Beta                   {beta:>6.2f}   {beta_source}")
print(f"  Equity risk premium    {pct(rates['equity_risk_premium']):>6}")
print_note(rates.get("source", ""), indent=4)
print(f"  Cost of equity         {pct(w['cost_of_equity']):>6}")
print(f"  Pre-tax cost of debt   {pct(cost_of_debt):>6}   {debt_source}")
print(f"                                  historical interest rate: {pct(ref['interest_rate'])}")
print(f"  Debt weight            {pct(w['debt_weight']):>6}")
print(f"  WACC                   {pct(w['wacc']):>6}\n")

f = result["forecast"]
rows = {
    "revenue": f["revenue"] / 1e9,
    "growth (%)": f["growth"] * 100,
    "EBIT margin (%)": f["ebit_margin"] * 100,
    "NOPAT": f["nopat"] / 1e9,
}
if rnd_pct is not None:
    rows["+ R&D"] = f["rnd"] / 1e9
    rows["- R&D amortization"] = f["rnd_amortization"] / 1e9
    rows["= NOPAT, R&D capitalized"] = f["nopat_adjusted"] / 1e9
if inputs.sales_to_capital is None:
    rows["+ D&A"] = f["d_and_a"] / 1e9
    rows["- capex"] = f["capex"] / 1e9
    rows["- working capital"] = f["nwc_change"] / 1e9
    rows["= FCFF"] = f["fcff"] / 1e9
    rows[f"reinvestment ({basis})"] = f["reinvestment"] / 1e9
else:
    rows[f"- reinvestment ({basis})"] = f["reinvestment"] / 1e9
    rows["= FCFF"] = f["fcff"] / 1e9
    if rnd_pct is not None:
        rows["  of which net R&D"] = (f["rnd"] - f["rnd_amortization"]) / 1e9
        rows["  of which capex beyond D&A and working capital"] = f["net_capex_and_wc"] / 1e9
rows["return on new capital (%)"] = f["implied_roic"] * 100
table = pd.DataFrame(rows).T
# Format as text so each row can have its own number of decimals.
text = table.map(lambda x: "n/a" if pd.isna(x) else f"{x:,.1f}")
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
      f"terminal {pct(result['terminal_reinvestment_rate'])} ({basis})")

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