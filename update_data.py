"""Download data from SEC EDGAR and Yahoo Finance and save it to the local database.

Usage:
    python update_data.py AAPL MSFT    add or refresh these companies
    python update_data.py              refresh every company already stored
"""
import sys
import time

import db
import market_data
import sec_data


def update(ticker):
    """Fetch one company's details, financials and share price, then save them."""
    cik = sec_data.get_cik(ticker)
    info = sec_data.get_company_info(ticker, cik)
    rows = sec_data.get_financial_records(cik)
    db.save_company(info)  # company first, because the other tables refer to it
    db.save_financials(cik, rows)
    print(f"{info['ticker']}: saved {len(rows)} rows for {info['name']}")

    # The price comes from an unofficial source that occasionally fails.
    # If it does, keep the SEC data we already saved and warn instead of stopping.
    try:
        price = market_data.get_price(ticker)
    except Exception as error:
        print(f"  Warning: could not get a price for {ticker} ({error})")
        return
    db.save_price(cik, price)
    print(f"  price {price['close']:.2f} USD on {price['date']}")


if __name__ == "__main__":
    db.init_db()
    tickers = sys.argv[1:] or db.list_tickers()
    if not tickers:
        print("Usage: python update_data.py AAPL MSFT ...")
    for ticker in tickers:
        update(ticker)
        time.sleep(0.2)  # pause between companies to stay well under SEC's rate limit