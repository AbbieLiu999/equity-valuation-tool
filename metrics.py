"""Calculations built on top of the raw financial data."""

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