"""
montecarlo_dcf.py - Monte Carlo DCF valuation for a single ticker.

Builds on dcf.py: reuses its yfinance data pull (fetch_data), WACC (compute_wacc),
historical growth estimate (blended_growth), growth fade (fade_growth) and
discounting / terminal value / equity bridge (value_per_share).

Instead of one value per assumption, each key input is drawn from a distribution
and the DCF is run for every draw at once with numpy (no Python loop over sims).

Model, per simulation:
    Revenue_t = Revenue_0 * prod(1 + g_t)    g fades linearly from the drawn
                                             growth (yr 1) to the drawn terminal
                                             growth (yr N), same fade as dcf.py
    FCFF_t    = Revenue_t * (margin * (1 - tax) - reinvestment rate)
    Value     = PV(FCFF_1..N) + PV(terminal value) + cash - debt, / diluted shares

Usage (PowerShell):
    python montecarlo_dcf.py ADBE
    python montecarlo_dcf.py ADBE --sims 50000 --seed 42
"""
import argparse
import sys

import numpy as np

import dcf  # shared data pull + DCF engine (dcf.py in this folder)


# ── SETTINGS ──────────────────────────────────────────────────────────────────
# Any value set to None is derived from the company's own history at run time.
# Replace None with a number (decimal, e.g. 0.12 = 12%) to override it.

DEFAULT_SIMS = 10_000

# "expense" -> SBC is a real cost (conservative; dcf.py's recommended setting)
# "addback" -> add SBC back to FCFF, like street / OCF-based FCF
SBC_TREATMENT = "expense"

# Revenue growth ~ Normal(mean, sd)
# This is the YEAR-1 growth rate; it fades linearly to the drawn terminal growth
# by the last projection year (dcf.PROJECTION_YEARS = 5), same as dcf.py.
REV_GROWTH_MEAN      = None           # None -> CAGR/EWMA blend of revenue (dcf.blended_growth)
REV_GROWTH_SD        = None           # None -> std dev of historical YoY revenue growth
REV_GROWTH_SD_FLOOR  = 0.02           # minimum sd when derived from history
REV_GROWTH_BASE_CLIP = (-0.05, 0.30)  # bounds on the derived mean (same clamp as dcf.py)
REV_GROWTH_DRAW_CLIP = (-0.20, 0.50)  # bounds on any single draw

# Operating margin (EBIT / revenue) ~ Normal(mean, sd), held flat over the projection
MARGIN_MEAN       = None              # None -> average of the last MARGIN_AVG_YEARS
MARGIN_AVG_YEARS  = 3
MARGIN_SD         = None              # None -> std dev of historical margins
MARGIN_SD_FLOOR   = 0.015
MARGIN_DRAW_CLIP  = (-0.30, 0.70)

# WACC ~ Triangular(low, mode, high)
WACC_MODE   = None                    # None -> dcf.compute_wacc() (CAPM + cost of debt)
WACC_LOW    = None                    # None -> mode - WACC_SPREAD
WACC_HIGH   = None                    # None -> mode + WACC_SPREAD
WACC_SPREAD = 0.015

# Terminal growth ~ Triangular(low, mode, high)
TG_LOW, TG_MODE, TG_HIGH = 0.015, 0.025, 0.035   # dcf.py uses a fixed 2.5%

# Every simulation must satisfy: WACC - terminal growth >= MIN_WACC_TG_SPREAD.
# Offending (WACC, terminal growth) pairs are redrawn together until they pass.
MIN_WACC_TG_SPREAD = 0.01
MAX_REDRAW_ROUNDS  = 100

# Fixed (not simulated) inputs
TAX_RATE           = None   # None -> dcf.py's average historical effective tax rate
REINVEST_RATE      = None   # None -> avg of -(D&A + dWC + CapEx [+ SBC]) / revenue
REINVEST_AVG_YEARS = 3

MIN_HISTORY_YEARS  = 3            # need >= 2 YoY growth rates to estimate a spread
HIST_TRIM_PCT      = (0.5, 99.5)  # chart x-range only; stats use every simulation
# ─────────────────────────────────────────────────────────────────────────────


