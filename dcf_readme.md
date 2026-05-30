# DCF Analyzer (`dcf.py`)

A discounted cash flow (DCF) valuation tool that fetches live financial data from Yahoo Finance and produces Bear/Base/Bull scenario intrinsic value estimates for a list of tickers.

---

## Configuration

All tunable parameters live at the top of the file.

| Variable | Default | Description |
|---|---|---|
| `TICKERS` | 11 symbols | List of stock tickers to analyze |
| `TERMINAL_GROWTH` | 2.5% | Perpetual growth rate used in terminal value |
| `PROJECTION_YEARS` | 5 | Number of years to project free cash flow |
| `FALLBACK_TAX_RATE` | 25% | Used when reported tax data is missing or invalid |
| `FALLBACK_WACC` | 10% | Used when WACC cannot be computed from fundamentals |

### WACC Components

| Variable | Default | Description |
|---|---|---|
| `RISK_FREE_RATE` | 4.1% | Fernandez 2026 survey (97 countries) — alternative: 4.3% (~10-year Treasury yield) |
| `EQUITY_RISK_PREMIUM` | 5.3% | Fernandez 2026 survey (97 countries) — alternative: 5.5% (Damodaran implied ERP) |
| `FALLBACK_BETA` | 1.0 | Used when beta is unavailable |
| `FALLBACK_COST_OF_DEBT` | 5.0% | Used when interest expense data is missing |

### Growth Rate / Base FCFF

| Variable | Default | Description |
|---|---|---|
| `EWMA_DECAY` | 0.85 | Exponential weight decay per year going backward |
| `NORMALIZE_YEARS` | 3 | Years averaged to produce the base FCFF |

### Scenarios

| Scenario | Growth Multiplier | WACC Adjustment |
|---|---|---|
| Bear | 0.5× base growth | +2% |
| Base | 1.0× base growth | 0% |
| Bull | 1.5× base growth | −2% |

---

## How It Works

