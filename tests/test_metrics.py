"""Unit tests for metrics.py, using small hand-made tables with known answers.

Run from the project root with: python -m pytest
"""
import math

import pandas as pd

from metrics import add_derived_metrics, is_financial
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