class InputError(Exception):
    """Bad ticker, missing data, or unusable settings - reported without a traceback."""


# ── 1. DATA ──────────────────────────────────────────────────────────────────
def load_company(ticker):
    """Pull statements via dcf.fetch_data() and check everything the model needs."""
    try:
        data = dcf.fetch_data(ticker)
    except Exception as e:  # network errors, yfinance HTTP 404s, parse failures
        raise InputError(
            f"Could not download data for '{ticker}' from Yahoo Finance "
            f"({type(e).__name__}: {e}).\nCheck the ticker symbol and your internet connection."
        )

    if data is None:
        raise InputError(
            f"No usable financial statements for '{ticker}'. Either the ticker does not exist, "
            "or Yahoo is missing EBIT / D&A (common for banks, insurers and recent IPOs)."
        )
    if not data["price"] or data["price"] <= 0:
        raise InputError(f"No current price available for '{ticker}'.")
    if not data["shares"] or data["shares"] <= 0:
        raise InputError(f"No share count available for '{ticker}'.")

    # Use the unbroken run of recent years that have positive revenue
    revenue = data["revenue_values"]
    n = 0
    while n < len(revenue) and np.isfinite(revenue[n]) and revenue[n] > 0:
        n += 1
    if n < MIN_HISTORY_YEARS:
        raise InputError(
            f"'{ticker}' has only {n} recent year(s) of usable revenue data; "
            f"need at least {MIN_HISTORY_YEARS}."
        )

    hist = {
        "years":   data["dates"][:n],
        "revenue": revenue[:n],
        "ebit":    data["ebit_values"][:n],
        "da":      data["da_values"][:n],
        "capex":   data["capex_values"][:n],
        "wc":      data["wc_values"][:n],
        "sbc":     data["sbc_values"][:n],
    }
    return data, hist


# ── 2. ASSUMPTIONS (distribution parameters) ─────────────────────────────────
def derive_assumptions(data, hist):
    """Turn history + SETTINGS into the parameters of each input distribution."""
    rev     = hist["revenue"]              # most recent first
    yoy     = rev[:-1] / rev[1:] - 1       # YoY revenue growth, most recent first
    margins = hist["ebit"] / rev

    # Reinvestment as % of revenue. dcf.py: FCFF = NOPAT + D&A + dWC + CapEx (+ SBC)
    # (CapEx is negative in yfinance), so FCFF / revenue = margin*(1-t) - reinvest_rate.
    non_nopat = hist["da"] + hist["wc"] + hist["capex"]
    if SBC_TREATMENT == "addback":
        non_nopat = non_nopat + hist["sbc"]
    reinvest_hist = -non_nopat / rev

    a = {
        "years":         hist["years"],
        "rev_hist":      rev,
        "yoy_hist":      yoy,
        "margin_hist":   margins,
        "reinvest_hist": reinvest_hist,
        "revenue0":      float(rev[0]),
        "cash":          data["cash"],
        "debt":          data["debt"],
        "shares":        data["shares"],
        "price":         data["price"],
    }

    # Revenue growth: normal
    if REV_GROWTH_MEAN is not None:
        a["growth_mean"] = REV_GROWTH_MEAN
    else:
        a["growth_mean"] = float(np.clip(dcf.blended_growth(rev), *REV_GROWTH_BASE_CLIP))
    a["growth_sd"] = (REV_GROWTH_SD if REV_GROWTH_SD is not None
                      else max(float(np.std(yoy, ddof=1)), REV_GROWTH_SD_FLOOR))

    # Operating margin: normal
    a["margin_mean"] = (MARGIN_MEAN if MARGIN_MEAN is not None
                        else float(np.mean(margins[:MARGIN_AVG_YEARS])))
    a["margin_sd"] = (MARGIN_SD if MARGIN_SD is not None
                      else max(float(np.std(margins, ddof=1)), MARGIN_SD_FLOOR))

    # WACC: triangular around dcf.py's fundamental WACC
    mode = WACC_MODE if WACC_MODE is not None else dcf.compute_wacc(data)
    a["wacc_mode"] = mode
    a["wacc_low"]  = WACC_LOW if WACC_LOW is not None else mode - WACC_SPREAD
    a["wacc_high"] = WACC_HIGH if WACC_HIGH is not None else mode + WACC_SPREAD

    # Terminal growth: triangular, straight from SETTINGS
    a["tg_low"], a["tg_mode"], a["tg_high"] = TG_LOW, TG_MODE, TG_HIGH

    # Fixed inputs
    a["tax_rate"] = TAX_RATE if TAX_RATE is not None else data["avg_tax_rate"]
    a["reinvest_rate"] = (REINVEST_RATE if REINVEST_RATE is not None
                          else float(np.mean(reinvest_hist[:REINVEST_AVG_YEARS])))

    validate_assumptions(a)
    return a


