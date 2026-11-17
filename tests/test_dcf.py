"""Unit tests for dcf.py, using small hand-made inputs with known answers."""
import math
from dataclasses import replace

import pandas as pd
import pytest

from dcf import (DCFInputs, build_forecast, calculate_wacc, growth_path, implied_growth,
                 sanity_checks, sensitivity, validate_assumptions, value)


def make_inputs(**changes):
    """A simple, valid set of inputs; pass keyword arguments to change any of them."""
    inputs = DCFInputs(
        base_revenue=100.0, base_margin=0.20, base_year_end=pd.Timestamp("2025-12-31"),
        growth_rates=[0.10, 0.08, 0.06], ebit_margin_target=0.20, tax_rate=0.25,
        d_and_a_pct=0.05, capex_pct=0.06, nwc_pct=0.10, wacc=0.09, terminal_growth=0.03,
        terminal_roic=0.15, valuation_date=pd.Timestamp("2026-06-30"), net_claims=-20.0, shares=10.0,
    )
    return replace(inputs, **changes)


def make_assumptions(**operations):
    return {
        "horizon_years": 5,
        "growth": {"near_term": [0.10, 0.08], "terminal": 0.03, "terminal_roic": 0.15},
        "operations": {
            "ebit_margin_target": 0.30, "tax_rate": 0.18, "d_and_a_pct_revenue": 0.03,
            "capex_pct_revenue": 0.04, "nwc_pct_revenue_change": 0.10, **operations,
        },
        "discount_rate": {"equity_risk_premium": 0.05, "credit_rating": "AA"},
    }


def test_growth_fades_to_terminal():
    path = growth_path([0.30, 0.20], 0.03, 5)
    expected = [0.30, 0.20, 0.20 - 0.17 / 3, 0.20 - 0.34 / 3, 0.03]
    assert all(math.isclose(a, b) for a, b in zip(path, expected))


def test_growth_without_near_term_is_terminal_throughout():
    assert growth_path([], 0.03, 3) == [0.03, 0.03, 0.03]


def test_forecast_cash_flow_by_hand():
    inputs = make_inputs(growth_rates=[0.10], base_margin=0.20, ebit_margin_target=0.30,
                         capex_pct=0.08)
    year = build_forecast(inputs).iloc[0]
    # Revenue 110, EBIT 33 (30% margin reached in the only year), NOPAT 24.75,
    # D&A 5.5, capex 8.8, working capital 10% of the 10 increase = 1.0
    assert math.isclose(year["revenue"], 110)
    assert math.isclose(year["fcff"], 24.75 + 5.5 - 8.8 - 1.0)


def test_forecast_years_are_labelled_by_fiscal_year_end():
    assert list(build_forecast(make_inputs()).index) == ["FY2026", "FY2027", "FY2028"]


def test_terminal_value_and_value_per_share():
    result = value(make_inputs())
    final_nopat = result["forecast"]["nopat"].iloc[-1]
    # Growing 3% with a 15% return on new capital means reinvesting 3/15 = 20% of NOPAT.
    assert math.isclose(result["terminal_reinvestment_rate"], 0.20)
    expected = final_nopat * 1.03 * (1 - 0.20) / (0.09 - 0.03)
    assert math.isclose(result["terminal_value"], expected)
    # Net claims of -20 means net cash of 20, which adds to equity value.
    assert math.isclose(result["equity_value"], result["enterprise_value"] + 20)
    assert math.isclose(result["per_share"], result["equity_value"] / 10)


def test_terminal_growth_must_be_below_wacc():
    with pytest.raises(ValueError):
        value(make_inputs(terminal_growth=0.09))


def test_first_forecast_year_already_over():
    with pytest.raises(ValueError):
        value(make_inputs(valuation_date=pd.Timestamp("2027-01-15")))


def test_sensitivity_centre_matches_base_case_and_skips_impossible_cells():
    inputs = make_inputs(wacc=0.05, terminal_growth=0.04)
    grid = sensitivity(inputs)
    assert math.isclose(grid.iloc[2, 2], value(inputs)["per_share"])  # centre cell
    assert math.isnan(grid.iloc[0, 4])  # lowest WACC (4%) with highest growth (5%)


def test_reverse_dcf_recovers_the_growth_rate():
    inputs = make_inputs(growth_rates=[0.12] * 3)
    price = value(inputs)["per_share"]
    assert math.isclose(implied_growth(inputs, price), 0.12, abs_tol=1e-6)


def test_wacc_by_hand():
    w = calculate_wacc(risk_free_rate=0.04, beta=1.2, equity_risk_premium=0.05,
                       pretax_cost_of_debt=0.05, tax_rate=0.20, equity_value=900, debt=100)
    assert math.isclose(w["cost_of_equity"], 0.10)
    assert math.isclose(w["wacc"], 0.9 * 0.10 + 0.1 * 0.04)


def test_valid_assumptions_pass():
    validate_assumptions(make_assumptions())


def test_percent_typed_instead_of_decimal_is_caught():
    with pytest.raises(ValueError, match="decimals"):
        validate_assumptions(make_assumptions(tax_rate=18))


def test_missing_assumption_is_caught():
    a = make_assumptions()
    del a["operations"]["capex_pct_revenue"]
    with pytest.raises(ValueError, match="capex_pct_revenue"):
        validate_assumptions(a)


