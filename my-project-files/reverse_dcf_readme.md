# reverse_dcf.py — Reference Guide

## Purpose

This script runs a **reverse DCF**: instead of projecting cash flows to find a fair value, it takes the current market price as given and solves backward for the **constant annual revenue growth rate** the market is implicitly pricing in. The answer to the question: *"What does the stock need to do to justify this price?"*

Supports multiple tickers in a single run. Financial data is auto-fetched from Yahoo Finance — only the ticker(s) and judgment-call assumptions need to be set manually.

## How to Run

```bash
python reverse_dcf.py
```

No arguments. All inputs are variables at the top of the file. Edit them, run, read the output.

---

## All Inputs (top of file)

### Tickers

| Variable | What it is | Example |
|---|---|---|
| `TICKERS` | List of ticker symbols to analyze, in order | `["AAAA", "BBBB"]` |
| `OVERRIDES` | Per-ticker manual values that take precedence over auto-fetch | `{"AAAA": {"EBIT_MARGIN": 0.55}}` |

### Auto-Fetched Fields

These default to `None`, which means the value is pulled from Yahoo Finance (LTM where applicable). Set a value to override for all tickers; use `OVERRIDES` to override for a specific ticker only.

| Variable | What it is | Source |
|---|---|---|
| `CURRENT_PRICE` | Share price to solve against | `info['currentPrice']` |
| `SHARES_OUTSTANDING` | Diluted shares | `info['sharesOutstanding']` |
| `NET_DEBT` | Total debt minus cash. **Negative = net cash.** | Most-recent quarter balance sheet |
| `BASE_REVENUE` | Starting revenue (LTM). This is year 0 — the model grows from here | Sum of last 4 quarters |
| `EBIT_MARGIN` | EBIT / revenue, held constant every year | LTM Operating Income / LTM Revenue |
| `TAX_RATE` | Effective cash tax rate | LTM Tax Provision / LTM Pretax Income |
| `DA_PCT_REV` | D&A as % of revenue | LTM D&A / LTM Revenue |
| `CAPEX_PCT_REV` | CapEx as % of revenue (cash outflow) | LTM CapEx / LTM Revenue |

### Manual Assumptions

These are judgment calls and are always set manually. They apply to all tickers unless overridden via `OVERRIDES`.

| Variable | What it is | Default |
|---|---|---|
| `NWC_PCT_REV_CHANGE` | Working capital investment as % of the *change* in revenue. Zero in a flat year, positive cash drag when growing | `0.05` |
| `WACC` | Discount rate for all cash flows | `0.10` |
| `TERMINAL_GROWTH` | Perpetuity growth rate in the Gordon growth terminal value | `0.03` |
| `FORECAST_YEARS` | Length of the explicit forecast period | `5` |
| `GROWTH_LOW` / `GROWTH_HIGH` | Solver search bounds. If no root exists here, the model prints a no-solution warning | `-0.10` / `0.80` |
| `WACC_STEPS` | WACC offsets used in sensitivity table | `[-2%, -1%, 0%, +1%, +2%]` |
| `TG_STEPS` | Terminal growth offsets used in sensitivity table | `[-1%, -0.5%, 0%, +0.5%, +1%]` |

---

## Per-Ticker Overrides

Use `OVERRIDES` to pin any value for a specific ticker without affecting others:

```python
OVERRIDES = {
    "TICK1": {"EBIT_MARGIN": 0.55},
    "TICK2": {"WACC": 0.09, "TAX_RATE": 0.12},
}
```

Any key from the auto-fetched or manual assumptions tables is valid. Values set here take precedence over both auto-fetch and module-level defaults.

---

## Model Logic (step by step)

### 1. Enterprise value from market price

```
Market Cap  = CURRENT_PRICE × SHARES_OUTSTANDING
EV          = Market Cap + NET_DEBT
```

NET_DEBT is added because `equity value = EV − net debt`. A negative NET_DEBT (net cash) makes EV smaller than market cap.

### 2. Revenue projection

For each year `t` in `[1 … FORECAST_YEARS]`:

```
Revenue(t) = Revenue(t-1) × (1 + g)
```

`g` is the candidate growth rate the solver is testing.

### 3. Unlevered free cash flow each year

```
EBIT    = Revenue(t) × EBIT_MARGIN
NOPAT   = EBIT × (1 − TAX_RATE)
D&A     = Revenue(t) × DA_PCT_REV
CapEx   = Revenue(t) × CAPEX_PCT_REV
dNWC    = (Revenue(t) − Revenue(t-1)) × NWC_PCT_REV_CHANGE

UFCF(t) = NOPAT + D&A − CapEx − dNWC
```