def validate_assumptions(a):
    """Catch settings that would make numpy error out or the DCF meaningless."""
    for name, lo, mode, hi in [("WACC", a["wacc_low"], a["wacc_mode"], a["wacc_high"]),
                               ("Terminal growth", a["tg_low"], a["tg_mode"], a["tg_high"])]:
        if not (lo <= mode <= hi and lo < hi):
            raise InputError(f"{name} triangle must satisfy low <= mode <= high and low < high "
                             f"(got {lo:.2%} / {mode:.2%} / {hi:.2%}).")
    if a["wacc_low"] <= 0:
        raise InputError(f"WACC low end must be positive (got {a['wacc_low']:.2%}).")
    if a["growth_sd"] < 0 or a["margin_sd"] < 0:
        raise InputError("Standard deviations in SETTINGS cannot be negative.")
    # Is it even possible to draw a valid (WACC, terminal growth) pair?
    if a["wacc_high"] - a["tg_low"] < MIN_WACC_TG_SPREAD:
        raise InputError(
            f"WACC range ({a['wacc_low']:.2%}-{a['wacc_high']:.2%}) and terminal growth range "
            f"({a['tg_low']:.2%}-{a['tg_high']:.2%}) leave no room for the required "
            f"{MIN_WACC_TG_SPREAD:.2%} gap. Adjust them in SETTINGS."
        )


# ── 3. RANDOM DRAWS ──────────────────────────────────────────────────────────
def draw_inputs(a, n, rng):
    """Draw n values of each uncertain input. Returns (dict of arrays, # pairs redrawn)."""
    growth = np.clip(rng.normal(a["growth_mean"], a["growth_sd"], n), *REV_GROWTH_DRAW_CLIP)
    margin = np.clip(rng.normal(a["margin_mean"], a["margin_sd"], n), *MARGIN_DRAW_CLIP)
    wacc   = rng.triangular(a["wacc_low"], a["wacc_mode"], a["wacc_high"], n)
    tg     = rng.triangular(a["tg_low"], a["tg_mode"], a["tg_high"], n)

    # Terminal growth must stay below WACC (by MIN_WACC_TG_SPREAD) or the terminal
    # value explodes / turns negative. Redraw offending pairs together (rejection
    # sampling) rather than capping, so no artificial pile-up at the boundary.
    bad = (wacc - tg) < MIN_WACC_TG_SPREAD
    n_redrawn = int(bad.sum())
    for _ in range(MAX_REDRAW_ROUNDS):
        if not bad.any():
            break
        k = int(bad.sum())
        wacc[bad] = rng.triangular(a["wacc_low"], a["wacc_mode"], a["wacc_high"], k)
        tg[bad]   = rng.triangular(a["tg_low"], a["tg_mode"], a["tg_high"], k)
        bad = (wacc - tg) < MIN_WACC_TG_SPREAD
    if bad.any():
        raise InputError("Could not draw WACC / terminal growth pairs with the required gap; "
                         "the two ranges overlap too much. Adjust them in SETTINGS.")

    inputs = {
        "Revenue growth":   growth,
        "Operating margin": margin,
        "WACC":             wacc,
        "Terminal growth":  tg,
    }
    return inputs, n_redrawn