1. **Fetch** — `fetch_data()` pulls cash flow, income statement, and balance sheet data via `yfinance`. It calculates Free Cash Flow to Firm (FCFF) for each available historical year using:

   **Textbook formula:**
   ```
   FCFF = NOPAT + D&A − ΔWorking Capital − CapEx
   NOPAT = EBIT × (1 − effective tax rate)
   ```

   **In code, all terms are added:**
   ```python
   fcff = nopat + da + wc + capex
   ```

   This is correct — not a bug — because of how Yahoo Finance reports the numbers:
   - **CapEx** is returned as a **negative value** (it is a cash outflow), so adding it is equivalent to subtracting a positive capital expenditure.
   - **Change in Working Capital (`wc`)** is also sign-adjusted by Yahoo Finance — an increase in working capital (cash consumed) comes through as negative, so adding it again matches the textbook subtraction.
   - **CapEx is optional** — asset-light companies (e.g. SaaS) often do not report a separate CapEx line. When the row is absent it defaults to 0, which is appropriate for businesses with negligible physical capital spending.

   `fetch_data()` also collects beta, market cap, interest expense, diluted shares, and average effective tax rate for use in WACC computation.

   **Data-fetching robustness:**
   - Row labels vary across companies and yfinance versions. `get_row()` tries all candidate label names and returns whichever has the **most non-null values**, rather than stopping at the first match. This prevents a sparsely-populated label from shadowing a better one (e.g. Google's D&A appears under both `Depreciation And Amortization` and `Reconciled Depreciation`).
   - Year matching uses **year strings** (`"2024"`) rather than exact pandas timestamps. This avoids silent intersection failures caused by timestamp metadata differences between the income statement and cash flow statement — a known yfinance quirk.

2. **WACC** — `compute_wacc()` derives a per-ticker WACC from fundamentals rather than using a single hardcoded rate:

   ```
   Cost of Equity  = Risk-Free Rate + Beta × Equity Risk Premium   (CAPM)
   Cost of Debt    = Interest Expense / Total Debt  (capped at 15%)
   After-Tax CoD   = Cost of Debt × (1 − Effective Tax Rate)
   WACC            = (Market Cap / Total Capital) × CoE
                   + (Debt / Total Capital) × After-Tax CoD
   ```

   Result is clamped to [5%, 20%]. Falls back to `FALLBACK_WACC` if capital structure data is unavailable.

3. **Growth Rate** — `calculate_dcf()` derives a base growth rate by blending two methods:
   - **CAGR** (geometric mean): `(Most Recent FCFF / Oldest FCFF)^(1/n) − 1` — used when both endpoints are positive.
   - **EWMA-weighted YoY rates**: year-over-year growth rates weighted exponentially so that recent years carry more influence (`EWMA_DECAY = 0.85` per year going backward). Growth is computed as `(curr − prev) / |prev|` rather than `curr/prev − 1` so that years with negative FCFF (e.g. capital-intensive utilities mid-cycle) still produce a correctly-signed rate.

   When both are available they are blended 50/50. The result is capped between −5% and +30%.

4. **Normalized Base FCFF** — The starting FCFF is chosen adaptively:
   - If FCFF grew every year within the `NORMALIZE_YEARS` window (consistent growth), the most recent year is used directly — averaging would only pull it down.
   - If there was any down year within that window (volatile history), the model averages the most recent `NORMALIZE_YEARS` (default 3) years to smooth out one-time anomalies.

5. **DCF** — `run_dcf()` uses a **two-stage growth model**: growth fades linearly from the derived base rate in year 1 down to `TERMINAL_GROWTH` by the final projection year, rather than projecting at a flat rate and cliff-dropping to the terminal assumption. Each year's FCF is discounted at the scenario WACC. A Gordon Growth terminal value is appended at year `PROJECTION_YEARS` and enterprise value is converted to per-share intrinsic value using net cash and diluted shares outstanding.

6. **Diluted Shares** — `fetch_data()` first tries to read `Diluted Average Shares` from the income statement, which captures stock-based compensation dilution. It falls back to `sharesOutstanding` from `ticker.info` if unavailable.

7. **Output** — Prints a scenario table per ticker showing growth rate, WACC, intrinsic value, and upside/downside vs. current price.

---

## Dependencies

```
yfinance
numpy
pandas
```

Install with:

```bash
pip install yfinance numpy pandas
```

---

## Usage

```bash
python dcf.py
```

Edit `TICKERS` at the top of the file to change which stocks are analyzed.

---

## Output Example

```
============================================================
  DCF Analysis (FCFF): QCOM
============================================================
  Historical FCFF (most recent first):
    2024  $    5.12B
    2023  $    3.88B
    ...

  Normalized Base FCFF :  $4.50B  (3-yr avg)
  Base Growth Rate     : 14.2%
  Terminal Growth Rate : 2.5%
  Projection Period    : 5 years  (two-stage fade)
  Beta                 : 1.15
  Computed WACC        : 9.3%
  Current Price        : $165.30

                              Bear        Base        Bull
  --------------------------------------------------------
  Growth Rate               7.1%       14.2%       21.3%
  WACC                     11.3%        9.3%        7.3%
  Intrinsic Value         $118.20     $201.45     $352.87
  Upside / (Downside)     -28.5%      +21.9%     +113.5%
  ========================================================

... (repeated for each ticker) ...


============================================================
  Summary: Upside / (Downside) by Scenario
============================================================
  Ticker          Bear        Base        Bull
  ------------------------------------------
  QCOM          -28.5%      +21.9%     +113.5%
  AMD           -15.2%      +34.7%      +98.3%
  ...
  ==========================================
```

The summary table is printed after all individual analyses and lists every ticker's Bear/Base/Bull upside or downside in one place for quick comparison. Tickers that fail to fetch data are omitted from the summary (they print an error inline).
