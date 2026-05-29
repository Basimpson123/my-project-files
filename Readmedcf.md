# DCF Analyzer (`dcf.py`)

A discounted cash flow (DCF) valuation tool that fetches live financial data from Yahoo Finance and produces Bear/Base/Bull scenario intrinsic value estimates for a list of tickers.

---

## Configuration

All tunable parameters live at the top of the file.

| Variable | Default | Description |
|---|---|---|
| `TICKERS` | 11 symbols | List of stock tickers to analyze |
| `WACC` | 10% | Base weighted average cost of capital |
| `TERMINAL_GROWTH` | 2.5% | Perpetual growth rate used in terminal value |
| `PROJECTION_YEARS` | 5 | Number of years to project free cash flow |
| `FALLBACK_TAX_RATE` | 25% | Used when reported tax data is missing or invalid |

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

   The net result is identical to the standard formula; the signs are just already baked into the raw data.

2. **Growth Rate** — `calculate_dcf()` derives a base growth rate from the year-over-year FCFF history, capped between −5% and +30%.

3. **DCF** — `run_dcf()` projects FCFF forward for `PROJECTION_YEARS`, discounts each year at `WACC`, adds a Gordon Growth terminal value, and converts enterprise value to per-share intrinsic value using net cash and shares outstanding.

4. **Output** — Prints a scenario table per ticker showing growth rate, WACC, intrinsic value, and upside/downside vs. current price.

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

  Base Growth Rate     : 14.2%
  Terminal Growth Rate : 2.5%
  Projection Period    : 5 years
  Current Price        : $165.30

                              Bear        Base        Bull
  --------------------------------------------------------
  Growth Rate               7.1%       14.2%       21.3%
  WACC                     12.0%       10.0%        8.0%
  Intrinsic Value         $112.45     $198.72     $341.09
  Upside / (Downside)     -32.0%      +20.2%     +106.3%
  ========================================================
```