# ── 4. DCF FOR EVERY SIMULATION (vectorized) ─────────────────────────────────
def intrinsic_values(a, growth, margin, wacc, tg):
    """
    Value per share for arrays (or scalars) of inputs. Arrays have shape (n_sims,);
    the projection adds a year axis -> (n_sims, PROJECTION_YEARS).
    """
    growth_path = dcf.fade_growth(growth, tg, dcf.PROJECTION_YEARS)       # (n, years)
    revenue     = a["revenue0"] * np.cumprod(1 + growth_path, axis=-1)    # (n, years)
    fcff_margin = (np.asarray(margin, dtype=float)[..., np.newaxis] * (1 - a["tax_rate"])
                   - a["reinvest_rate"])
    fcff        = revenue * fcff_margin
    return dcf.value_per_share(fcff, wacc, tg, a["cash"], a["debt"], a["shares"])


def center_inputs(a):
    """Base case: every input at the center of its distribution."""
    return {
        "Revenue growth":   a["growth_mean"],
        "Operating margin": a["margin_mean"],
        "WACC":             a["wacc_mode"],
        "Terminal growth":  a["tg_mode"],
    }


def _call(a, inp):
    return intrinsic_values(a, inp["Revenue growth"], inp["Operating margin"],
                            inp["WACC"], inp["Terminal growth"])


# ── 5. SENSITIVITY ───────────────────────────────────────────────────────────
def spearman(x, y):
    """Rank correlation: how consistently y rises (or falls) as x rises. -1 … +1."""
    if np.ptp(x) == 0:  # input held constant (sd = 0 in SETTINGS)
        return np.nan
    rx = np.argsort(np.argsort(x))
    ry = np.argsort(np.argsort(y))
    return float(np.corrcoef(rx, ry)[0, 1])


def sensitivity(a, inputs, values):
    """
    For each input: Spearman correlation with value across all sims, plus a
    tornado-style swing = value with that input at its 5th vs 95th percentile
    while every other input sits at its center.
    """
    center = center_inputs(a)
    rows = []
    for name, draws in inputs.items():
        swing = []
        for pct in (5, 95):
            scenario = dict(center, **{name: float(np.percentile(draws, pct))})
            if scenario["WACC"] - scenario["Terminal growth"] < MIN_WACC_TG_SPREAD:
                swing.append(np.nan)  # this one-at-a-time combo breaks the WACC > g rule
            else:
                swing.append(float(_call(a, scenario)))
        rows.append({"name": name, "rho": spearman(draws, values),
                     "lo": swing[0], "hi": swing[1]})
    # Rank by strength of correlation (NaN last)
    rows.sort(key=lambda r: -abs(r["rho"]) if np.isfinite(r["rho"]) else 1)
    return rows


# ── 6. OUTPUT ────────────────────────────────────────────────────────────────
def fmt_money(v):
    """Right-aligned dollar column for the summary table."""
    return f"${v:>10,.2f}" if np.isfinite(v) else f"{'n/a':>11}"


def short_money(v, decimals=2):
    """Compact dollar value for inline use, e.g. $1,234.50 or -$3.08."""
    if not np.isfinite(v):
        return "n/a"
    return f"{'-' if v < 0 else ''}${abs(v):,.{decimals}f}"


