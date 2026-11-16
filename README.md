# Equity Valuation Tool

An automated equity valuation pipeline for US-listed companies. It pulls financial statements directly from SEC EDGAR, cleans inconsistent XBRL data, stores every number in a SQLite database together with the filing it came from, and calculates profitability metrics, an enterprise value bridge and valuation multiples.

I built this after working in equity investment, to automate the parts of company analysis I used to do by hand: pulling numbers out of 10-Ks, reconciling definitions across companies, and building EV bridges. Most of the interesting work turned out to be in the details: XBRL tags that change over the years, companies that report the same line item in different ways, and valuation definitions that have to stay consistent between the numerator and the denominator.

## Example output

```
$ python show.py AAPL
Apple Inc. (SIC 3571: Electronic Computers)

Key figures (USD billions):
period_end           2022-09-24  2023-09-30  2024-09-28  2025-09-27  2026-09-26
revenue                   394.3       383.3       391.0       416.2       478.8
ebitda                    130.5       125.8       134.7       144.7       171.5
net_income                 99.8        97.0        93.7       112.0       132.2
operating_cash_flow       122.2       110.5       118.3       111.5       145.1
capex                      10.7        11.0         9.4        12.7         9.0
fcf                       111.4        99.6       108.8        98.8       136.1

Ratios (%):
period_end        2022-09-24  2023-09-30  2024-09-28  2025-09-27  2026-09-26
revenue_growth           7.8        -2.8         2.0         6.4        15.1
operating_margin        30.3        29.8        31.5        32.0        33.0
ebitda_margin           33.1        32.8        34.4        34.8        35.8
net_margin              25.3        25.3        24.0        26.9        27.6
fcf_margin              28.3        26.0        27.8        23.7        28.4
fcf_conversion         111.7       102.7       116.1        88.2       102.9

Valuation (USD billions)
  Price 352.78 USD on 2026-11-16 (yfinance), 14.53B shares as of 2026-10-23
  Balance sheet and earnings: fiscal year ending 2026-09-26

  Market cap                   5,126.7
  + Long-term debt                78.1
  + Short-term debt                2.0
  + Finance leases                 1.5
  + Minority interest              0.0
  - Cash                          36.3
  - Marketable securities        100.6
  = Enterprise value           5,071.3

  EV/EBITDA    29.6x
  EV/EBIT      32.1x
  EV/Revenue   10.6x
  P/E          38.8x
  FCF yield    2.7%

  No data found, counted as 0: minority_interest
```

## What it does

| Stage | What happens |
|---|---|
| Fetch | Downloads XBRL financial data, company details (name, industry code, fiscal year end) and the latest share count from SEC EDGAR, and the latest share price from Yahoo Finance |
| Clean | Keeps annual figures only, uses the most recently filed value when a number is restated, and merges XBRL tags that companies switch between over time |
| Store | Saves everything in SQLite in long format, with the source XBRL tag, accession number and filing date for every value |
| Analyze | Calculates EBITDA, margins, free cash flow, growth, an EV bridge and valuation multiples, and flags financial companies where these metrics don't apply |
| Test | 14 unit tests cover the cleaning rules and every calculation, including edge cases like gaps in the data and loss-making years |

## Quick start

```bash
git clone https://github.com/AbbieLiu999/equity-valuation-tool.git
cd equity-valuation-tool
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

The SEC requires contact information in every request. Open `sec_data.py` and replace the `HEADERS` placeholder with your name and email.

```bash
python update_data.py AAPL MSFT NVDA   # download data (needs internet)
python show.py MSFT                    # metrics and valuation (works offline)
python update_data.py                  # refresh every company already stored
python -m pytest                       # run the tests
```

## Project structure

| File | Responsibility |
|---|---|
| `sec_data.py` | Fetches and cleans data from SEC EDGAR; defines which XBRL tags map to each metric |
| `market_data.py` | Fetches share prices; kept separate so the price source is easy to replace |
| `db.py` | Creates the SQLite tables and handles all saving and loading |
| `metrics.py` | All calculations: derived metrics, EV bridge and multiples |
| `update_data.py` | Command-line entry point to download and store data |
| `show.py` | Command-line entry point to display a company's metrics and valuation |
| `explore.py` | Helper used to investigate which XBRL tags a company reports |
| `tests/` | Unit tests for the cleaning logic and the calculations |

## Design

### Pipeline

```
update_data.py -> sec_data.py (SEC EDGAR) ----+
               -> market_data.py (Yahoo) -----+-> db.py -> valuation.db

