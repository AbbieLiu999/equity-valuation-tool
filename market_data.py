"""Get market data: share prices, interest rates and credit spreads, and beta.

Kept in its own file so data sources are easy to replace.
"""
import io

import pandas as pd
import requests
import yfinance as yf

# ICE BofA US corporate bond option-adjusted spreads by rating, published on FRED
FRED_SPREAD_SERIES = {
    "AAA": "BAMLC0A1CAAA",
    "AA": "BAMLC0A2CAA",
    "A": "BAMLC0A3CA",
    "BBB": "BAMLC0A4CBBB",
}


def get_price(ticker):
    """Return the latest price as a dict with date, close and source.

    During trading hours, today's "close" is the latest intraday price.
    """
    history = yf.Ticker(ticker).history(period="5d")
    if history.empty:
        raise ValueError(f"no price data returned for {ticker}")
    return {
        "date": history.index[-1].date().isoformat(),
        "close": float(history["Close"].iloc[-1]),
        "source": "yfinance",
    }

def blume_adjust(raw_beta):
    """Pull a historical beta toward 1.0, the market average: 0.67 x raw + 0.33 x 1.0."""
    return 0.67 * raw_beta + 0.33

def get_beta(ticker):
    """Return the stock's beta from Yahoo Finance (5 years of monthly returns)."""
    beta = yf.Ticker(ticker).info.get("beta")
    if beta is None:
        raise ValueError(f"no beta available for {ticker}")
    raw = float(beta)
    return blume_adjust(raw), f"Blume-adjusted from raw beta {raw:.2f} (yfinance, 5Y monthly)"


def parse_fred_csv(text):
    """Return (latest value as a decimal, its date) from a FRED CSV quoted in percent.

    FRED leaves days without data blank (or "."), so skip anything that isn't a number.
    """
    df = pd.read_csv(io.StringIO(text))
    values = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    latest = values.last_valid_index()
    if latest is None:
        raise ValueError("no data in the FRED series")
    return float(values[latest]) / 100, str(df.iloc[latest, 0])


def get_fred_latest(series_id):
    """Download one FRED series (no API key needed) and return its latest value and date."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return parse_fred_csv(response.text)


def get_risk_free_rate():
    """Return the 10-year US Treasury yield as a decimal, with a description of its source."""
    rate, as_of = get_fred_latest("DGS10")
    return rate, f"FRED 10-year Treasury yield, {as_of}"


def rating_bucket(rating):
    """Map an S&P/Fitch style rating to its broad category: "AA+" -> "AA", "A-" -> "A"."""
    bucket = rating.upper().rstrip("+-")
    if bucket not in FRED_SPREAD_SERIES:
        raise ValueError(
            f"unsupported rating '{rating}'. Use S&P/Fitch style ratings from AAA to BBB-, "
            "or set pretax_cost_of_debt directly"
        )
    return bucket


def get_credit_spread(rating):
    """Return the corporate bond spread for a credit rating as a decimal, with its source."""
    bucket = rating_bucket(rating)
    spread, as_of = get_fred_latest(FRED_SPREAD_SERIES[bucket])
    return spread, f"FRED ICE BofA {bucket} spread, {as_of}"