def print_report(ticker, a, values, n_redrawn, base_value, sens):
    n     = len(values)
    price = a["price"]
    w     = 64

    print(f"\n{'=' * w}")
    print(f"  Monte Carlo DCF: {ticker}   ({n:,} simulations, SBC as {SBC_TREATMENT})")
    print(f"{'=' * w}")

    # History the defaults were derived from
    print("  History (most recent first)")
    print(f"    {'Year':<6}{'Revenue':>12}{'YoY growth':>13}{'EBIT margin':>13}{'Reinvest %':>12}")
    for i, year in enumerate(a["years"]):
        yoy = f"{a['yoy_hist'][i]:>12.1%}" if i < len(a["yoy_hist"]) else f"{'':>12}"
        print(f"    {year:<6}{a['rev_hist'][i] / 1e9:>11.2f}B {yoy}"
              f"{a['margin_hist'][i]:>13.1%}{a['reinvest_hist'][i]:>12.1%}")

    # Distribution parameters actually used
    print("\n  Simulated inputs")
    print(f"    {'Revenue growth (yr 1)':<24}Normal      mean {a['growth_mean']:>6.1%}   sd {a['growth_sd']:.1%}")
    print(f"    {'Operating margin':<24}Normal      mean {a['margin_mean']:>6.1%}   sd {a['margin_sd']:.1%}")
    print(f"    {'WACC':<24}Triangular  {a['wacc_low']:.1%} / {a['wacc_mode']:.1%} / {a['wacc_high']:.1%}")
    print(f"    {'Terminal growth':<24}Triangular  {a['tg_low']:.1%} / {a['tg_mode']:.1%} / {a['tg_high']:.1%}")
    print("  Fixed inputs")
    print(f"    Tax rate {a['tax_rate']:.1%}   Reinvestment {a['reinvest_rate']:.1%} of revenue   "
          f"Projection {dcf.PROJECTION_YEARS} yrs")
    print(f"    Revenue ${a['revenue0'] / 1e9:.2f}B   Cash ${a['cash'] / 1e9:.2f}B   "
          f"Debt ${a['debt'] / 1e9:.2f}B   Shares {a['shares'] / 1e6:,.1f}M")
    print(f"  WACC/terminal-growth pairs redrawn to keep g < WACC - {MIN_WACC_TG_SPREAD:.1%}: "
          f"{n_redrawn:,} ({n_redrawn / n:.2%})")

    # Results
    pct = np.percentile(values, [5, 25, 50, 75, 95])
    above = float(np.mean(values > price))
    L = 31  # label column width
    print(f"\n  {'Current price':<{L}}{fmt_money(price)}")
    print(f"  {'Base case (inputs at center)':<{L}}{fmt_money(base_value)}")
    print(f"  {'-' * (L + 11)}")
    print(f"  {'Mean':<{L}}{fmt_money(float(values.mean()))}")
    print(f"  {'Median':<{L}}{fmt_money(pct[2])}")
    for label, v in zip(["5th percentile", "25th percentile", "75th percentile", "95th percentile"],
                        [pct[0], pct[1], pct[3], pct[4]]):
        print(f"  {label:<{L}}{fmt_money(v)}")
    print(f"  {'-' * (L + 11)}")
    print(f"  {'Sims with value > price':<{L}}{above:>11.1%}")
    print(f"  {'Median upside / (downside)':<{L}}{pct[2] / price - 1:>+11.1%}")

    # Sensitivity
    print("\n  What moves the value most (ranked by |rank correlation|)")
    print(f"    {'Input':<20}{'Spearman':>9}   Value with input at P5 -> P95 (others at center)")
    for r in sens:
        rho = f"{r['rho']:>+9.2f}" if np.isfinite(r["rho"]) else f"{'n/a':>9}"
        print(f"    {r['name']:<20}{rho}   {short_money(r['lo']):>10} -> {short_money(r['hi'])}")
    print(f"{'=' * w}")
    return pct[2], above


