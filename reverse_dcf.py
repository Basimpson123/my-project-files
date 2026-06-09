from scipy.optimize import brentq
import numpy as np

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
TICKER             = "NVDA"
CURRENT_PRICE      = 135.00       # $ per share
SHARES_OUTSTANDING = 24_400e6    # shares
NET_DEBT           = -17_200e6   # $ (negative = net cash position)

BASE_REVENUE       = 130_497e6   # $ last-twelve-months revenue

# Operating assumptions — held constant over the forecast period
EBIT_MARGIN        = 0.62        # EBIT / revenue
TAX_RATE           = 0.15        # effective cash tax rate
DA_PCT_REV         = 0.02        # D&A as % of revenue
CAPEX_PCT_REV      = 0.02        # CapEx as % of revenue (cash outflow)
NWC_PCT_REV_CHANGE = 0.05        # dNWC as % of the change in revenue (cash use when growing)

# Discount / terminal value
WACC               = 0.10        # weighted-average cost of capital
TERMINAL_GROWTH    = 0.03        # Gordon-growth perpetuity rate
FORECAST_YEARS     = 5           # explicit forecast period

# Solver bounds: search for implied growth within this range
GROWTH_LOW         = -0.10       # -10%
GROWTH_HIGH        =  0.80       #  80%

# Sensitivity table: values to sweep
WACC_STEPS         = [-0.02, -0.01, 0.00, +0.01, +0.02]   # offsets from base WACC
TG_STEPS           = [-0.01, -0.005, 0.00, +0.005, +0.01]  # offsets from base terminal growth
# ─────────────────────────────────────────────────────────────────────────────


def intrinsic_price(g, wacc=WACC, tg=TERMINAL_GROWTH):
    """
    Project revenue at constant annual growth rate g for FORECAST_YEARS.
    Convert each year's revenue to unlevered FCF, discount at wacc.
    Add Gordon-growth terminal value.  Return implied share price.
    """
    if wacc <= tg:
        return None

    pv_fcfs  = 0.0
    revenue  = BASE_REVENUE

    for year in range(1, FORECAST_YEARS + 1):
        prev_revenue = revenue
        revenue      = prev_revenue * (1 + g)

        ebit    = revenue * EBIT_MARGIN
        nopat   = ebit * (1 - TAX_RATE)
        da      = revenue * DA_PCT_REV
        capex   = revenue * CAPEX_PCT_REV
        d_nwc   = (revenue - prev_revenue) * NWC_PCT_REV_CHANGE

        ufcf    = nopat + da - capex - d_nwc
        pv_fcfs += ufcf / ((1 + wacc) ** year)

    # Terminal value on the final year's FCF grown one more period
    terminal_fcf = revenue * EBIT_MARGIN * (1 - TAX_RATE) + revenue * DA_PCT_REV \
                   - revenue * CAPEX_PCT_REV  # dNWC ~ 0 at steady state
    terminal_fcf *= (1 + tg)
    pv_terminal   = terminal_fcf / (wacc - tg) / ((1 + wacc) ** FORECAST_YEARS)

    enterprise_value = pv_fcfs + pv_terminal
    equity_value     = enterprise_value - NET_DEBT
    return equity_value / SHARES_OUTSTANDING


def solve_implied_growth(target_price, wacc=WACC, tg=TERMINAL_GROWTH):
    """Return implied revenue growth rate g where intrinsic_price(g) == target_price.
    Returns None if no root exists within [GROWTH_LOW, GROWTH_HIGH]."""
    if wacc <= tg:
        return None

    def objective(g):
        ip = intrinsic_price(g, wacc=wacc, tg=tg)
        if ip is None:
            return float("nan")
        return ip - target_price

    try:
        lo = objective(GROWTH_LOW)
        hi = objective(GROWTH_HIGH)
        if lo * hi > 0:  # same sign — no root in interval
            return None
        return brentq(objective, GROWTH_LOW, GROWTH_HIGH, xtol=1e-7)
    except (ValueError, RuntimeError):
        return None


