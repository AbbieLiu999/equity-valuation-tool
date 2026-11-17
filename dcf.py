"""Discounted cash flow model.

Pure calculations: nothing here reads files or uses the internet,
so every function can be tested with small hand-made inputs.
"""
import math
from dataclasses import dataclass, replace

import pandas as pd

from metrics import (add_return_metrics, balance_sheet_bridge, incremental_roic,
                     incremental_sales_to_capital)

REQUIRED_KEYS = {
    "growth": ["near_term", "terminal", "terminal_roic"],
    "operations": ["ebit_margin_target", "tax_rate", "d_and_a_pct_revenue"],
    "discount_rate": ["equity_risk_premium"],
}


@dataclass
class DCFInputs:
    """Everything the model needs, in one place. Rates are decimals (0.05 = 5%)."""

    base_revenue: float          # revenue in the latest fiscal year (year 0)
    base_margin: float           # EBIT margin in the latest fiscal year
    base_year_end: pd.Timestamp  # end of the latest fiscal year
    growth_rates: list           # revenue growth for each forecast year
    ebit_margin_target: float    # EBIT margin reached by the final forecast year
    tax_rate: float
    d_and_a_pct: float           # D&A as a share of revenue
    capex_pct: float             # capex as a share of revenue
    nwc_pct: float               # working capital investment as a share of the revenue increase
    wacc: float
    terminal_growth: float
    terminal_roic: float         # return on new invested capital after the forecast
    valuation_date: pd.Timestamp
    net_claims: float            # debt + leases + minority interest - cash - securities
    shares: float
    # Optional: capitalize R&D (Damodaran's approach). Leave rnd_pct as None to expense R&D.
    rnd_pct: float = None        # R&D as a share of revenue in the forecast
    rnd_life: int = 5            # years over which each year's R&D is written off
    rnd_history: tuple = ()      # actual R&D in past years, oldest first (at least rnd_life years)
    # Optional: link reinvestment to growth (Damodaran's approach). When set,
    # reinvestment = extra revenue / sales_to_capital, and capex_pct and nwc_pct are not used.
    sales_to_capital: float = None


def validate_assumptions(a):
    """Check an assumptions file is complete and sensible. Raise ValueError if not."""
    problems = []
    if not isinstance(a.get("horizon_years"), int) or a["horizon_years"] < 1:
        problems.append("horizon_years must be a whole number of at least 1")
    for section, keys in REQUIRED_KEYS.items():
        for key in keys:
            if key not in a.get(section, {}):
                problems.append(f"missing {key} in [{section}]")
    # Reinvestment comes either from sales-to-capital, or from capex and working capital.
    if "sales_to_capital" not in a.get("operations", {}):
        for key in ("capex_pct_revenue", "nwc_pct_revenue_change"):
            if key not in a.get("operations", {}):
                problems.append(f"missing {key} in [operations] (or set sales_to_capital instead)")
    discount_rate = a.get("discount_rate", {})
    if "credit_rating" not in discount_rate and "pretax_cost_of_debt" not in discount_rate:
        problems.append("[discount_rate] needs either credit_rating or pretax_cost_of_debt")

    if not problems:
        if len(a["growth"]["near_term"]) > a["horizon_years"]:
            problems.append("there are more near-term growth rates than forecast years")
        # Catch "5" typed for 5%: every rate in the file should be a decimal.
        rate_keys = ["ebit_margin_target", "tax_rate", "d_and_a_pct_revenue",
                     "capex_pct_revenue", "nwc_pct_revenue_change"]
        rates = {
            "terminal growth": a["growth"]["terminal"],
            "equity_risk_premium": discount_rate["equity_risk_premium"],
            **{key: a["operations"][key] for key in rate_keys if key in a["operations"]},
        }
        for name, rate in rates.items():
            if not -1 < rate < 1:
                problems.append(f"{name} is {rate}; rates must be decimals, e.g. 0.05 for 5%")
        operations = a["operations"]
        if "sales_to_capital" in operations and not 0 < operations["sales_to_capital"] < 50:
            problems.append("sales_to_capital must be positive, e.g. 1.5 for $1.50 of extra revenue "
                            "per $1 of extra capital")
        if "rnd_pct_revenue" in operations:
            if not 0 <= operations["rnd_pct_revenue"] < 1:
                problems.append(f"rnd_pct_revenue is {operations['rnd_pct_revenue']}; "
                                "use a decimal, e.g. 0.08 for 8%")
            life = operations.get("rnd_life", 5)
            if not isinstance(life, int) or not 1 <= life <= 15:
                problems.append("rnd_life must be a whole number of years from 1 to 15")
        roic = a["growth"]["terminal_roic"]
        if not 0 < roic < 5:
            problems.append(f"terminal_roic is {roic}; use a decimal, e.g. 0.30 for 30%")
        elif roic <= a["growth"]["terminal"]:
            problems.append("terminal_roic must be above terminal growth, "
                            "otherwise growth would need all cash flow or more to be reinvested")

    if problems:
        raise ValueError("Problems in the assumptions file:\n  - " + "\n  - ".join(problems))


