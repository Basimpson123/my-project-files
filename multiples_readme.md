# Stock Valuation Multiples (`multiples.py`)

Fetches real-time price and valuation data for a watchlist of tickers, benchmarks each against its industry peers, computes implied fair values from six multiples, and prints four formatted tables to the terminal.

## What It Does

### 1. Fetch Watchlist Data
Pulls the following for each ticker using `yfinance`:

| Field | Source |
|---|---|
| Current price | `fast_info.last_price` |
| Previous close / day high / low | `fast_info` |
| Trailing P/E | `info["trailingPE"]` |
| Forward P/E | `info["forwardPE"]` |
| EV/EBITDA | `info["enterpriseToEbitda"]` |
| EV/Revenue | `info["enterpriseToRevenue"]` |
| P/FCF | derived: `marketCap / freeCashflow` |
| PEG ratio | `info["trailingPegRatio"]` |
| EPS / Forward EPS | derived: `price / P/E` |
| FCF per share | derived: `freeCashflow / shares` |
| Earnings growth | `info["earningsGrowth"]` |
| Net debt | derived: `enterpriseValue - marketCap` |

**Current watchlist:** `QCOM, AMD, GOOGL, BWXT, CEG, LHX, LULU, VEEV, DECK, ADBE, NVO, MELI`

### 2. Build Industry Peer Groups
For each unique industry in the watchlist, calls `yf.Industry(key).top_companies`. Excludes watchlist tickers and takes up to **10 peers** per industry (up from 5 previously).

### 3. Fetch Peer Multiples
Fetches all six multiples for each peer. Sanity filters to exclude outliers:

| Multiple | Allowed range |
|---|---|
| P/E, Forward P/E | 0 – 500 |
| EV/EBITDA | 0 – 200 |
| EV/Revenue | 0 – 100 |
| P/FCF | 0 – 500 |
| PEG | 0 – 20 |

### 4. Industry Median Averages
For each industry, computes the **median** of each multiple across filtered peers. Reports the peer count `(n)` alongside each median so you can see when a benchmark is thin.

### 5. Compute Implied Fair Values
For each multiple, derives an implied stock price using the industry median as the benchmark:

| Multiple | Implied price formula |
|---|---|
| P/E | `ind_median_PE × EPS` |
| Forward P/E | `ind_median_Fwd_PE × Forward_EPS` |
| EV/EBITDA | `(ind_median_EV_EBITDA × EBITDA − net_debt) / shares` |
| EV/Revenue | `(ind_median_EV_Rev × revenue − net_debt) / shares` |
| P/FCF | `ind_median_PFCF × FCF_per_share` |
| PEG | `ind_median_PEG × (earnings_growth% ) × EPS` |

A **blended implied price** is the mean of all available implied values. The `vs Price` column shows the percentage upside/downside from the current price to the blended implied.

### 6. Output Tables

**Price Table** — current price vs. previous close and intraday range.

**Multiples Table** — all six multiples for each watchlist ticker.

**Industry Median Multiples** — peer-derived benchmarks with peer counts.

**Implied Fair Value** — implied price per multiple, blended implied, and color-coded `vs Price`:
- Green = ≥ +10% upside
- Yellow = within ±10%
- Red = ≤ −10% (overvalued vs. peers)

Color output requires `colorama`; falls back to plain text if not installed.

## Dependencies

```
yfinance
colorama   (optional, for color output)
statistics (stdlib)
```

Install optional color support:
```bash
pip install colorama
```

## Usage

```bash
python multiples.py
```

No arguments needed. Prints status messages while fetching, then outputs the four tables.
