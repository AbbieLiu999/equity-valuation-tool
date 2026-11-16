"""Unit tests for the cleaning logic in sec_data.py, using small fake SEC responses.

No internet needed: these test how we clean data, not the SEC API itself.
"""
from sec_data import get_annual_values, get_latest_shares


def entry(end, val, form="10-K", filed="2024-11-01", start=None):
    """One fact, shaped like an entry in SEC's companyfacts data."""
    e = {"end": end, "val": val, "form": form, "filed": filed, "accn": "test-accn"}
    if start:
        e["start"] = start
    return e


def make_facts(tags):
    """Wrap {tag: [entries]} the way SEC nests it: facts -> us-gaap -> tag -> units -> USD."""
    return {"facts": {"us-gaap": {t: {"units": {"USD": es}} for t, es in tags.items()}}}


def values_only(result):
    return {end: record["value"] for end, record in result.items()}


def test_keeps_latest_filing_and_skips_quarters():
    facts = make_facts({"Rev": [
        entry("2023-09-30", 383.0, filed="2023-11-03", start="2022-09-25"),
        entry("2023-09-30", 89.5, filed="2023-11-03", start="2023-07-02"),  # one quarter
        entry("2023-09-30", 383.3, filed="2024-11-01", start="2022-09-25"),  # restated
        entry("2023-12-30", 119.6, form="10-Q", start="2023-10-01"),         # quarterly report
    ]})
    assert values_only(get_annual_values(facts, ["Rev"])) == {"2023-09-30": 383.3}


def test_higher_priority_tag_wins_and_lower_fills_gaps():
    facts = make_facts({
        "NewTag": [entry("2023-09-30", 383.3, start="2022-09-25")],
        "OldTag": [
            entry("2017-09-30", 229.2, start="2016-09-25"),
            entry("2023-09-30", 999.0, start="2022-09-25"),
        ],
    })
    result = get_annual_values(facts, ["NewTag", "OldTag"])
    assert values_only(result) == {"2023-09-30": 383.3, "2017-09-30": 229.2}
    assert result["2017-09-30"]["source_tag"] == "OldTag"


def test_latest_shares_uses_most_recent_10k_or_10q():
    shares = [
        {"end": "2025-10-17", "val": 14.8e9, "form": "10-K", "filed": "2025-10-31", "accn": "a"},
        {"end": "2026-04-18", "val": 14.7e9, "form": "10-Q", "filed": "2026-05-01", "accn": "b"},
        {"end": "2026-06-01", "val": 1.0, "form": "8-K", "filed": "2026-06-02", "accn": "c"},
    ]
    facts = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": shares}}}}}
    result = get_latest_shares(facts)
    assert result["value"] == 14.7e9
    assert result["period_end"] == "2026-04-18"


def test_latest_shares_none_when_not_reported():
    assert get_latest_shares({"facts": {}}) is None


def test_balance_sheet_values_from_10q_are_kept():
    # NVIDIA's fiscal year end balance for this tag only appears in later 10-Qs.
    # Quarter-end balances are kept here too; load_financials filters them out.
    facts = make_facts({"Securities": [
        entry("2026-01-25", 39.1, form="10-Q", filed="2026-05-20"),
        entry("2026-01-25", 39.1, form="10-Q", filed="2026-08-26"),
        entry("2026-04-26", 37.1, form="10-Q", filed="2026-05-20"),
    ]})
    result = values_only(get_annual_values(facts, ["Securities"]))
    assert result == {"2026-01-25": 39.1, "2026-04-26": 37.1}