def growth_path(near_term, terminal, years):
    """Near-term growth rates first, then a straight-line fade down to the terminal rate."""
    path = list(near_term)
    last = path[-1] if path else terminal
    remaining = years - len(path)
    for step in range(1, remaining + 1):
        path.append(last + (terminal - last) * step / remaining)
    return path


def rnd_amortization(history, forecast, life):
    """Amortization of capitalized R&D in each forecast year.

    Each year's R&D is written off evenly over the following `life` years, so a
    year's amortization is 1/life of each of the previous `life` years' R&D.
    """
    if len(history) < life:
        raise ValueError(
            f"Capitalizing R&D over {life} years needs at least {life} years of R&D history, "
            f"but only {len(history)} were found."
        )
    spending = list(history) + list(forecast)
    start = len(history)
    return [sum(spending[i - life:i]) / life for i in range(start, start + len(forecast))]


def build_forecast(inputs):
    """Project revenue and free cash flow to the firm (FCFF) for each forecast year."""
    years = len(inputs.growth_rates)
    revenue = inputs.base_revenue
    rows = []
    for i, growth in enumerate(inputs.growth_rates, start=1):
        prior_revenue = revenue
        revenue = prior_revenue * (1 + growth)
        # The EBIT margin moves in a straight line from today's level to the target.
        margin = inputs.base_margin + (inputs.ebit_margin_target - inputs.base_margin) * i / years
        ebit = revenue * margin
        nopat = ebit * (1 - inputs.tax_rate)
        d_and_a = revenue * inputs.d_and_a_pct
        capex = revenue * inputs.capex_pct
        nwc_change = (revenue - prior_revenue) * inputs.nwc_pct
        rows.append({
            "year_end": inputs.base_year_end + pd.DateOffset(years=i),
            "revenue": revenue,
            "growth": growth,
            "ebit_margin": margin,
            "ebit": ebit,
            "nopat": nopat,
            "d_and_a": d_and_a,
            "capex": capex,
            "nwc_change": nwc_change,
            "fcff": nopat + d_and_a - capex - nwc_change,
        })
    forecast = pd.DataFrame(rows)
    forecast.index = [f"FY{end.year}" for end in forecast["year_end"]]

    # The NOPAT that reinvestment earns a return on. When R&D is capitalized
    # (Damodaran's approach), net R&D (spending minus amortization) is added to it.
    forecast["nopat_adjusted"] = forecast["nopat"]
    net_rnd = 0.0
    if inputs.rnd_pct is not None:
        forecast["rnd"] = forecast["revenue"] * inputs.rnd_pct
        amortization = rnd_amortization(inputs.rnd_history, forecast["rnd"], inputs.rnd_life)
        forecast["rnd_amortization"] = pd.Series(amortization, index=forecast.index)
        net_rnd = forecast["rnd"] - forecast["rnd_amortization"]
        forecast["nopat_adjusted"] = forecast["nopat"] + net_rnd  # R&D stays tax-deductible

    if inputs.sales_to_capital is None:
        # Reinvestment from the capex, D&A and working capital assumptions, plus net R&D.
        # Adding net R&D to both NOPAT and reinvestment leaves free cash flow unchanged.
        forecast["reinvestment"] = (forecast["capex"] - forecast["d_and_a"]
                                    + forecast["nwc_change"] + net_rnd)
    else:
        # Reinvestment follows growth: each $1 of extra revenue needs 1 / sales_to_capital
        # of extra capital. Falling revenue doesn't release capital (floored at 0).
        prior_revenue = forecast["revenue"].shift(fill_value=inputs.base_revenue)
        added_revenue = forecast["revenue"] - prior_revenue
        forecast["reinvestment"] = (added_revenue / inputs.sales_to_capital).clip(lower=0)
        forecast["fcff"] = forecast["nopat_adjusted"] - forecast["reinvestment"]
        # What's left for capex beyond D&A and for working capital, after net R&D
        forecast["net_capex_and_wc"] = forecast["reinvestment"] - net_rnd
        forecast[["capex", "nwc_change"]] = math.nan  # not used in this mode

    # The return on new capital that the assumptions imply each year:
    # extra NOPAT this year / what was reinvested the year before.
    prior = forecast["reinvestment"].shift()
    forecast["implied_roic"] = (forecast["nopat_adjusted"].diff() / prior).where(prior > 0)
    return forecast


