# Monte Carlo DCF (`montecarlo_dcf.py`)

A Monte Carlo version of the DCF in `dcf.py`. Instead of a single value for each assumption, it draws revenue growth, operating margin, WACC and terminal growth from probability distributions. It then runs the DCF thousands of times and reports the range of intrinsic values per share, how often the value beats the current price, and which input drives the result most.

It builds on `dcf.py` and doesn't replace it. Data fetching, WACC, the growth estimate, the growth fade and the discounting all come from `dcf.py`.

---

## Usage

```powershell
python montecarlo_dcf.py ADBE                       # 10,000 simulations
python montecarlo_dcf.py ADBE --sims 50000          # more simulations
python montecarlo_dcf.py ADBE --seed 42             # reproducible results
```

| Argument | Default | Description |
|---|---|---|
| `ticker` | required | Stock ticker. Case doesn't matter (`adbe` works). |
| `--sims` | 10,000 | Number of simulations, minimum 100. |
| `--seed` | none | Random seed. With the same seed and the same Yahoo data, the results are identical. |

Output: a summary in the terminal, plus a histogram saved as `<TICKER>_montecarlo.png` in the folder you run it from.

---

## The Model

Each simulation projects 5 years (`dcf.PROJECTION_YEARS`):

```
Revenue_t = Revenue_0 × (1 + g_1) × … × (1 + g_t)
FCFF_t    = Revenue_t × (operating margin × (1 − tax rate) − reinvestment rate)
Value     = PV(FCFF years 1–5) + PV(terminal value) + cash − debt, ÷ diluted shares
```

- **Growth fade:** year 1 uses the drawn revenue growth. Growth then fades linearly to the drawn terminal growth by year 5, the same fade `dcf.py` uses.
- **Terminal value:** Gordon growth on year-5 FCFF, discounted at the drawn WACC.
- **Operating margin** is EBIT ÷ revenue, held flat across all 5 years and into the terminal value.
- **Reinvestment rate** is `−(D&A + ΔWC + CapEx) / revenue`, averaged over recent years and held fixed. It uses the same FCFF definition as `dcf.py`, so `FCFF = NOPAT − reinvestment`. A negative rate means D&A and working-capital inflows exceed CapEx, which is common for asset-light software companies with deferred revenue.

All simulations are calculated at once as numpy arrays rather than one at a time in a loop, so most of the run time is the Yahoo Finance download.

---

## Configuration

All settings live in the `SETTINGS` block at the top of the file. A value of `None` means "derive it from the company's history at run time"; replace it with a decimal (e.g. `0.12` = 12%) to override.

### Simulated Inputs

| Input | Distribution | Default center | Default spread |
|---|---|---|---|
| Revenue growth (year 1) | Normal | Revenue CAGR / EWMA blend (`dcf.blended_growth`), clamped to −5%…30% | Std dev of historical YoY growth, at least 2% |
| Operating margin | Normal | Average EBIT margin, last 3 years | Std dev of historical margins, at least 1.5% |
| WACC | Triangular | `dcf.compute_wacc()` (CAPM + cost of debt) | ±1.5% |
| Terminal growth | Triangular | 2.5% | 1.5% low / 3.5% high |

| Variable | Default | Description |
|---|---|---|
| `REV_GROWTH_MEAN` / `REV_GROWTH_SD` | `None` | Override the revenue growth center / spread |
| `REV_GROWTH_SD_FLOOR` | 2% | Minimum spread when derived from history |
| `REV_GROWTH_BASE_CLIP` | −5% … 30% | Bounds on the derived center (same clamp as `dcf.py`) |
| `REV_GROWTH_DRAW_CLIP` | −20% … 50% | Bounds on any single draw |
| `MARGIN_MEAN` / `MARGIN_SD` | `None` | Override the margin center / spread |
| `MARGIN_AVG_YEARS` | 3 | Years averaged for the margin center |
| `MARGIN_SD_FLOOR` | 1.5% | Minimum spread when derived from history |
| `MARGIN_DRAW_CLIP` | −30% … 70% | Bounds on any single draw |
| `WACC_MODE` / `WACC_LOW` / `WACC_HIGH` | `None` | Override the WACC triangle |
| `WACC_SPREAD` | 1.5% | Distance from mode to low/high when they are `None` |
| `TG_LOW`, `TG_MODE`, `TG_HIGH` | 1.5% / 2.5% / 3.5% | Terminal growth triangle |

### Fixed Inputs

| Variable | Default | Description |
|---|---|---|
| `TAX_RATE` | `None` → `dcf.py` average effective rate | Tax rate applied to operating profit |
| `REINVEST_RATE` | `None` → 3-year average | Reinvestment as % of revenue. Set to `0.0` for a more conservative view on asset-light names |
| `REINVEST_AVG_YEARS` | 3 | Years averaged for the reinvestment rate |
| `SBC_TREATMENT` | `"expense"` | `"expense"` treats stock-based comp as a real cost; `"addback"` adds it back (see `dcf_readme.md`). No `"both"` option here |

### Guardrails

| Variable | Default | Description |
|---|---|---|
| `MIN_WACC_TG_SPREAD` | 1.0% | Every simulation must have WACC − terminal growth ≥ this gap |
| `MAX_REDRAW_ROUNDS` | 100 | Redraw attempts before giving up with an error |
| `MIN_HISTORY_YEARS` | 3 | Fewer years of revenue data than this is an error |
| `HIST_TRIM_PCT` | 0.5 … 99.5 | Chart x-range only; the statistics use every simulation |

