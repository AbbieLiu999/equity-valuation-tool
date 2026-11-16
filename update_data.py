"""Download data from SEC EDGAR and save it to the local database.

Usage: python update_data.py AAPL MSFT NVDA
"""
import sys
import time

import db
import sec_data


def update(ticker):
    """Fetch one company's details and financials, then save both."""
    cik = sec_data.get_cik(ticker)
    info = sec_data.get_company_info(ticker, cik)
    rows = sec_data.get_financial_records(cik)
    db.save_company(info)  # company first, because financials refer to it
    db.save_financials(cik, rows)
    print(f"{info['ticker']}: saved {len(rows)} rows for {info['name']}")


if __name__ == "__main__":
    db.init_db()
    tickers = sys.argv[1:] or db.list_tickers()
    if not tickers:
        print("Usage: python update_data.py AAPL MSFT ...")
    for ticker in tickers:
        update(ticker)
        time.sleep(0.2)