show.py -> db.py -> metrics.py -> terminal output
```

Downloading and analyzing are deliberately separate. `update_data.py` is the only part that needs the internet; `show.py` reads from the local database, so it runs instantly and offline, and never sends unnecessary requests to the SEC.

### Cleaning XBRL data

SEC data is far less tidy than it looks. Four problems shaped the cleaning logic.

**Companies use different tags for the same item, and change them over time.** Apple reported revenue as `SalesRevenueNet` until ASC 606 took effect, then switched to `RevenueFromContractWithCustomerExcludingAssessedTax`. Each metric therefore maps to a priority-ordered list of tags: the highest-priority tag wins for any given year, and lower-priority tags only fill gaps. Apple's 20-year revenue history ends up drawing on three different tags.

**The same number appears in many filings.** Every 10-K repeats prior years as comparatives, and those comparatives may be restated. For each period, the value from the most recently filed report is kept.

**Annual and quarterly figures are mixed together.** Income statement and cash flow items cover a period, so they must come from a 10-K and span 340 to 390 days (many companies use 52/53-week fiscal years). Balance sheet items are a single date, so the date alone identifies them, and they are accepted from 10-Qs as well. This matters in practice: NVIDIA's fiscal year end marketable securities appear under the standard tag only in later quarterly reports.

**The `fy` field is misleading.** In SEC's data it refers to the fiscal year of the filing, not of the number, so a 2023 comparative inside a 2024 annual report is labelled 2024. Periods are identified by their end date instead.

### Database

Data is stored in long format, one row per company, period and metric, rather than one column per metric. This means new metrics can be added without changing the schema, missing data simply has no row, and each value can carry its own source. Since every value is a USD amount (or a share count), the usual drawback of this design, mixing data types in one column, doesn't apply. pandas `pivot` turns it back into a familiar wide table for analysis.

The composite primary key `(cik, period_end, metric)` makes duplicate values impossible, foreign keys prevent data for companies that don't exist, and each company's refresh runs in a single transaction, so a failed update can never leave the old data deleted with nothing in its place. The database stores only raw figures from filings. Anything calculated, such as EBITDA or free cash flow, is computed in Python when the data is read, so a change in methodology never requires downloading data again.

### Data lineage

Every value records the XBRL tag it came from, the filing's accession number and the filing date, so any number can be traced back to the exact report it was taken from. For example, this query shows which tags each company's revenue history comes from:

```sql
SELECT c.ticker, f.source_tag, COUNT(*) AS years
FROM financials f JOIN companies c ON f.cik = c.cik
WHERE f.metric = 'revenue'
GROUP BY c.ticker, f.source_tag;
```

| ticker | source_tag | years |
|---|---|---|
| AAPL | RevenueFromContractWithCustomerExcludingAssessedTax | 10 |
| AAPL | Revenues | 1 |
| AAPL | SalesRevenueNet | 9 |
| MSFT | RevenueFromContractWithCustomerExcludingAssessedTax | 11 |
| MSFT | Revenues | 3 |
| MSFT | SalesRevenueNet | 5 |
| NVDA | RevenueFromContractWithCustomerExcludingAssessedTax | 6 |
| NVDA | Revenues | 13 |

### Error handling

Expected gaps are handled quietly: a company that doesn't use a particular tag simply has no data for it. Real errors stop the program with a clear message, such as a rejected request or an unknown ticker. Two cases sit in between. A failed share price lookup prints a warning but keeps the SEC data that was already saved, because the price source is unofficial and occasionally unavailable. Items missing from the EV bridge are counted as zero, since many companies simply don't have them (Apple has no minority interest), but they are always listed in the output so that a real gap is never hidden.

## Methodology and data notes

| Item | Approach | Reasoning |
|---|---|---|
| EBITDA | Operating income plus D&A, with no add-back for stock-based compensation | Stock-based compensation is a real cost to shareholders, even though many companies' adjusted EBITDA excludes it |
| D&A | Reported total D&A; if not reported, depreciation plus intangible asset amortization | Microsoft reports "depreciation, amortization, and other" under a company-specific tag that the SEC API doesn't provide. Its finance lease assets are included in property and equipment, so their amortization is already part of depreciation and isn't added separately |
| Free cash flow | Operating cash flow minus capital expenditures | A simple measure for historical analysis; the DCF will use unlevered free cash flow. Microsoft's capex excludes data centers obtained through finance leases, so its free cash flow is overstated |
| Capex | Purchases of property and equipment; falls back to purchases of productive assets | NVIDIA's fallback tag also includes intangible asset purchases (small in practice). Its FY2013 to FY2021 capex uses a company-specific tag and is left blank |
| Growth | Calculated only when the prior year in the data is about one year earlier | Prevents a gap in the data from making a multi-year change look like a single year |
| Ratios on losses | FCF conversion and multiples are left blank when the denominator is zero or negative | A ratio on negative earnings is meaningless and easily misread |
| Financial companies | Companies with SIC codes 6000 to 6799 are identified and excluded from EV-based metrics | Banks' debt is mostly deposits, their raw material, so EV, EBITDA and free cash flow don't apply. They need P/B and ROE instead |
| Share count | Basic shares outstanding from the latest 10-K or 10-Q cover page | Not fully diluted for options and RSUs |
| Cash | Cash, plus current and non-current marketable securities; strategic equity investments are excluded | High-grade securities can be sold at any time, while strategic stakes (such as Microsoft's equity investments) are illiquid or held for business reasons |
| Debt | Total long-term debt if reported, otherwise current plus non-current portions; plus commercial paper or other short-term borrowings | Short-term debt takes the first tag found, so a company with both commercial paper and other borrowings may be understated |
| Leases | Finance lease liabilities are included in EV; operating lease liabilities are not | Under ASC 842, operating lease costs are deducted before EBITDA, while finance lease costs (amortization and interest) are not, so this keeps EV and EBITDA consistent |
| Timing | Current share price, but balance sheet and earnings from the latest fiscal year | This creates a timing mismatch, largest for NVIDIA, whose fiscal year ended in January 2026 while it grew revenue 65% that year. Trailing twelve-month figures are planned |

## Validation

**Against company results.** Apple's FY2026 revenue ($478.8B), operating income ($158.1B), net income ($132.2B) and operating cash flow ($145.1B) match its reported results, and the calculated EBITDA of $171.5B matches independently reported figures.

**Against Yahoo Finance.** Apple's figures differ noticeably from Yahoo's, and every difference can be explained:

| | This tool (Nov 16) | Yahoo (Nov 13) | Main reason |
|---|---|---|---|
| Market cap | $5.13T | $5.18T | Different price dates |
| Enterprise value | $5.07T | $5.20T | Treatment of long-term marketable securities |
| EV/EBITDA | 29.6x | 30.1x | Follows from EV |
| P/E | 38.8x | 39.7x | Price date and share count method |
| Free cash flow | $136.1B | $105.7B | Different definitions |

About $53B of the EV gap comes from the price date. Roughly $80B comes from a deliberate choice: Yahoo's "total cash" of $56.7B includes only cash and short-term securities, while this tool also treats Apple's long-term marketable securities as cash. For P/E, Yahoo divides by diluted EPS, which uses the year's average diluted share count; because of Apple's buybacks, the current share count used here is lower than that average. Yahoo's "levered free cash flow" uses its own formula, so it isn't comparable to operating cash flow minus capex.

**Regression checks.** After changing how filings are filtered, Apple's and Microsoft's valuations were rechecked and came out unchanged, confirming the change only filled gaps without affecting correct figures.

## Case studies

These came up while building and checking the tool, and show why the details matter.

**EBITDA flatters capital-intensive companies.** On EV/EBITDA, Microsoft looks far cheaper than Apple. Microsoft's D&A is 11.8% of revenue against Apple's 2.8%, so the picture reverses on free cash flow:

| | Microsoft | Apple |
|---|---|---|
| EV/EBITDA | 20.0x | 29.6x |
| EV/EBIT | 25.0x | 32.1x |
| P/E | 28.8x | 38.8x |
| EV/FCF | 57.9x | 37.3x |

**Microsoft's finance leases now exceed its traditional debt.** At the end of FY2026, Microsoft had $66.6B of finance lease liabilities against $40.3B of long-term debt, as it increasingly leases data centers rather than borrowing to build them. Leaving finance leases out of EV would significantly understate its leverage.

**Normalizing one-off items.** Microsoft's FY2026 net margin jumped 4.2 percentage points while its operating margin rose only 1.2 points. The 10-K shows net pre-tax gains of $6.5B related to OpenAI's recapitalization in FY2026, after net losses of $4.8B in FY2025. Adjusting both years (assuming a 19% tax rate) brings net income growth down from 31% to about 22%, and raises the P/E from 28.8x to about 30x. EV/EBITDA and EV/EBIT are unaffected, because these items sit below operating income.

**A balance that only appears in quarterly reports.** After adding the right tag for NVIDIA's marketable securities, the number of stored rows didn't change at all, which revealed that the tag still wasn't being picked up. Checking each data point's source showed that NVIDIA's fiscal year end balance appears under that tag only as a comparative in later 10-Qs. The fix was to accept balance sheet values from 10-Qs, while still requiring income and cash flow figures to come from 10-Ks.

## Limitations and roadmap

The next steps are a DCF model with a perpetual growth terminal value, a reverse DCF that shows the growth rate implied by the current share price, and a Streamlit dashboard where every figure links to the filing it came from.

Further improvements include trailing twelve-month earnings and the most recent quarter's balance sheet to remove the timing mismatch, fully diluted share counts using the treasury stock method, a table of manual adjustments for one-off items like Microsoft's OpenAI gains, and a separate valuation module for banks based on P/B and ROE. Company-specific extension tags aren't available through the SEC's companyfacts API; filling those gaps would require parsing full XBRL filings.

## Tech stack

Python, requests, pandas, SQLite, yfinance and pytest.

## Disclaimer

This project is for education and research and is not investment advice. Data comes from SEC EDGAR and from Yahoo Finance through the unofficial yfinance library, and may contain errors.

## Author

Dan Liu. MS in Computer Science student at Northeastern University, previously worked in equity investment. https://www.linkedin.com/in/dan-liu-70b32458/

## License

MIT