**Keeping terminal growth below WACC.** If terminal growth gets close to WACC, the terminal value explodes, and if it's above WACC the value turns negative. Any simulation that breaks the gap has its WACC and terminal growth redrawn together until it passes. This is rejection sampling: it avoids the artificial spike at the boundary that simply capping the value would create. The terminal summary shows how many pairs were redrawn. With the default ranges it's usually 0.

---

## Output

### Terminal Summary

Example output from November 25, 2026 (live data, so your numbers will differ):

```
================================================================
  Monte Carlo DCF: ADBE   (10,000 simulations, SBC as expense)
================================================================
  History (most recent first)
    Year       Revenue   YoY growth  EBIT margin  Reinvest %
    2025        23.77B        10.5%        37.9%       -5.2%
    2024        21.50B        10.8%        33.0%       -3.8%
    2023        19.41B        10.2%        35.6%       -0.8%
    2022        17.61B                     34.8%       -4.3%

  Simulated inputs
    Revenue growth (yr 1)   Normal      mean  10.5%   sd 2.0%
    Operating margin        Normal      mean  35.5%   sd 2.0%
    WACC                    Triangular  9.4% / 10.9% / 12.4%
    Terminal growth         Triangular  1.5% / 2.5% / 3.5%
  ...
  Current price                  $    232.83
  Base case (inputs at center)   $    255.62
  ------------------------------------------
  Mean                           $    257.84
  Median                         $    255.90
  5th percentile                 $    214.52
  25th percentile                $    236.97
  75th percentile                $    275.79
  95th percentile                $    308.27
  ------------------------------------------
  Sims with value > price              79.9%
  Median upside / (downside)           +9.9%

  What moves the value most (ranked by |rank correlation|)
    Input                Spearman   Value with input at P5 -> P95 (others at center)
    WACC                    -0.67      $292.53 -> $227.22
    Operating margin        +0.44      $234.41 -> $277.92
    Terminal growth         +0.39      $238.10 -> $276.39
    Revenue growth          +0.38      $237.51 -> $274.30
```

| Section | What it shows |
|---|---|
| History | The years the defaults were derived from, to check whether the centers look sensible |
| Simulated / fixed inputs | The exact distribution parameters used in this run |
| Base case | One DCF with every input at its center. It should land close to the median |
| Percentiles | 5th–95th is the range 90% of simulations fall in; 25th–75th is the middle half |
| Sims with value > price | Share of simulations where intrinsic value exceeds the current price |

### Sensitivity Ranking

Two complementary measures, ranked by the first:

- **Spearman rank correlation** (−1 to +1), measured across all simulations. It shows how consistently the value moves when that input moves. The sign gives the direction: higher WACC means lower value, so it's negative.
- **P5 → P95 swing (tornado-style):** the value with one input set to its 5th and then its 95th percentile, holding the others at their centers. This gives the effect in dollars. It shows `n/a` if that combination would break the WACC/terminal-growth gap.

### Histogram

`<TICKER>_montecarlo.png` shows the distribution of intrinsic values. A solid line marks the current price and a dashed line marks the median. If the current price is far outside the distribution, it's noted at the edge of the chart instead, so the bars stay readable. The subtitle notes how many extreme tail values fall outside the chart range.

---

## Error Handling

The script prints a clear message and exits with code 1, rather than crashing, when:

- The ticker doesn't exist, or Yahoo Finance can't be reached
- Yahoo is missing EBIT or D&A (typical for **banks and insurers**, e.g. JPM)
- There's no current price or share count
- There are fewer than 3 consecutive recent years of positive revenue
- The settings are invalid, e.g. triangle low > high, a negative spread, or WACC and terminal growth ranges that can't satisfy the required gap

Invalid command-line arguments (e.g. `--sims 5`) are rejected by argparse with exit code 2.

---

## Limitations

- **Flat margins.** Margin is held constant, which badly undervalues companies whose margins are still expanding. CRWD, with roughly 1.5% EBIT margin, comes out near $3/share against a ~$209 price. For such names, override `MARGIN_MEAN` with your view of steady-state margin, and treat the output with caution.
- **Short history.** Yahoo provides about 4 years of annual statements, so the derived spreads rest on only 2–3 data points. That's why the floors exist. Widen the ranges in `SETTINGS` if you think the defaults are too tight.
- **Independent inputs.** The inputs are drawn independently. In reality, high growth and high margins often go together, which this model doesn't capture.
- **Negative reinvestment** for asset-light companies carries into the terminal value, which is slightly generous. Set `REINVEST_RATE = 0.0` to test that.
- **Data warnings.** The `[WARN]` lines about FCFF diverging from OCF − CapEx come from `dcf.fetch_data()`. They're informational only; see `dcf_readme.md`.

---

## Relationship to `dcf.py`

`montecarlo_dcf.py` imports `dcf.py` and uses these functions. Renaming or changing their signatures will break it:

| Function | Used for |
|---|---|
| `fetch_data()` | Yahoo Finance pull: statements, price, shares, cash, debt, revenue, FCFF components |
| `compute_wacc()` | Center of the WACC triangle |
| `blended_growth()` | Center of the revenue growth distribution |
| `fade_growth()` | Year-by-year growth path, from initial growth to terminal growth |
| `value_per_share()` | Discounting, terminal value and the equity bridge, vectorized across simulations |

`dcf.py` also reads `PROJECTION_YEARS`. Changing it in `dcf.py` changes both scripts.

---

## Dependencies

```
yfinance
numpy
matplotlib
```

```powershell
pip install yfinance numpy matplotlib
```
