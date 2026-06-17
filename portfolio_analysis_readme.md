# portfolio_analysis.py — Reference Document

This document is a complete reference for `portfolio_analysis.py`. Load it at the start of a chat session to give the assistant full context before making changes.

---

## Purpose

A self-contained Python script that downloads historical price data for a stock portfolio, computes standard risk/return and diversification metrics, compares the portfolio to a benchmark, and outputs a formatted console summary plus two CSV files. An optional `--plot` flag generates a cumulative return chart.

---

## File Location

```
c:\Users\blake\Code\my-project-files\portfolio_analysis.py
```

---

## Dependencies

```
pip install yfinance pandas numpy scipy matplotlib
```

| Library | Used for |
|---|---|
| `yfinance` | Downloading adjusted-close price history from Yahoo Finance |
| `pandas` | DataFrame manipulation, resampling, alignment |
| `numpy` | Math (sqrt, prod, isclose, etc.) |
| `scipy.stats.linregress` | Beta/alpha regression |
| `matplotlib` | Optional cumulative return chart |

---

## Configuration

At the top of the file, `DEFAULT_PORTFOLIO` holds all runtime settings:

```python
DEFAULT_PORTFOLIO = {
    "tickers": {
        "AAPL": 0.20,
        "MSFT": 0.20,
        "GOOGL": 0.15,
        "AMZN": 0.15,
        "BRK-B": 0.10,
        "JPM":  0.10,
        "JNJ":  0.10,
    },
    "benchmark": "SPY",
    "lookback": "3y",         # yfinance period string: 1y, 2y, 3y, 5y, 10y
    "frequency": "daily",     # "daily" or "monthly"
    "risk_free_rate": 0.041,  # 4.1% — Pablo Fernandez survey of 54 countries, 2025
}
```

All of these values can be overridden at runtime by passing a JSON config file via `--config`.

**Constraints enforced at startup:**
- Weights must sum to 1.0 (tolerance ±0.0001).
- All weights must be non-negative.
- Tickers that fail to download are warned about, dropped, and the remaining weights are renormalized so they still sum to 1.0.

---

## CLI Usage

```bash
# Use DEFAULT_PORTFOLIO defined in the file
python portfolio_analysis.py

# Load tickers/settings from a JSON file
python portfolio_analysis.py --config my_portfolio.json

# Show and save a cumulative return chart
python portfolio_analysis.py --plot

# Both
python portfolio_analysis.py --config my_portfolio.json --plot
```

### JSON config file schema

```json
{
  "tickers":        { "TICKER": weight_float, ... },
  "benchmark":      "SPY",
  "lookback":       "3y",
  "frequency":      "daily",
  "risk_free_rate": 0.04
}
```

All keys except `"tickers"` are optional; missing keys fall back to the same defaults as `DEFAULT_PORTFOLIO`.

---

## Function Map

Every piece of logic lives in one of these functions. `main()` orchestrates the calls in the order listed.

### `validate_weights(weights)`
- Exits immediately with a clear error if weights don't sum to 1.0 or contain negatives.
- Called before any network requests.

### `load_prices(tickers, period) → pd.DataFrame`
- Downloads adjusted-close prices for all tickers (portfolio holdings + benchmark) in a single `yf.download()` call.
- Handles the MultiIndex vs. single-column difference yfinance returns depending on ticker count.
- Warns and drops any ticker that returned all-NaN data.
- Returns a DataFrame indexed by date, one column per ticker.

### `compute_returns(prices, weights, frequency) → (port_rets, hold_rets, weights, periods_per_year)`
- Splits the price DataFrame into only the tickers that are in `weights` (benchmark excluded here).
- Renormalizes weights if any tickers were dropped.
- If `frequency == "monthly"`, resamples prices to month-end (`"ME"`) before computing returns.
- Computes `pct_change()` for each holding → `hold_rets` (DataFrame).
- Dot-products holding returns by the weight vector → `port_rets` (Series named `"portfolio"`).
- Returns `periods_per_year` as 252 (daily) or 12 (monthly) for use in annualizing.

### `risk_metrics(port_rets, rf_annual, periods_per_year) → dict`
Computes five standalone metrics on the portfolio return series:

| Key | Formula summary |
|---|---|
| `"CAGR"` | `(1+r).prod() ^ (ppy/n) - 1` — compound growth annualized |
| `"Annualized Volatility"` | `std(r) * sqrt(ppy)` |
| `"Sharpe Ratio"` | `mean(excess) / std(r) * sqrt(ppy)` where `excess = r - rf_per_period` |
| `"Sortino Ratio"` | `mean(excess)*ppy / downside_dev` — only negative-excess periods enter the denominator |
| `"Max Drawdown"` | Most negative value of `(cumulative - rolling_peak) / rolling_peak` |

Risk-free rate is converted from annual to per-period: `rf_per_period = (1+rf_annual)^(1/ppy) - 1`.

### `benchmark_metrics(port_rets, bench_rets, rf_annual, periods_per_year) → dict`
Runs a linear regression of portfolio excess returns on benchmark excess returns (`scipy.stats.linregress`):

