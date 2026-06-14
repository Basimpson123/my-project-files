# DCF Analyzer (`dcf.py`)

A discounted cash flow (DCF) valuation tool that fetches live financial data from Yahoo Finance and produces Bear/Base/Bull scenario intrinsic value estimates for a list of tickers.

---

## Configuration

All tunable parameters live at the top of the file.

| Variable | Default | Description |
|---|---|---|
| `TICKERS` | 10 symbols | List of stock tickers to analyze |
| `TERMINAL_GROWTH` | 2.5% | Perpetual growth rate used in terminal value |
| `PROJECTION_YEARS` | 5 | Number of years to project free cash flow |
| `FALLBACK_TAX_RATE` | 25% | Used when reported tax data is missing or invalid |
| `FALLBACK_WACC` | 10% | Used when WACC cannot be computed from fundamentals |
| `DEBUG` | `False` | When `True`, prints which label `get_row()` selected for every field |
| `SBC_TREATMENT` | `"both"` | Controls how stock-based compensation is handled — see below |

### WACC Components

| Variable | Default | Description |
|---|---|---|
| `RISK_FREE_RATE` | 4.1% | Fernandez 2026 survey (97 countries) — alternative: 4.3% (~10-year Treasury yield) |
| `EQUITY_RISK_PREMIUM` | 5.3% | Fernandez 2026 survey (97 countries) — alternative: 5.5% (Damodaran implied ERP) |
| `FALLBACK_BETA` | 1.0 | Used when beta is unavailable |
| `FALLBACK_COST_OF_DEBT` | 5.0% | Used when interest expense data is missing |

### SBC Treatment

`SBC_TREATMENT` controls how stock-based compensation is incorporated into FCFF. This matters
because the tool's textbook EBIT-based buildup starts from EBIT (which is computed *after* the
SBC expense) and never adds it back — making the result ~$400–500M/yr lower than the
street-reported "OCF − CapEx" figure for heavy-SBC companies (e.g. VEEV, ADBE). Neither figure
is wrong; they answer different questions. The flag makes the choice explicit.

| Value | Behavior |
|---|---|
| `"expense"` | SBC treated as a real economic cost. Conservative; matches the textbook buildup. Recommended for strict valuation. |
| `"addback"` | SBC added back to FCFF. Matches street/OCF-based reported FCF numbers. |
| `"both"` | Runs and prints both treatments per ticker. The spread between them is the visible cost of SBC. |

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
   fcff_expense = nopat + da + wc + capex
   fcff_addback = fcff_expense + sbc
   ```

   The sign conventions are correct — not bugs — because of how Yahoo Finance reports the numbers:
   - **CapEx** is returned as a **negative value** (it is a cash outflow), so adding it is equivalent to subtracting a positive capital expenditure.
   - **Change in Working Capital (`wc`)** is also sign-adjusted by Yahoo Finance — an increase in working capital (cash consumed) comes through as negative, so adding it again matches the textbook subtraction.
   - **CapEx is optional** — asset-light companies (e.g. SaaS) often do not report a separate CapEx line. When the row is absent it defaults to 0, which is appropriate for businesses with negligible physical capital spending.
   - **SBC** (`sbc`) is fetched from the cash flow statement (`Stock Based Compensation` / `Share Based Compensation`). Whether it is added to FCFF is governed by `SBC_TREATMENT` (see Configuration).

   `fetch_data()` also collects operating cash flow (for the reconciliation check), beta, market cap, interest expense, diluted shares, and average effective tax rate.

   **Reconciliation warning:** After computing `fcff_expense` for each year, the tool independently computes `OCF − CapEx` and prints a `[WARN]` line if the two figures diverge by more than 25%. A consistent gap across all historical years is the signature of a definitional mismatch (typically SBC or deferred revenue) rather than a one-off data glitch.

   ```
   [WARN] VEEV 2024: FCFF (0.64B) diverges >25% from OCF−CapEx (1.42B) — likely SBC/deferred-revenue definitional gap.
   ```

   **Data-fetching robustness:**
   - Row labels vary across companies and yfinance versions. `get_row()` tries all candidate label names and returns whichever has the **most non-null values**, rather than stopping at the first match. This prevents a sparsely-populated label from shadowing a better one (e.g. Google's D&A appears under both `Depreciation And Amortization` and `Reconciled Depreciation`).
   - When `DEBUG = True`, `get_row()` prints the winning label and its non-null count for every field, making it easy to audit which series was selected without re-running with breakpoints.
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

With `SBC_TREATMENT = "both"` (default), each ticker prints two blocks back-to-back — one for each treatment — followed by a combined summary table. Any year where the tool's FCFF diverges >25% from `OCF − CapEx` prints a `[WARN]` line during the fetch phase, before the analysis blocks.

```
Fetching data for VEEV...
  [WARN] VEEV 2024: FCFF (0.64B) diverges >25% from OCF−CapEx (1.42B) — likely SBC/deferred-revenue definitional gap.
  [WARN] VEEV 2023: FCFF (0.49B) diverges >25% from OCF−CapEx (1.07B) — likely SBC/deferred-revenue definitional gap.
  ...

============================================================
  DCF Analysis (FCFF): VEEV [SBC as Expense]
============================================================
  Historical FCFF (most recent first):
    2024  $    0.64B
    2023  $    0.49B
    ...

  Base FCFF            : $0.57B  (3-yr avg)
  ...
                              Bear        Base        Bull
  --------------------------------------------------------
  Intrinsic Value          $58.10      $99.40     $172.60
  Upside / (Downside)      -67.1%      -43.6%       -2.0%
  ========================================================

============================================================
  DCF Analysis (FCFF): VEEV [SBC as Add-back]
============================================================
  Historical FCFF (most recent first):
    2024  $    1.42B
    2023  $    1.07B
    ...

  Base FCFF            : $1.25B  (3-yr avg)
  ...
                              Bear        Base        Bull
  --------------------------------------------------------
  Intrinsic Value         $127.80     $218.50     $378.90
  Upside / (Downside)      -27.5%      +24.3%     +115.4%
  ========================================================

... (repeated for each ticker) ...


============================================================
  Summary: Upside / (Downside) by Scenario
============================================================
  Ticker          Bear        Base        Bull
  ------------------------------------------
  QCOM (E)      -28.5%      +21.9%     +113.5%
  QCOM (A)      -18.2%      +38.4%     +134.7%
  VEEV (E)      -67.1%      -43.6%       -2.0%
  VEEV (A)      -27.5%      +24.3%     +115.4%
  ...
  ==========================================
```

`(E)` = SBC as Expense (conservative), `(A)` = SBC as Add-back (street FCF). The spread between the two rows for any ticker is the visible economic cost of its SBC program.

With `SBC_TREATMENT = "expense"` or `"addback"`, only one block prints per ticker and summary rows use the bare ticker symbol. Tickers that fail to fetch data are omitted from the summary (they print an error inline).