def test_sanity_checks_flag_capex_below_d_and_a():
    inputs = make_inputs(capex_pct=0.02, d_and_a_pct=0.05)
    warnings = sanity_checks(inputs, value(inputs), risk_free_rate=0.04)
    assert any("capex is below D&A" in w for w in warnings)


def test_higher_return_on_new_capital_means_less_reinvestment_and_more_value():
    low = value(make_inputs(terminal_roic=0.10))
    high = value(make_inputs(terminal_roic=0.30))
    assert high["terminal_reinvestment_rate"] < low["terminal_reinvestment_rate"]
    assert high["per_share"] > low["per_share"]


def test_return_on_new_capital_must_be_above_terminal_growth():
    with pytest.raises(ValueError):
        value(make_inputs(terminal_roic=0.03))


def test_return_on_new_capital_typed_as_percent_is_caught():
    a = make_assumptions()
    a["growth"]["terminal_roic"] = 30
    with pytest.raises(ValueError, match="decimal"):
        validate_assumptions(a)


# ---------- R&D capitalized (Damodaran's approach) ----------

def with_rnd(**changes):
    return make_inputs(rnd_pct=0.10, rnd_life=3, rnd_history=(6.0, 7.0, 8.0), **changes)


def test_rnd_amortization_uses_the_previous_years():
    from dcf import rnd_amortization
    # Year 1: (30 + 60 + 90) / 3. Year 2: (60 + 90 + 120) / 3.
    assert rnd_amortization([30, 60, 90], [120, 150], life=3) == [60, 90]


def test_capitalizing_rnd_leaves_free_cash_flow_unchanged():
    expensed = build_forecast(make_inputs())
    capitalized = build_forecast(with_rnd())
    assert all(math.isclose(a, b) for a, b in zip(expensed["fcff"], capitalized["fcff"]))
    # Net R&D is added to both NOPAT and reinvestment, so they still net to FCFF.
    net = capitalized["nopat_adjusted"] - capitalized["reinvestment"]
    assert all(math.isclose(a, b) for a, b in zip(net, capitalized["fcff"]))


def test_terminal_value_uses_capitalized_nopat():
    result = value(with_rnd())
    final_nopat = result["forecast"]["nopat_adjusted"].iloc[-1]
    expected = final_nopat * 1.03 * (1 - 0.03 / 0.15) / (0.09 - 0.03)
    assert math.isclose(result["terminal_value"], expected)


def test_capitalizing_rnd_needs_enough_history():
    with pytest.raises(ValueError, match="R&D history"):
        build_forecast(make_inputs(rnd_pct=0.10, rnd_life=5, rnd_history=(6.0, 7.0)))


def test_implied_return_on_new_capital():
    f = build_forecast(make_inputs())
    expected = (f["nopat_adjusted"].iloc[1] - f["nopat_adjusted"].iloc[0]) / f["reinvestment"].iloc[0]
    assert math.isclose(f["implied_roic"].iloc[1], expected)
    assert pd.isna(f["implied_roic"].iloc[0])  # no prior forecast year


def test_rnd_assumptions_are_checked():
    a = make_assumptions(rnd_pct_revenue=9)
    with pytest.raises(ValueError, match="rnd_pct_revenue"):
        validate_assumptions(a)
    a = make_assumptions(rnd_pct_revenue=0.09, rnd_life=0)
    with pytest.raises(ValueError, match="rnd_life"):
        validate_assumptions(a)


# ---------- Reinvestment linked to growth (sales-to-capital) ----------

def test_sales_to_capital_reinvestment_and_cash_flow():
    f = build_forecast(make_inputs(growth_rates=[0.10, 0.05], sales_to_capital=2.0))
    # Revenue 100 -> 110 -> 115.5: extra revenue 10 then 5.5, so reinvestment 5 then 2.75.
    assert list(f["reinvestment"].round(6)) == [5.0, 2.75]
    assert all(math.isclose(a, b) for a, b in zip(f["fcff"], f["nopat_adjusted"] - f["reinvestment"]))


def test_sales_to_capital_with_rnd_splits_reinvestment():
    f = build_forecast(with_rnd(sales_to_capital=2.0))
    net_rnd = f["rnd"] - f["rnd_amortization"]
    assert all(math.isclose(a, b) for a, b in zip(f["net_capex_and_wc"], f["reinvestment"] - net_rnd))


def test_falling_revenue_does_not_release_capital():
    f = build_forecast(make_inputs(growth_rates=[-0.10], sales_to_capital=2.0))
    assert f["reinvestment"].iloc[0] == 0


def test_reverse_dcf_works_with_sales_to_capital():
    inputs = make_inputs(growth_rates=[0.12] * 3, sales_to_capital=2.0)
    price = value(inputs)["per_share"]
    assert math.isclose(implied_growth(inputs, price), 0.12, abs_tol=1e-6)


def test_sales_to_capital_replaces_capex_and_working_capital():
    a = make_assumptions(sales_to_capital=1.5)
    del a["operations"]["capex_pct_revenue"]
    del a["operations"]["nwc_pct_revenue_change"]
    validate_assumptions(a)  # no error
    a["operations"]["sales_to_capital"] = 0
    with pytest.raises(ValueError, match="sales_to_capital"):
        validate_assumptions(a)


def test_warns_when_reinvestment_is_below_net_rnd():
    inputs = with_rnd(sales_to_capital=40.0)  # almost no reinvestment for the growth assumed
    warnings = sanity_checks(inputs, value(inputs), risk_free_rate=0.04)
    assert any("less reinvestment than net R&D" in w for w in warnings)