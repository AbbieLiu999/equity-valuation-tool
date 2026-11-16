"""Unit tests for metrics.py, using small hand-made tables with known answers.

Run from the project root with: python -m pytest
"""
import math

import pandas as pd

from metrics import add_derived_metrics, calculate_valuation, is_financial
from sec_data import METRIC_TAGS


def make_df(periods, **columns):
    """Build a table shaped like load_financials() output, with every metric column."""
    df = pd.DataFrame(columns, index=pd.to_datetime(periods))
    return df.reindex(columns=list(METRIC_TAGS))


def test_ebitda_and_fcf():
    df = make_df(
        ["2024-12-31"],
        revenue=[100.0], operating_income=[20.0], d_and_a=[5.0],
        net_income=[15.0], operating_cash_flow=[30.0], capex=[10.0],
    )
    row = add_derived_metrics(df).iloc[0]
    assert row["ebitda"] == 25.0
    assert math.isclose(row["ebitda_margin"], 0.25)
    assert row["fcf"] == 20.0
    assert math.isclose(row["fcf_conversion"], 20.0 / 15.0)


def test_d_and_a_filled_from_components():
    # First year: no total D&A reported, so it should be depreciation + amortization.
    # Second year: total D&A reported, so it should be kept as is.
    df = make_df(
        ["2023-12-31", "2024-12-31"],
        revenue=[100.0, 100.0], d_and_a=[None, 6.0],
        depreciation=[4.0, 4.0], amortization=[1.0, 1.0],
    )
    result = add_derived_metrics(df)
    assert list(result["d_and_a"]) == [5.0, 6.0]


def test_d_and_a_stays_empty_if_a_component_is_missing():
    df = make_df(["2024-12-31"], revenue=[100.0], depreciation=[4.0])
    assert pd.isna(add_derived_metrics(df)["d_and_a"].iloc[0])


def test_growth_skips_gaps_in_years():
    # 2022 is missing, so 2023 must not be compared with 2021.
    df = make_df(
        ["2020-12-31", "2021-12-31", "2023-12-31"],
        revenue=[100.0, 110.0, 150.0],
    )
    growth = add_derived_metrics(df)["revenue_growth"]
    assert pd.isna(growth.iloc[0])  # no prior year
    assert math.isclose(growth.iloc[1], 0.10)
    assert pd.isna(growth.iloc[2])  # gap before this year


def test_fcf_conversion_empty_when_loss_making():
    df = make_df(
        ["2024-12-31"],
        revenue=[100.0], net_income=[-5.0], operating_cash_flow=[10.0], capex=[2.0],
    )
    assert pd.isna(add_derived_metrics(df)["fcf_conversion"].iloc[0])


def test_is_financial():
    assert is_financial("6021")       # national commercial bank
    assert not is_financial("3571")   # electronic computers
    assert not is_financial("")       # missing SIC code
    assert not is_financial(None)


# ---------- Valuation ----------

def make_latest(**values):
    """One row shaped like the latest year of add_derived_metrics() output."""
    columns = list(METRIC_TAGS) + ["ebitda", "fcf"]
    return pd.Series(values, dtype=float).reindex(columns)


def test_enterprise_value_bridge_and_multiples():
    latest = make_latest(
        revenue=500, ebitda=125, net_income=50, fcf=40,
        long_term_debt=200, short_term_debt=50, finance_lease_liabilities=30,
        cash=100, short_term_investments=80, long_term_investments=20,
        operating_income=100
    )
    result = calculate_valuation(latest, price=10, shares=100)  # market cap 1,000
    # 1,000 + 200 + 50 + 30 + 0 (minority interest) - 100 - 80 - 20 = 1,080
    assert result["enterprise_value"] == 1080
    assert result["missing"] == ["minority_interest"]
    m = result["multiples"]
    assert math.isclose(m["ev_to_ebitda"], 1080 / 125)
    assert math.isclose(m["ev_to_ebit"], 1080 / 100)
    assert math.isclose(m["ev_to_revenue"], 1080 / 500)
    assert math.isclose(m["pe"], 20)
    assert math.isclose(m["fcf_yield"], 0.04)


def test_debt_adds_up_parts_when_total_is_missing():
    latest = make_latest(revenue=500, ebitda=100, net_income=50, long_term_debt_noncurrent=150)
    result = calculate_valuation(latest, price=10, shares=100)
    assert result["bridge"]["long_term_debt"] == 150
    assert "long_term_debt_current" in result["missing"]


def test_multiples_empty_when_earnings_are_negative():
    latest = make_latest(revenue=500, ebitda=-10, net_income=-30, fcf=5)
    m = calculate_valuation(latest, price=10, shares=100)["multiples"]
    assert math.isnan(m["ev_to_ebitda"])
    assert math.isnan(m["pe"])