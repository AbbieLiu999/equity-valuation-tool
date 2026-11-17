"""Unit tests for the return on invested capital calculations in metrics.py."""
import math

import pandas as pd

from metrics import add_return_metrics, capitalized_rnd, incremental_roic, invested_capital
from sec_data import METRIC_TAGS


def make_df(periods, **columns):
    """Build a table shaped like load_financials() output, with every metric column."""
    df = pd.DataFrame(columns, index=pd.to_datetime(periods))
    return df.reindex(columns=list(METRIC_TAGS))


def test_invested_capital_leaves_out_cash_and_debt():
    df = make_df(
        ["2025-12-31"],
        current_assets=[150.0], cash=[30.0], short_term_investments=[20.0],
        current_liabilities=[120.0], short_term_debt=[10.0], long_term_debt_current=[5.0],
        ppe_net=[40.0], goodwill=[8.0],
    )
    # Operating assets 100, operating liabilities 105: working capital -5.
    # -5 + PP&E 40 + goodwill 8 + intangibles 0 (not reported) = 43
    assert invested_capital(df).iloc[0] == 43.0


def test_capitalized_rnd_with_three_year_life():
    rnd = pd.Series([30.0, 60.0, 90.0])
    asset, amortization = capitalized_rnd(rnd, life=3)
    # Latest year: all of 90, 2/3 of 60, 1/3 of 30 is still on the books.
    assert math.isclose(asset.iloc[2], 90 + 40 + 10)
    assert pd.isna(asset.iloc[1])           # not enough history yet
    assert pd.isna(amortization.iloc[2])    # amortization needs 3 earlier years


def test_roic_uses_average_capital_and_skips_non_positive_capital():
    df = make_df(
        ["2024-12-31", "2025-12-31"],
        operating_income=[20.0, 30.0], income_tax=[5.0, 5.0], pretax_income=[20.0, 25.0],
        current_assets=[50.0, 50.0], current_liabilities=[40.0, 80.0], ppe_net=[90.0, 70.0],
    )
    result = add_return_metrics(df)
    # 2025: NOPAT 30 x (1 - 20%) = 24; capital 100 then 40, average 70.
    assert math.isclose(result["roic"].iloc[1], 24 / 70)
    assert pd.isna(result["roic"].iloc[0])  # no prior year to average with


def test_incremental_roic():
    df = pd.DataFrame({"nopat": [10.0, 12.0, 15.0], "capital": [100.0, 110.0, 125.0]})
    assert math.isclose(incremental_roic(df, "nopat", "capital", 2), 5 / 25)
    shrinking = pd.DataFrame({"nopat": [10.0, 15.0], "capital": [100.0, 90.0]})
    assert math.isnan(incremental_roic(shrinking, "nopat", "capital", 1))