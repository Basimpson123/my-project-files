# reverse_dcf.py — Reference Guide

## Purpose

This script runs a **reverse DCF**: instead of projecting cash flows to find a fair value, it takes the current market price as given and solves backward for the **constant annual revenue growth rate** the market is implicitly pricing in. The answer to the question: *"What does the stock need to do to justify this price?"*

## How to Run

```bash
python reverse_dcf.py
```

No arguments. All inputs are variables at the top of the file. Edit them, run, read the output.

---

## All Inputs (top of file)

| Variable | What it is | Default (NVDA) |
|---|---|---|
| `TICKER` | Label only, no data fetching | `"NVDA"` |
| `CURRENT_PRICE` | Share price to solve against | `135.00` |
| `SHARES_OUTSTANDING` | Diluted shares | `24_400e6` |
| `NET_DEBT` | Total debt minus cash. **Negative = net cash.** Used to bridge EV ↔ equity value | `-17_200e6` |
| `BASE_REVENUE` | Starting revenue (LTM). This is year 0 — the model grows from here | `130_497e6` |
| `EBIT_MARGIN` | EBIT / revenue, held constant every year | `0.62` |
| `TAX_RATE` | Effective cash tax rate | `0.15` |
| `DA_PCT_REV` | D&A as % of revenue | `0.02` |
| `CAPEX_PCT_REV` | CapEx as % of revenue (cash outflow) | `0.02` |
| `NWC_PCT_REV_CHANGE` | Working capital investment as % of the *change* in revenue. Zero in a flat year, positive cash drag when growing | `0.05` |
| `WACC` | Discount rate for all cash flows | `0.10` |
| `TERMINAL_GROWTH` | Perpetuity growth rate in the Gordon growth terminal value | `0.03` |
| `FORECAST_YEARS` | Length of the explicit forecast period | `5` |
| `GROWTH_LOW` / `GROWTH_HIGH` | Solver search bounds. If no root exists here, the model prints a no-solution warning | `-0.10` / `0.80` |
| `WACC_STEPS` | WACC offsets used in sensitivity table | `[-2%, -1%, 0%, +1%, +2%]` |
| `TG_STEPS` | Terminal growth offsets used in sensitivity table | `[-1%, -0.5%, 0%, +0.5%, +1%]` |

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

`solve_implied_growth()` wraps `intrinsic_price(g)` in an objective function:

```
objective(g) = intrinsic_price(g) − CURRENT_PRICE
```

`scipy.optimize.brentq` finds the root to `xtol=1e-7`. Before calling brentq, the code checks that `objective(GROWTH_LOW)` and `objective(GROWTH_HIGH)` have opposite signs — if they don't, no root exists in range and the function returns `None`.

---

## Output

```
============================================================
  Reverse DCF: NVDA
============================================================
  ... inputs echoed ...
============================================================

  Implied Revenue Growth Rate: 33.41% per year

  Interpretation: The market is pricing in 33.4% annual
  revenue growth for 5 years to justify a $135.00 share price,
  given a 10.0% WACC and 3.0% terminal growth rate.

  Sensitivity: Implied Revenue Growth (%)
  ---------------------------------------------------------------
  WACC \ TG        2.0%      2.5%      3.0%      3.5%      4.0%
  ---------------------------------------------------------------
    8.0%        27.7%     25.7%     23.4%     21.0%     18.4%
    ...
   *10.0%        36.7%     35.1%   [33.4%]     31.6%     29.6%
  ---------------------------------------------------------------
  * = base WACC row   [ ] = base-case cell
```

The sensitivity table re-solves independently at every WACC × terminal growth combination. `*` marks the base WACC row. `[ ]` marks the exact base-case cell.

---

## Key Design Choices and Gotchas

**Revenue-based, not FCFF-based.** `dcf.py` works from historical FCFF. This script works from revenue forward using margin/ratio assumptions — more transparent but more sensitive to `EBIT_MARGIN` being right.

**Constant margin assumption.** EBIT margin, D&A%, CapEx% are all fixed. They do not converge or change over the forecast period. If you expect margin expansion/compression, the implied growth rate will be misleading — adjust `EBIT_MARGIN` to a normalized/terminal figure before running.

**dNWC is a function of revenue change, not revenue level.** A company growing at 33% has real working capital drag. At 0% growth, dNWC = 0. This is intentional — it penalizes high-growth scenarios more, which is conservative.

**Terminal FCF omits dNWC.** Steady-state perpetuity assumes flat revenue, so dNWC = 0 in the terminal value calculation. This is consistent with the Gordon growth assumption.

**NET_DEBT sign convention.** Positive = the company owes more than it holds (debt-heavy). Negative = net cash (common for large-cap tech). A large negative NET_DEBT *raises* implied growth needed (equity value > EV, so the model needs higher FCFs to match).

**No data fetching.** Unlike `dcf.py`, this script has no `yfinance` calls. All inputs are entered manually. This is intentional — reverse DCF is a "what must be true" exercise, not a historical-data exercise.

**Solver failure.** If the market price implies a growth rate outside `[GROWTH_LOW, GROWTH_HIGH]`, the script prints a no-solution warning. Common causes: price implies negative growth (distressed valuation) or implausibly high growth (speculative). Widen the bounds or check model assumptions.

---

## Companion Script

`dcf.py` — forward DCF using yfinance data, FCFF-based, multi-ticker, with Bear/Base/Bull scenarios. Shares the same formatting conventions and column-aligned output style.

---

## Dependencies

```
scipy   # brentq solver
numpy   # not heavily used; available for future extensions
```

Install: `pip install scipy numpy`
