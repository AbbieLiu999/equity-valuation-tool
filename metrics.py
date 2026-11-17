"""Calculations built on top of the raw financial data."""
import math

import pandas as pd

# SIC codes 6000-6799 cover finance, insurance and real estate.
FINANCIAL_SIC_RANGE = range(6000, 6800)


def is_financial(sic):
    """True if the SIC code belongs to a bank, insurer or other financial company."""
    if not sic:
        return False
    return int(sic) in FINANCIAL_SIC_RANGE


def add_derived_metrics(df):
    """Return a copy of df with calculated metrics added."""
    df = df.copy()

    # Some companies (like Microsoft) don't report total D&A as one tag,
    # so fill the gap by adding depreciation and intangible amortization.
    components = df["depreciation"] + df["amortization"]
    df["d_and_a"] = df["d_and_a"].fillna(components)

    # Profitability
    df["ebitda"] = df["operating_income"] + df["d_and_a"]
    df["operating_margin"] = df["operating_income"] / df["revenue"]
    df["ebitda_margin"] = df["ebitda"] / df["revenue"]
    df["net_margin"] = df["net_income"] / df["revenue"]

    # Cash flow
    df["fcf"] = df["operating_cash_flow"] - df["capex"]
    df["fcf_margin"] = df["fcf"] / df["revenue"]
    # FCF conversion only makes sense when the company is profitable.
    profitable = df["net_income"] > 0
    df["fcf_conversion"] = (df["fcf"] / df["net_income"]).where(profitable)

    # Growth: only compare with the prior row if it is about one year earlier.
    # Otherwise a gap in the data would make a multi-year change look like one year.
    days_since_prior = df.index.to_series().diff().dt.days
    one_year_apart = days_since_prior.between(340, 390)
    df["revenue_growth"] = df["revenue"].pct_change(fill_method=None).where(one_year_apart)

    return df


def balance_sheet_bridge(latest):
    """The items between enterprise value and equity value, from the latest balance sheet.

    Debt-like items are positive and cash-like items negative, so that
    enterprise value = market cap + sum of items, and
    equity value = enterprise value - sum of items.
    Items with no data are counted as 0 and listed in "missing",
    so a gap is always visible instead of silently changing the result.
    Returns (items, missing).
    """
    missing = []

    def amount(metric):
        value = latest[metric]
        if pd.isna(value):
            missing.append(metric)
            return 0.0
        return float(value)

    # Use total long-term debt if it's reported, otherwise add up the two parts.
    if pd.isna(latest["long_term_debt"]):
        long_term_debt = amount("long_term_debt_noncurrent") + amount("long_term_debt_current")
    else:
        long_term_debt = float(latest["long_term_debt"])

    # Finance leases are included but operating leases are not: under ASC 842,
    # operating lease costs are already deducted before EBITDA, finance lease costs are not.
    items = {
        "long_term_debt": long_term_debt,
        "short_term_debt": amount("short_term_debt"),
        "finance_lease_liabilities": amount("finance_lease_liabilities"),
        "minority_interest": amount("minority_interest"),
        "cash": -amount("cash"),
        "investments": -(amount("short_term_investments") + amount("long_term_investments")),
    }
    return items, missing


def calculate_valuation(latest, price, shares):
    """Build the enterprise value bridge and valuation multiples.

    latest: the most recent year (one row) of add_derived_metrics() output.
    """
    items, missing = balance_sheet_bridge(latest)
    market_cap = price * shares
    bridge = {"market_cap": market_cap, **items}
    enterprise_value = sum(bridge.values())

    ebitda = latest["ebitda"]
    ebit = latest["operating_income"]
    net_income = latest["net_income"]
    multiples = {
        # Multiples on negative earnings are meaningless, so leave them empty.
        "ev_to_ebitda": enterprise_value / ebitda if ebitda > 0 else math.nan,
        "ev_to_ebit": enterprise_value / ebit if ebit > 0 else math.nan,
        "ev_to_revenue": enterprise_value / latest["revenue"],
        "pe": market_cap / net_income if net_income > 0 else math.nan,
        "fcf_yield": latest["fcf"] / market_cap,
    }
    return {
        "bridge": bridge,
        "enterprise_value": enterprise_value,
        "multiples": multiples,
        "missing": missing,
    }