def print_sensitivity_table(base_implied_g):
    """Print a grid of implied growth rates across WACC × terminal-growth offsets."""
    wacc_vals = [WACC + dw for dw in WACC_STEPS]
    tg_vals   = [TERMINAL_GROWTH + dt for dt in TG_STEPS]

    col = 10
    header_label = "WACC \\ TG"

    print(f"\n  {'Sensitivity: Implied Revenue Growth (%)':}")
    print(f"  {'-' * (len(header_label) + col * len(tg_vals) + 4)}")
    print(f"  {header_label:<{len(header_label) + 2}}" +
          "".join(f"{tg * 100:>{col - 1}.1f}%" for tg in tg_vals))
    print(f"  {'-' * (len(header_label) + col * len(tg_vals) + 4)}")

    for wacc_val in wacc_vals:
        marker = " *" if abs(wacc_val - WACC) < 1e-9 else "  "
        row = f"{marker}{wacc_val * 100:.1f}%{'':<{len(header_label) - 6}}"
        for tg_val in tg_vals:
            g = solve_implied_growth(CURRENT_PRICE, wacc=wacc_val, tg=tg_val)
            if g is None:
                row += f"{'N/A':>{col}}"
            else:
                cell = f"{g * 100:.1f}%"
                # Mark the base-case cell
                if abs(wacc_val - WACC) < 1e-9 and abs(tg_val - TERMINAL_GROWTH) < 1e-9:
                    cell = f"[{g * 100:.1f}%]"
                row += f"{cell:>{col}}"
        print(f"  {row}")

    print(f"  {'-' * (len(header_label) + col * len(tg_vals) + 4)}")
    print(f"  * = base WACC row   [ ] = base-case cell")


def main():
    market_cap      = CURRENT_PRICE * SHARES_OUTSTANDING
    enterprise_value = market_cap + NET_DEBT

    print(f"\n{'=' * 60}")
    print(f"  Reverse DCF: {TICKER}")
    print(f"{'=' * 60}")
    print(f"  Current Price        : ${CURRENT_PRICE:>10.2f}")
    print(f"  Shares Outstanding   : {SHARES_OUTSTANDING / 1e9:>10.2f}B")
    print(f"  Market Cap           : ${market_cap / 1e9:>10.2f}B")
    print(f"  Net Debt (Cash)      : ${NET_DEBT / 1e9:>10.2f}B")
    print(f"  Implied EV           : ${enterprise_value / 1e9:>10.2f}B")
    print(f"\n  Base Revenue         : ${BASE_REVENUE / 1e9:>10.2f}B")
    print(f"  EBIT Margin          : {EBIT_MARGIN * 100:>10.1f}%")
    print(f"  Tax Rate             : {TAX_RATE * 100:>10.1f}%")
    print(f"  D&A % Rev            : {DA_PCT_REV * 100:>10.1f}%")
    print(f"  CapEx % Rev          : {CAPEX_PCT_REV * 100:>10.1f}%")
    print(f"  dNWC % dRev          : {NWC_PCT_REV_CHANGE * 100:>10.1f}%")
    print(f"\n  WACC                 : {WACC * 100:>10.1f}%")
    print(f"  Terminal Growth      : {TERMINAL_GROWTH * 100:>10.1f}%")
    print(f"  Forecast Period      : {FORECAST_YEARS:>10d} years")
    print(f"{'=' * 60}")

    implied_g = solve_implied_growth(CURRENT_PRICE)

    if implied_g is None:
        print(f"\n  [!] No implied growth rate found in range "
              f"[{GROWTH_LOW * 100:.0f}%, {GROWTH_HIGH * 100:.0f}%].")
        print(f"      The current price may be inconsistent with these model assumptions.")
    else:
        print(f"\n  Implied Revenue Growth Rate: {implied_g * 100:.2f}% per year")
        print(f"\n  Interpretation: The market is pricing in {implied_g * 100:.1f}% annual")
        print(f"  revenue growth for {FORECAST_YEARS} years to justify a ${CURRENT_PRICE:.2f} share price,")
        print(f"  given a {WACC * 100:.1f}% WACC and {TERMINAL_GROWTH * 100:.1f}% terminal growth rate.")

        # Quick sanity check: verify the solver round-trips correctly
        check = intrinsic_price(implied_g)
        if check is not None and abs(check - CURRENT_PRICE) > 0.01:
            print(f"\n  [WARN] Solver round-trip mismatch: model price ${check:.2f} vs ${CURRENT_PRICE:.2f}")

    print_sensitivity_table(implied_g)
    print()


if __name__ == "__main__":
    main()