def value(inputs):
    """Run the DCF and return the forecast, enterprise value and value per share."""
    if inputs.terminal_growth >= inputs.wacc:
        raise ValueError(
            f"Terminal growth ({inputs.terminal_growth:.1%}) must be below WACC ({inputs.wacc:.1%}), "
            "otherwise the terminal value is infinite."
        )
    forecast = build_forecast(inputs)

    # Discount each year from the valuation date to that fiscal year's end.
    years_away = (forecast["year_end"] - inputs.valuation_date).dt.days / 365.25
    if years_away.iloc[0] <= 0:
        raise ValueError(
            "The first forecast year has already ended. Run update_data.py to load the latest 10-K."
        )
    forecast["discount_factor"] = 1 / (1 + inputs.wacc) ** years_away
    forecast["pv_fcff"] = forecast["fcff"] * forecast["discount_factor"]

    final = forecast.iloc[-1]
    g = inputs.terminal_growth
    if inputs.terminal_roic <= g:
        raise ValueError(
            f"Return on new capital ({inputs.terminal_roic:.1%}) must be above terminal growth ({g:.1%})."
        )
    # Growth has to be paid for. To grow at g when new capital earns a return of R,
    # a company must reinvest g / R of its NOPAT every year (Damodaran's approach).
    # So terminal free cash flow is next year's NOPAT minus that reinvestment.
    # With R&D capitalized, NOPAT and the return on new capital are both measured
    # with R&D treated as investment, so terminal_roic must be on that basis too.
    terminal_reinvestment_rate = g / inputs.terminal_roic
    terminal_fcff = final["nopat_adjusted"] * (1 + g) * (1 - terminal_reinvestment_rate)
    terminal_value = terminal_fcff / (inputs.wacc - g)
    pv_terminal = terminal_value * final["discount_factor"]
    pv_forecast = forecast["pv_fcff"].sum()
    enterprise_value = pv_forecast + pv_terminal
    equity_value = enterprise_value - inputs.net_claims

    return {
        "forecast": forecast,
        "pv_forecast": pv_forecast,
        "terminal_value": terminal_value,
        "pv_terminal": pv_terminal,
        "enterprise_value": enterprise_value,
        "terminal_share": pv_terminal / enterprise_value,
        "terminal_reinvestment_rate": terminal_reinvestment_rate,
        # For comparison: share of NOPAT reinvested in the final forecast year
        "final_year_reinvestment_rate": final["reinvestment"] / final["nopat_adjusted"],
        # Cross-check with the exit multiple method: what EV/EBITDA does the terminal value imply?
        "implied_exit_multiple": terminal_value / (final["ebit"] + final["d_and_a"]),
        "equity_value": equity_value,
        "per_share": equity_value / inputs.shares,
    }


def sensitivity(inputs, steps=(-0.01, -0.005, 0.0, 0.005, 0.01)):
    """Value per share for a grid of WACC (rows) and terminal growth (columns).

    Cells where terminal growth isn't below WACC (or the return on new capital) are left empty.
    """
    table = {}
    for wacc_step in steps:
        wacc = inputs.wacc + wacc_step
        row = {}
        for growth_step in steps:
            growth = inputs.terminal_growth + growth_step
            if growth >= wacc or growth >= inputs.terminal_roic:
                row[growth] = math.nan
            else:
                row[growth] = value(replace(inputs, wacc=wacc, terminal_growth=growth))["per_share"]
        table[wacc] = row
    return pd.DataFrame.from_dict(table, orient="index")