# ---------- Return on invested capital ----------

def invested_capital(df):
    """Operating invested capital: operating working capital, net PP&E, goodwill and intangibles.

    Cash, securities and interest-bearing debt are left out, because they are
    financing decisions, not part of running the business. Goodwill and intangibles
    count as 0 when not reported; working capital and PP&E must be reported.
    """
    def zero_if_missing(column):
        return df[column].fillna(0)

    operating_assets = (df["current_assets"] - zero_if_missing("cash")
                        - zero_if_missing("short_term_investments"))
    operating_liabilities = (df["current_liabilities"] - zero_if_missing("short_term_debt")
                             - zero_if_missing("long_term_debt_current"))
    return (operating_assets - operating_liabilities + df["ppe_net"]
            + zero_if_missing("goodwill") + zero_if_missing("intangibles"))


def capitalized_rnd(rnd, life=5):
    """Treat R&D as an investment written off over `life` years (Damodaran's approach).

    Accounting rules expense R&D immediately, so research-heavy companies look as if
    they invest almost nothing. Here, each year's R&D becomes an asset that loses
    1/life of its value per year. Returns two Series: the research asset still on
    the books, and this year's amortization. Both need `life` earlier years of R&D.
    """
    asset = sum(rnd.shift(years) * (life - years) / life for years in range(life))
    amortization = sum(rnd.shift(years) / life for years in range(1, life + 1))
    return asset, amortization


def add_return_metrics(df, rnd_life=5):
    """Return a copy of df with NOPAT, invested capital and ROIC, as reported and R&D-adjusted.

    ROIC uses the average of this year's and last year's invested capital, and is
    left empty when that average isn't positive.
    """
    df = df.copy()
    tax_rate = df["income_tax"] / df["pretax_income"]  # effective rate each year

    df["nopat"] = df["operating_income"] * (1 - tax_rate)
    df["invested_capital"] = invested_capital(df)
    average = (df["invested_capital"] + df["invested_capital"].shift()) / 2
    df["roic"] = (df["nopat"] / average).where(average > 0)

    # R&D-adjusted: add back R&D, subtract its amortization, and add the research asset.
    research_asset, amortization = capitalized_rnd(df["rnd"], rnd_life)
    df["research_asset"] = research_asset
    # R&D stays fully tax-deductible, so the add-back isn't taxed (Damodaran's approach).
    df["nopat_adjusted"] = df["nopat"] + df["rnd"] - amortization
    df["invested_capital_adjusted"] = df["invested_capital"] + research_asset
    average = (df["invested_capital_adjusted"] + df["invested_capital_adjusted"].shift()) / 2
    df["roic_adjusted"] = (df["nopat_adjusted"] / average).where(average > 0)
    return df


def incremental_ratio(df, numerator, capital_column, years):
    """Change in `numerator` divided by the change in invested capital over the last `years`.

    Empty when invested capital didn't grow, because the ratio is then meaningless.
    """
    recent = df[[numerator, capital_column]].dropna().tail(years + 1)
    if len(recent) < years + 1:
        return math.nan
    added = recent[numerator].iloc[-1] - recent[numerator].iloc[0]
    added_capital = recent[capital_column].iloc[-1] - recent[capital_column].iloc[0]
    return added / added_capital if added_capital > 0 else math.nan


def incremental_roic(df, nopat_column, capital_column, years):
    """Return on new capital over the last `years`: extra NOPAT / extra invested capital."""
    return incremental_ratio(df, nopat_column, capital_column, years)


def incremental_sales_to_capital(df, capital_column, years):
    """Sales-to-capital on new capital over the last `years`: extra revenue / extra invested capital."""
    return incremental_ratio(df, "revenue", capital_column, years)