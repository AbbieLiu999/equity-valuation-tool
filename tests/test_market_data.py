"""Unit tests for the parts of market_data.py that don't need the internet."""
import pytest

from market_data import parse_fred_csv, rating_bucket, blume_adjust


def test_fred_csv_skips_days_without_data():
    text = "observation_date,DGS10\n2026-11-12,4.31\n2026-11-13,4.28\n2026-11-16,\n"
    rate, as_of = parse_fred_csv(text)
    assert rate == pytest.approx(0.0428)
    assert as_of == "2026-11-13"


def test_rating_bucket():
    assert rating_bucket("AA+") == "AA"
    assert rating_bucket("aaa") == "AAA"
    assert rating_bucket("BBB-") == "BBB"
    with pytest.raises(ValueError):
        rating_bucket("Aa1")  # Moody's style isn't supported

def test_blume_adjusted_beta():
    assert blume_adjust(1.09) == pytest.approx(1.0603)