# QAD Screen — S&P 500 Stock Screener

## Overview

`qadscreen.py` screens all ~503 S&P 500 constituents against a set of quality, value, and liquidity criteria. Each run reports which stocks entered, exited, or held since the previous run, then prints the full results and saves them to `qadscreen_results.csv`, which is overwritten on every run.

## How to Run

```
python qadscreen.py
```

A full run takes several minutes. The income statement (a second Yahoo request) is only fetched for stocks that clear the other checks, or that passed last run, which keeps most tickers to a single request.

All thresholds are defined once as constants at the top of `qadscreen.py` (`MAX_PE`, `MIN_ROE`, etc.) — edit them there to tune the screen.

## Screen Criteria

| Criteria | Threshold |
|---|---|
| Index | S&P 500 constituents only (one share class per company — see Notes) |
| Market Cap | > $2B |
| P/E (trailing) | Between 0 and 25 (negative = losses, fails) |
| Forward P/E | Between 0 and 25 (negative = forecast loss, fails) |
| Gross Margin | > 25% |
| Operating Margin | > 15% |
| Return on Equity | > 10% |
| Shareholder Equity | Must be positive |
| Debt / Equity | < 1.0x |
| Price vs 52-Week High | 15% or more below |
| **ND/EBITDA** | **< 4.0x (deal-breaker)** |
| **Interest Coverage** | **> 4.0x (deal-breaker)** |

### Deal-Breaker Checks

The ND/EBITDA and interest coverage filters are treated as hard disqualifiers:

- **ND/EBITDA** = (Total Debt − Cash) / EBITDA. A net cash position scores negative and passes easily. Companies with negative EBITDA are disqualified.
- **Interest Coverage** = EBIT / Interest Expense, pulled directly from the income statement (not derived from balance sheet ratios). This avoids deferred-revenue distortions that can make a leveraged company's D/E look cleaner than it really is.
- If interest coverage cannot be computed and the company carries meaningful debt (> $100M), the stock is skipped rather than given a free pass.
- Companies with negligible debt (< $100M total) are treated as having effectively infinite coverage and pass this filter automatically.

## Output Columns

| Column | Description |
|---|---|
| Status | `Added` (new this run), `Held` (passed last run too), `Baseline` (first run), or `Removed - ...` (see below) |
| Ticker | Stock symbol |
| Company | Full company name |
| Industry | Yahoo Finance industry classification |
| Market Cap ($B) | Market capitalisation in billions |
| P/E | Trailing twelve-month P/E ratio |
| Fwd P/E | Forward P/E ratio |
| Gross Margin % | Gross profit margin |
| Op Margin % | Operating profit margin |
| ROE % | Return on equity |
| D/E | Debt-to-equity as a ratio (0.25 = debt is 25% of equity) |
| ND/EBITDA | Net debt to EBITDA |
| Int Coverage | EBIT / Interest Expense (999.9 = effectively debt-free) |
| % Below 52W High | How far the current price sits below the 52-week high |
| Reason | Removed rows only: why the stock dropped out |

Passing stocks are sorted alphabetically by **Industry**, then by **Market Cap** largest-to-smallest within each industry.

Stocks that dropped out **this run** are listed at the bottom with a `Removed - failed criteria`, `Removed - data unavailable`, or `Removed - left S&P 500` status, the reason in the **Reason** column, and their figures from the last run they passed. Filter the Status column in Excel to see just the adds or removals.

## Changes Since Last Run

Before overwriting the CSV, each run compares its results against the previous run's `qadscreen_results.csv` and prints a **CHANGES SINCE LAST RUN** report:

| Section | Meaning |
|---|---|
| `+` ENTERED | Passed this run but not last run |
| `-` EXITED | Failed one or more criteria — each failed check is listed with its value and threshold (e.g. `P/E 27.3 (need < 25)`) |
| `?` EXITED - DATA UNAVAILABLE | Couldn't be screened because Yahoo Finance returned missing data. Usually a temporary data gap rather than a real change — re-check next run |
| `x` EXITED - NO LONGER IN S&P 500 | Removed from the index since last run |
| HELD | Passed both this run and last run |

If nothing changed, the report says so. If no previous CSV exists (or it can't be read), the run becomes the baseline and no comparison is printed.

## Output File

`qadscreen_results.csv` — saved in the same directory as the script. **Each run overwrites the previous file**, and only the most recent results are kept (no archive). This file is also the baseline for the next run's comparison (Removed rows are ignored), so deleting it resets the change report. If the file is open in Excel when the run finishes, the save fails with a message — close it and re-run. If no stocks pass the screen, the existing file is left untouched.

## Dependencies

```
pip install yfinance pandas requests lxml
```

## Data Sources

- **Ticker list**: Wikipedia — List of S&P 500 companies
- **Fundamentals & price data**: Yahoo Finance via `yfinance`
- **Income statement** (for interest coverage): Yahoo Finance via `yfinance`

## Notes

- `debtToEquity` from Yahoo Finance is always a percentage (e.g. 45 = 0.45x); the script divides by 100. Yahoo omits it for companies with no debt, which are treated as 0x rather than as missing data.
- Companies with negative shareholder equity (common after heavy buybacks — e.g. MCD, AZO, LOW) are rejected with the reason `Equity negative shareholder equity`. Yahoo leaves their ROE and D/E blank, so this is checked first to avoid misreporting them as data gaps.
- **Share classes:** companies with more than one share class in the index are screened once, using the Class A ticker (GOOGL not GOOG, FOXA not FOX, NWSA not NWS). The skipped tickers are never reported as having left the S&P 500.
- **Retries:** stocks that passed last run get one retry (after a 2-second pause) if Yahoo returns incomplete data, and every check is run so their exit reason is complete. This cuts down false "data unavailable" exits.
- The 0.1-second sleep between tickers is intentional to avoid hitting Yahoo Finance rate limits.