D&A and CapEx are modeled as fixed percentages of revenue (not growing independently). dNWC is zero when revenue is flat and a cash drag when revenue is growing.

### 4. Terminal value

Computed on the **final forecast year's revenue**, treating it as a steady state (dNWC = 0):

```
Terminal FCF  = [Revenue(N) × EBIT_MARGIN × (1 − TAX_RATE) + D&A − CapEx] × (1 + TERMINAL_GROWTH)
Terminal PV   = Terminal FCF / (WACC − TERMINAL_GROWTH) / (1 + WACC)^N
```

Gordon growth model. The model guards against `WACC <= TERMINAL_GROWTH` and returns `None` in that case.

### 5. Sum to enterprise value → share price

```
EV_model      = sum of discounted UFCFs + Terminal PV
Equity_model  = EV_model − NET_DEBT
Price_model   = Equity_model / SHARES_OUTSTANDING
```

### 6. Solver

`solve_implied_growth()` wraps `intrinsic_price(g, cfg)` in an objective function:

```
objective(g) = intrinsic_price(g) − CURRENT_PRICE
```

`scipy.optimize.brentq` finds the root to `xtol=1e-7`. Before calling brentq, the code checks that `objective(GROWTH_LOW)` and `objective(GROWTH_HIGH)` have opposite signs — if they don't, no root exists in range and the function returns `None`.

---

## Output

One full block is printed per ticker:

```
============================================================
  Reverse DCF: TICK
============================================================
  Current Price        :     $XXX.XX (auto)
  Shares Outstanding   :       X.XXB (auto)
  ...
============================================================

  Implied Revenue Growth Rate: XX.XX% per year

  Interpretation: The market is pricing in XX.X% annual
  revenue growth for 5 years to justify a $XXX.XX share price,
  given a 10.0% WACC and 3.0% terminal growth rate.

  Sensitivity: Implied Revenue Growth (%)
  ---------------------------------------------------------------
  WACC \ TG        2.0%      2.5%      3.0%      3.5%      4.0%
  ---------------------------------------------------------------
    8.0%         X.X%      X.X%      X.X%      X.X%      X.X%
    ...
   *10.0%         X.X%      X.X%    [X.X%]      X.X%      X.X%
  ---------------------------------------------------------------
  * = base WACC row   [ ] = base-case cell
```

Values pulled from Yahoo Finance are tagged `(auto)`. The sensitivity table re-solves independently at every WACC × terminal growth combination.

---

## Key Design Choices and Gotchas

**Revenue-based, not FCFF-based.** `dcf.py` works from historical FCFF. This script works from revenue forward using margin/ratio assumptions — more transparent but more sensitive to `EBIT_MARGIN` being right.

**Auto-fetch uses LTM figures.** Revenue, EBIT margin, tax rate, D&A%, and CapEx% are all derived from the sum of the last 4 reported quarters. Net debt is from the most recent quarter's balance sheet. Verify these against the company's actual filings — yfinance field names can vary.

**Constant margin assumption.** EBIT margin, D&A%, CapEx% are all fixed. They do not converge or change over the forecast period. If you expect margin expansion/compression, the implied growth rate will be misleading — adjust `EBIT_MARGIN` (via `OVERRIDES` or the module-level variable) to a normalized/terminal figure before running.

**dNWC is a function of revenue change, not revenue level.** A company growing at 33% has real working capital drag. At 0% growth, dNWC = 0. This is intentional — it penalizes high-growth scenarios more, which is conservative.

**Terminal FCF omits dNWC.** Steady-state perpetuity assumes flat revenue, so dNWC = 0 in the terminal value calculation. This is consistent with the Gordon growth assumption.

**NET_DEBT sign convention.** Positive = the company owes more than it holds (debt-heavy). Negative = net cash (common for large-cap tech). A large negative NET_DEBT *raises* implied growth needed (equity value > EV, so the model needs higher FCFs to match).

**Solver failure.** If the market price implies a growth rate outside `[GROWTH_LOW, GROWTH_HIGH]`, the script prints a no-solution warning. Common causes: price implies negative growth (distressed valuation) or implausibly high growth (speculative). Widen the bounds or check model assumptions.

---

## Companion Script

`dcf.py` — forward DCF using yfinance data, FCFF-based, multi-ticker, with Bear/Base/Bull scenarios. Shares the same formatting conventions and column-aligned output style.

---

## Dependencies

```
scipy     # brentq solver
numpy     # array utilities
yfinance  # auto-fetch price, shares, financials
```

Install: `pip install scipy numpy yfinance`