| Key | Meaning |
|---|---|
| `"Beta"` | Regression slope — portfolio sensitivity to benchmark |
| `"Alpha (annualized)"` | `(1 + intercept)^ppy - 1` — per-period intercept annualized |
| `"R-Squared"` | `r_value²` — fraction of variance explained by the benchmark |
| `"Upside Capture"` | `mean(port) / mean(bench)` on periods when benchmark > 0 |
| `"Downside Capture"` | `mean(port) / mean(bench)` on periods when benchmark < 0 |

Both series are date-aligned with `pd.concat(...).dropna()` before the regression.

### `diversification_metrics(hold_rets, weights) → (dict, corr_matrix)`
Quantifies concentration and correlation:

| Key | Meaning |
|---|---|
| `"Number of Holdings"` | Count of tickers in the (possibly renormalized) portfolio |
| `"Top-5 Weight"` | Sum of the five largest position weights |
| `"HHI (Concentration)"` | `sum(w²)` — Herfindahl-Hirschman Index; higher = more concentrated |
| `"Effective N (1/HHI)"` | `1/HHI` — equivalent number of equal-weight positions |
| `"Largest Holding"` | Ticker with the highest weight |
| `"Largest Weight"` | That ticker's weight |

Also returns `corr_matrix`: a DataFrame of pairwise Pearson correlations between holding return series.

### `print_summary(weights, r_metrics, b_metrics, d_metrics, benchmark, rf_rate, frequency)`
Formats and prints a console report with four sections:
1. **Holdings & Weights** — sorted bar chart using `#` characters
2. **Return & Risk Metrics**
3. **Benchmark Metrics**
4. **Diversification**

Percentages are formatted with `:.2%`; ratios with 4 decimal places. Uses the internal `_fmt()` helper.

### `save_outputs(r_metrics, b_metrics, d_metrics, corr_matrix)`
Writes two files to the **same directory as the script** (`Path(__file__).parent`), regardless of where the command is run from:
- `portfolio_metrics.csv` — all metrics merged into a single `Metric / Value` CSV
- `portfolio_correlation.csv` — the full correlation matrix

### `plot_cumulative(port_rets, bench_rets, benchmark)`
Only called when `--plot` is passed. Computes `(1+r).cumprod()` for both series, plots them on a shared axis, saves `portfolio_cumulative.png` (150 dpi) to the **same directory as the script**, and calls `plt.show()`. Gracefully skips if matplotlib is not installed.

---

## Execution Flow (main)

```
parse CLI args
  └─ load JSON config OR use DEFAULT_PORTFOLIO
validate_weights()
load_prices(all_tickers + benchmark, lookback)
  ├─ separate benchmark column from portfolio prices
compute_returns(port_prices, weights, frequency)
  └─ also compute bench_rets separately (with same resampling)
risk_metrics(port_rets, ...)
benchmark_metrics(port_rets, bench_rets, ...)   ← skipped if benchmark failed
diversification_metrics(hold_rets, weights)
print_summary(...)
save_outputs(...)
plot_cumulative(...)   ← only if --plot and benchmark available
```

---

## Output Files

| File | Contents | Always written? |
|---|---|---|
| `portfolio_metrics.csv` | All numeric metrics (two columns: Metric, Value) | Yes |
| `portfolio_correlation.csv` | N×N correlation matrix of holdings | Yes |
| `portfolio_cumulative.png` | Cumulative return chart | Only with `--plot` |

All files are written to the script's own directory (`my-project-files/`) using `Path(__file__).parent`, so they always land in the same place regardless of where you run the command from.

---

## Key Design Decisions

- **Adj Close / `auto_adjust=True`**: Uses the `"Close"` column after yfinance auto-adjusts for splits and dividends, so return calculations are economically accurate.
- **Benchmark handled separately**: The benchmark ticker is downloaded in the same batch as holdings but split off before `compute_returns()` so it never enters the weight vector or holding-return DataFrame.
- **Renormalization on missing tickers**: Rather than hard-failing, dropped tickers cause weights to be rescaled so the math still works. A warning is always printed.
- **Risk-free rate source**: Default is 4.1% (0.041), taken from the Pablo Fernandez survey of 54 countries (2025). Can be overridden per-run via the `risk_free_rate` key in a JSON config file.
- **Per-period risk-free conversion**: The annual rate is compounded down to match return frequency; it is not simply divided by 252 or 12.
- **Sortino denominator**: Only periods where excess return (return minus rf) is negative enter the downside deviation calculation — consistent with the standard definition.
- **Capture ratios**: Computed as mean-return ratios (not cumulative product ratios) for simplicity and robustness on short windows.

---

## Known Limitations / Extension Points

- Weights are assumed static (buy-and-hold); no rebalancing is modeled.
- Capture ratios use simple mean-of-returns, not geometric returns.
- No transaction costs, taxes, or slippage.
- Monthly resampling uses month-end prices only; intra-month volatility is lost.
- No rolling metrics (rolling Sharpe, rolling beta, etc.) — a natural next feature.
- No factor model beyond the single-factor benchmark regression.