def implied_growth(inputs, price, low=-0.5, high=1.0, iterations=100):
    """Reverse DCF: the constant yearly revenue growth that makes value per share equal the price.

    Margins, reinvestment and the terminal value keep their assumed values.
    Uses bisection: value rises with growth (as long as new capital earns more than
    WACC), so we keep halving the interval that contains the answer.
    Returns None if the answer is outside [low, high].
    """
    years = len(inputs.growth_rates)

    def gap(rate):
        trial = replace(inputs, growth_rates=[rate] * years)
        return value(trial)["per_share"] - price

    if gap(low) > 0 or gap(high) < 0:
        return None
    for _ in range(iterations):
        middle = (low + high) / 2
        if gap(middle) < 0:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def calculate_wacc(risk_free_rate, beta, equity_risk_premium, pretax_cost_of_debt,
                   tax_rate, equity_value, debt):
    """Weighted average cost of capital, using market values for the weights."""
    cost_of_equity = risk_free_rate + beta * equity_risk_premium  # CAPM
    after_tax_cost_of_debt = pretax_cost_of_debt * (1 - tax_rate)
    debt_weight = debt / (equity_value + debt)
    return {
        "cost_of_equity": cost_of_equity,
        "after_tax_cost_of_debt": after_tax_cost_of_debt,
        "debt_weight": debt_weight,
        "wacc": (1 - debt_weight) * cost_of_equity + debt_weight * after_tax_cost_of_debt,
    }


def historical_references(history, years=3):
    """Averages over the last few fiscal years, shown next to the assumptions as a guide."""
    recent = history.tail(years)
    revenue = recent["revenue"]
    latest = history.iloc[-1]
    returns = add_return_metrics(history)
    items, _ = balance_sheet_bridge(latest)
    debt = items["long_term_debt"] + items["short_term_debt"] + items["finance_lease_liabilities"]
    return {
        "ebit_margin": (recent["operating_income"] / revenue).mean(),
        "d_and_a_pct": (recent["d_and_a"] / revenue).mean(),
        "capex_pct": (recent["capex"] / revenue).mean(),
        "tax_rate": (recent["income_tax"] / recent["pretax_income"]).mean(),
        "rnd_pct": (recent["rnd"] / revenue).mean(),
        "roic": returns["roic"].iloc[-1],
        "roic_adjusted": returns["roic_adjusted"].iloc[-1],
        "roic_new": incremental_roic(returns, "nopat", "invested_capital", 5),
        "roic_new_adjusted": incremental_roic(
            returns, "nopat_adjusted", "invested_capital_adjusted", 5),
        "sales_to_capital_new": incremental_sales_to_capital(returns, "invested_capital", 5),
        "sales_to_capital_new_adjusted": incremental_sales_to_capital(
            returns, "invested_capital_adjusted", 5),
        # Backward-looking: reflects rates locked in when the debt was issued.
        "interest_rate": latest["interest_expense"] / debt if debt > 0 else math.nan,
    }


def sanity_checks(inputs, result, risk_free_rate):
    """Warnings about assumptions that are allowed but worth a second look."""
    warnings = []
    forecast = result["forecast"]
    if inputs.sales_to_capital is not None:
        if "net_capex_and_wc" in forecast and (forecast["net_capex_and_wc"] < 0).any():
            warnings.append(
                "Sales-to-capital implies less reinvestment than net R&D alone in some years, "
                "so capex would have to fall below D&A. Consider a lower sales_to_capital."
            )
    elif inputs.capex_pct < inputs.d_and_a_pct:
        warnings.append(
            "During the forecast, capex is below D&A, so the asset base shrinks while revenue grows."
        )
    if inputs.terminal_roic > inputs.wacc:
        warnings.append(
            f"Return on new capital ({inputs.terminal_roic:.0%}) stays above WACC ({inputs.wacc:.1%}) "
            "forever, which assumes a lasting competitive advantage. Many valuations let it fade "
            "toward WACC; make sure the source explains why it doesn't here."
        )
    if inputs.terminal_growth > risk_free_rate:
        warnings.append(
            f"Terminal growth ({inputs.terminal_growth:.2%}) is above the risk-free rate "
            f"({risk_free_rate:.2%}). Few companies can outgrow the economy forever."
        )
    if result["terminal_share"] > 0.85:
        warnings.append(
            f"The terminal value is {result['terminal_share']:.0%} of enterprise value, "
            "so the result depends heavily on the terminal assumptions."
        )
    return warnings
