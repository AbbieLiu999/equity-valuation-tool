"""Get share prices. Kept in its own file so the data source is easy to replace."""
import yfinance as yf


def get_price(ticker):
    """Return the latest closing price as a dict with date, close and source."""
    history = yf.Ticker(ticker).history(period="5d")
    if history.empty:
        raise ValueError(f"no price data returned for {ticker}")
    return {
        "date": history.index[-1].date().isoformat(),
        "close": float(history["Close"].iloc[-1]),
        "source": "yfinance",
    }