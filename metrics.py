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


def calculate_valuation(latest, price, shares):
    """Build the enterprise value bridge and valuation multiples.

    latest: the most recent year (one row) of add_derived_metrics() output.
    Bridge items with no data are counted as 0 and listed in "missing",
    so a gap is always visible instead of silently changing the result.
    """
    missing = []

    def amount(metric):
        value = latest[metric]
        if pd.isna(value):
            missing.append(metric)
            return 0.0
        return float(value)

    market_cap = price * shares

    # Use total long-term debt if it's reported, otherwise add up the two parts.
    if pd.isna(latest["long_term_debt"]):
        long_term_debt = amount("long_term_debt_noncurrent") + amount("long_term_debt_current")
    else:
        long_term_debt = float(latest["long_term_debt"])

    # Finance leases are included but operating leases are not: under ASC 842,
    # operating lease costs are already deducted before EBITDA, finance lease costs are not.
    bridge = {
        "market_cap": market_cap,
        "long_term_debt": long_term_debt,
        "short_term_debt": amount("short_term_debt"),
        "finance_lease_liabilities": amount("finance_lease_liabilities"),
        "minority_interest": amount("minority_interest"),
        "cash": -amount("cash"),
        "investments": -(amount("short_term_investments") + amount("long_term_investments")),
    }
    enterprise_value = sum(bridge.values())

    ebitda = latest["ebitda"]
    net_income = latest["net_income"]
    ebit = latest["operating_income"]
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