def save_histogram(ticker, values, price, median, above, path):
    """Histogram of intrinsic values with the current price and median marked."""
    import matplotlib
    matplotlib.use("Agg")  # render straight to file, no window
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, StrMethodFormatter

    # Trim extreme tails for readability only. Stretch the axis to include the price
    # only if it's reasonably close; otherwise the bars would shrink to slivers and
    # the price is shown as an off-chart marker instead.
    lo, hi = np.percentile(values, HIST_TRIM_PCT)
    span   = hi - lo
    price_on_chart = (lo - 0.25 * span) <= price <= (hi + 0.25 * span)
    if price_on_chart:
        lo, hi = min(lo, price), max(hi, price)
    shown  = values[(values >= lo) & (values <= hi)]
    hidden = len(values) - len(shown)

    BAR, INK, INK_2, GRID = "#257de0", "#191919", "#666666", "#e6e6e6"

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
    counts, _, _ = ax.hist(shown, bins=60, color=BAR, edgecolor="white", linewidth=0.6)
    ax.set_ylim(0, counts.max() * 1.15)  # headroom above the bars for line labels

    # Reference lines, labeled directly instead of a legend
    ax.axvline(median, color=INK_2, linewidth=1.5, linestyle="--")
    if price_on_chart:
        ax.axvline(price, color=INK, linewidth=2)
    xmin, xmax = ax.get_xlim()
    xform = ax.get_xaxis_transform()  # x in data units, y in axes fraction

    def label_side(x, prefer_left):
        """Put the label on the preferred side of its line unless it would hit the plot edge."""
        frac = (x - xmin) / (xmax - xmin)
        if prefer_left and frac < 0.25:
            return False
        if not prefer_left and frac > 0.75:
            return True
        return prefer_left

    # Default: each label on the outer side of its line, so the two don't collide
    price_left = price <= median
    median_on_left = label_side(median, prefer_left=not price_left)
    median_y = 0.985
    if price_on_chart:
        price_on_left = label_side(price, prefer_left=price_left)
        ax.text(price, 0.985, f" Current price {short_money(price)} ", transform=xform,
                ha="right" if price_on_left else "left", va="top", fontsize=9,
                color=INK, fontweight="bold")
        if median_on_left == price_left:  # labels now face each other: stagger vertically
            median_y = 0.925
    else:
        # Price is far outside the distribution: note it at the edge it lies beyond
        right = price > hi
        ax.text(0.995 if right else 0.005, 0.985,
                f"Current price {short_money(price)} {'→' if right else '←'} (off chart)",
                transform=ax.transAxes, ha="right" if right else "left", va="top",
                fontsize=9, color=INK, fontweight="bold")
    ax.text(median, median_y, f" Median {short_money(median)} ", transform=xform,
            ha="right" if median_on_left else "left", va="top", fontsize=9, color=INK_2)

    ax.set_title(f"{ticker}: Monte Carlo DCF intrinsic value per share",
                 loc="left", fontsize=13, color=INK, pad=22)
    note = f"{len(values):,} simulations  |  {above:.0%} above current price"
    if hidden:
        note += f"  |  {hidden:,} tail values outside chart range"
    ax.text(0, 1.02, note, transform=ax.transAxes, fontsize=9, color=INK_2)

    # Whole dollars on ticks, unless the tick sits between dollars (e.g. $2.5)
    ax.xaxis.set_major_formatter(FuncFormatter(
        lambda v, _: short_money(v, 0 if float(v).is_integer() else 1)))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.set_xlabel("Intrinsic value per share", color=INK_2)
    ax.set_ylabel("Simulations", color=INK_2)
    ax.tick_params(colors=INK_2, labelsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(path, facecolor="white")
    plt.close(fig)


# ── MAIN ─────────────────────────────────────────────────────────────────────
def positive_int(text):
    value = int(text.replace(",", "").replace("_", ""))
    if value < 100:
        raise argparse.ArgumentTypeError("must be at least 100")
    return value


def main():
    # Never crash on a character the console can't display (e.g. cp1252 redirects)
    sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(description="Monte Carlo DCF valuation (builds on dcf.py).")
    parser.add_argument("ticker", help="stock ticker, e.g. ADBE")
    parser.add_argument("--sims", type=positive_int, default=DEFAULT_SIMS,
                        help=f"number of simulations (default {DEFAULT_SIMS:,})")
    parser.add_argument("--seed", type=int, default=None,
                        help="random seed for reproducible results")
    args = parser.parse_args()
    ticker = args.ticker.strip().upper()

    try:
        print(f"Fetching data for {ticker}...", flush=True)
        data, hist = load_company(ticker)
        a = derive_assumptions(data, hist)

        rng = np.random.default_rng(args.seed)
        inputs, n_redrawn = draw_inputs(a, args.sims, rng)

        values = _call(a, inputs)              # one intrinsic value per simulation
        base_value = float(_call(a, center_inputs(a)))
        sens = sensitivity(a, inputs, values)

        median, above = print_report(ticker, a, values, n_redrawn, base_value, sens)

        path = f"{ticker}_montecarlo.png"
        save_histogram(ticker, values, a["price"], median, above, path)
        print(f"\n  Histogram saved to {path}")
    except InputError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
