"""Store and load financial data in a local SQLite database."""
import sqlite3

import pandas as pd

from sec_data import METRIC_TAGS

DB_PATH = "valuation.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    cik             TEXT PRIMARY KEY,
    ticker          TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    sic             TEXT,
    sic_description TEXT,
    fiscal_year_end TEXT
);

CREATE TABLE IF NOT EXISTS financials (
    cik        TEXT NOT NULL REFERENCES companies(cik),
    period_end TEXT NOT NULL,
    metric     TEXT NOT NULL,
    value      REAL NOT NULL,
    source_tag TEXT NOT NULL,
    accn       TEXT,
    filed      TEXT,
    PRIMARY KEY (cik, period_end, metric)
);
"""


def get_connection():
    """Open the database and turn on foreign key checks (off by default in SQLite)."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create the tables if they don't exist yet."""
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.close()


def save_company(info):
    """Insert a company, or update its details if it's already stored."""
    conn = get_connection()
    with conn:  # commits if everything succeeds, rolls back on error
        conn.execute(
            """
            INSERT INTO companies (cik, ticker, name, sic, sic_description, fiscal_year_end)
            VALUES (:cik, :ticker, :name, :sic, :sic_description, :fiscal_year_end)
            ON CONFLICT(cik) DO UPDATE SET
                ticker = excluded.ticker,
                name = excluded.name,
                sic = excluded.sic,
                sic_description = excluded.sic_description,
                fiscal_year_end = excluded.fiscal_year_end
            """,
            info,
        )
    conn.close()


def save_financials(cik, rows):
    """Replace all stored financials for one company with fresh rows."""
    conn = get_connection()
    with conn:  # the delete and the inserts succeed or fail together
        conn.execute("DELETE FROM financials WHERE cik = ?", (cik,))
        conn.executemany(
            """
            INSERT INTO financials (cik, period_end, metric, value, source_tag, accn, filed)
            VALUES (:cik, :period_end, :metric, :value, :source_tag, :accn, :filed)
            """,
            rows,
        )
    conn.close()


def load_company(ticker):
    """Return one company's details as a dict, or None if it isn't stored."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row  # lets us turn each row into a dict
    row = conn.execute(
        "SELECT * FROM companies WHERE ticker = ?", (ticker.upper(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def load_financials(ticker):
    """Load one company's financials as a wide table: one row per fiscal year."""
    conn = get_connection()
    long_df = pd.read_sql_query(
        """
        SELECT f.period_end, f.metric, f.value
        FROM financials f
        JOIN companies c ON f.cik = c.cik
        WHERE c.ticker = ?
        """,
        conn,
        params=(ticker.upper(),),
    )
    conn.close()
    if long_df.empty:
        raise ValueError(f"No data for {ticker}. Run update_data.py first.")

    # Long to wide: one row per period, one column per metric.
    df = long_df.pivot(index="period_end", columns="metric", values="value")
    # Every metric gets a column, even if this company has no data for it.
    df = df.reindex(columns=list(METRIC_TAGS))
    df.columns.name = None
    df.index = pd.to_datetime(df.index)
    # Keep only fiscal year ends (rows where revenue exists), oldest to newest.
    return df.sort_index().dropna(subset=["revenue"])

def list_tickers():
    """Return the tickers of all companies stored in the database."""
    conn = get_connection()
    tickers = [row[0] for row in conn.execute("SELECT ticker FROM companies")]
    conn.close()
    return tickers