from scipy.optimize import brentq
import numpy as np
import yfinance as yf

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
TICKERS = ["AVGO", "QCOM", "GOOGL"]

# Per-ticker overrides — any key set here takes precedence over auto-fetch.
# Example: OVERRIDES = {"AVGO": {"EBIT_MARGIN": 0.55}, "QCOM": {"WACC": 0.09}}
OVERRIDES = {}

# Auto-fetchable fields — None = always auto-fetch; set a value to use as the
# default for every ticker (still overridable per-ticker via OVERRIDES).
CURRENT_PRICE      = None
SHARES_OUTSTANDING = None
NET_DEBT           = None
BASE_REVENUE       = None
EBIT_MARGIN        = None
TAX_RATE           = None
DA_PCT_REV         = None
CAPEX_PCT_REV      = None

# Judgment-call assumptions — shared across all tickers unless overridden
NWC_PCT_REV_CHANGE = 0.05
WACC               = 0.10
TERMINAL_GROWTH    = 0.03
FORECAST_YEARS     = 5

GROWTH_LOW  = -0.10
GROWTH_HIGH =  0.80

WACC_STEPS = [-0.02, -0.01, 0.00, +0.01, +0.02]
TG_STEPS   = [-0.01, -0.005, 0.00, +0.005, +0.01]
# ─────────────────────────────────────────────────────────────────────────────

_AUTO_KEYS = [
    'CURRENT_PRICE', 'SHARES_OUTSTANDING', 'NET_DEBT', 'BASE_REVENUE',
    'EBIT_MARGIN', 'TAX_RATE', 'DA_PCT_REV', 'CAPEX_PCT_REV',
]

_ALL_KEYS = _AUTO_KEYS + [
    'NWC_PCT_REV_CHANGE', 'WACC', 'TERMINAL_GROWTH', 'FORECAST_YEARS',
]


def _ltm_sum(df):
    cols = min(4, df.shape[1])
    return df.iloc[:, :cols].sum(axis=1)


def _get(series, *names):
    for name in names:
        if name in series.index:
            val = series[name]
            try:
                f = float(val)
                if not np.isnan(f):
                    return f
            except (TypeError, ValueError):
                pass
    return None


def fetch_financials(ticker):
    """Pull key financials from yfinance. Returns a dict of resolved values."""
    tk = yf.Ticker(ticker)
    info = tk.info
    result = {}

    price = info.get('currentPrice') or info.get('regularMarketPrice')
    if price:
        result['CURRENT_PRICE'] = float(price)

    shares = info.get('sharesOutstanding')
    if shares:
        result['SHARES_OUTSTANDING'] = float(shares)

    bs = tk.quarterly_balance_sheet
    if bs is not None and not bs.empty:
        col = bs.iloc[:, 0]
        debt = _get(col, 'Total Debt', 'Long Term Debt And Capital Lease Obligation', 'Long Term Debt')
        cash = _get(col, 'Cash And Cash Equivalents',
                    'Cash Cash Equivalents And Short Term Investments',
                    'Cash And Short Term Investments')
        if debt is not None and cash is not None:
            result['NET_DEBT'] = debt - cash
        elif debt is not None:
            result['NET_DEBT'] = debt
        elif cash is not None:
            result['NET_DEBT'] = -cash

    qis = tk.quarterly_income_stmt
    if qis is not None and not qis.empty:
        ltm = _ltm_sum(qis)
        revenue = _get(ltm, 'Total Revenue')
        ebit    = _get(ltm, 'Operating Income', 'EBIT')
        pretax  = _get(ltm, 'Pretax Income')
        tax     = _get(ltm, 'Tax Provision')

        if revenue:
            result['BASE_REVENUE'] = revenue
        if revenue and ebit and revenue != 0:
            result['EBIT_MARGIN'] = ebit / revenue
        if pretax and tax and pretax > 0:
            result['TAX_RATE'] = max(0.0, min(tax / pretax, 0.50))

    qcf = tk.quarterly_cashflow
    revenue = result.get('BASE_REVENUE')
    if qcf is not None and not qcf.empty and revenue:
        ltm_cf = _ltm_sum(qcf)
        da    = _get(ltm_cf, 'Depreciation And Amortization',
                     'Depreciation Amortization Depletion', 'Reconciled Depreciation')
        capex = _get(ltm_cf, 'Capital Expenditure',
                     'Purchase Of Property Plant And Equipment')
        if da:
            result['DA_PCT_REV'] = da / revenue
        if capex:
            result['CAPEX_PCT_REV'] = abs(capex) / revenue

    return result


def build_config(ticker):
    """Resolve all inputs for one ticker. Returns (cfg dict, set of auto-fetched keys)."""
    # Start from module-level defaults
    cfg = {k: globals()[k] for k in _ALL_KEYS}
    cfg['TICKER'] = ticker
    cfg['GROWTH_LOW']  = GROWTH_LOW
    cfg['GROWTH_HIGH'] = GROWTH_HIGH
    cfg['WACC_STEPS']  = WACC_STEPS
    cfg['TG_STEPS']    = TG_STEPS

    # Apply per-ticker overrides
    for k, v in OVERRIDES.get(ticker, {}).items():
        cfg[k.upper()] = v

    # Determine what still needs fetching
    needs_fetch = [k for k in _AUTO_KEYS if cfg[k] is None]
    fetched_keys = set()

    if needs_fetch:
        print(f"\n  Fetching data for {ticker} ...", end="", flush=True)
        try:
            fetched = fetch_financials(ticker)
        except Exception as e:
            print(f"\n  [ERROR] yfinance fetch failed: {e}")
            raise
        print(" done.")

        for key in needs_fetch:
            val = fetched.get(key)
            if val is None:
                raise ValueError(
                    f"Could not auto-fetch '{key}' for {ticker}. "
                    "Set it manually in OVERRIDES or as a module-level default."
                )
            cfg[key] = val
            fetched_keys.add(key)

    return cfg, fetched_keys


def intrinsic_price(g, cfg, wacc=None, tg=None):
    if wacc is None:
        wacc = cfg['WACC']
    if tg is None:
        tg = cfg['TERMINAL_GROWTH']
    if wacc <= tg:
        return None

    pv_fcfs = 0.0
    revenue = cfg['BASE_REVENUE']

    for year in range(1, cfg['FORECAST_YEARS'] + 1):
        prev_revenue = revenue
        revenue      = prev_revenue * (1 + g)

        ebit   = revenue * cfg['EBIT_MARGIN']
        nopat  = ebit * (1 - cfg['TAX_RATE'])
        da     = revenue * cfg['DA_PCT_REV']
        capex  = revenue * cfg['CAPEX_PCT_REV']
        d_nwc  = (revenue - prev_revenue) * cfg['NWC_PCT_REV_CHANGE']

        ufcf   = nopat + da - capex - d_nwc
        pv_fcfs += ufcf / ((1 + wacc) ** year)

    terminal_fcf = (revenue * cfg['EBIT_MARGIN'] * (1 - cfg['TAX_RATE'])
                    + revenue * cfg['DA_PCT_REV'] - revenue * cfg['CAPEX_PCT_REV'])
    terminal_fcf *= (1 + tg)
    pv_terminal   = terminal_fcf / (wacc - tg) / ((1 + wacc) ** cfg['FORECAST_YEARS'])

    enterprise_value = pv_fcfs + pv_terminal
    equity_value     = enterprise_value - cfg['NET_DEBT']
    return equity_value / cfg['SHARES_OUTSTANDING']


def solve_implied_growth(target_price, cfg, wacc=None, tg=None):
    if wacc is None:
        wacc = cfg['WACC']
    if tg is None:
        tg = cfg['TERMINAL_GROWTH']
    if wacc <= tg:
        return None

    def objective(g):
        ip = intrinsic_price(g, cfg, wacc=wacc, tg=tg)
        return float("nan") if ip is None else ip - target_price

    try:
        lo = objective(cfg['GROWTH_LOW'])
        hi = objective(cfg['GROWTH_HIGH'])
        if lo * hi > 0:
            return None
        return brentq(objective, cfg['GROWTH_LOW'], cfg['GROWTH_HIGH'], xtol=1e-7)
    except (ValueError, RuntimeError):
        return None


def print_sensitivity_table(cfg, auto_fetched):
    wacc_vals = [cfg['WACC'] + dw for dw in cfg['WACC_STEPS']]
    tg_vals   = [cfg['TERMINAL_GROWTH'] + dt for dt in cfg['TG_STEPS']]

    col = 10
    header_label = "WACC \\ TG"

    print(f"\n  {'Sensitivity: Implied Revenue Growth (%)':}")
    print(f"  {'-' * (len(header_label) + col * len(tg_vals) + 4)}")
    print(f"  {header_label:<{len(header_label) + 2}}" +
          "".join(f"{tg * 100:>{col - 1}.1f}%" for tg in tg_vals))
    print(f"  {'-' * (len(header_label) + col * len(tg_vals) + 4)}")

    for wacc_val in wacc_vals:
        marker = " *" if abs(wacc_val - cfg['WACC']) < 1e-9 else "  "
        row = f"{marker}{wacc_val * 100:.1f}%{'':<{len(header_label) - 6}}"
        for tg_val in tg_vals:
            g = solve_implied_growth(cfg['CURRENT_PRICE'], cfg, wacc=wacc_val, tg=tg_val)
            if g is None:
                row += f"{'N/A':>{col}}"
            else:
                cell = f"{g * 100:.1f}%"
                if abs(wacc_val - cfg['WACC']) < 1e-9 and abs(tg_val - cfg['TERMINAL_GROWTH']) < 1e-9:
                    cell = f"[{g * 100:.1f}%]"
                row += f"{cell:>{col}}"
        print(f"  {row}")

    print(f"  {'-' * (len(header_label) + col * len(tg_vals) + 4)}")
    print(f"  * = base WACC row   [ ] = base-case cell")


def run_ticker(ticker):
    cfg, auto_fetched = build_config(ticker)

    def tag(key):
        return " (auto)" if key in auto_fetched else ""

    market_cap       = cfg['CURRENT_PRICE'] * cfg['SHARES_OUTSTANDING']
    enterprise_value = market_cap + cfg['NET_DEBT']

    print(f"\n{'=' * 60}")
    print(f"  Reverse DCF: {ticker}")
    print(f"{'=' * 60}")
    print(f"  Current Price        : ${cfg['CURRENT_PRICE']:>10.2f}{tag('CURRENT_PRICE')}")
    print(f"  Shares Outstanding   : {cfg['SHARES_OUTSTANDING'] / 1e9:>10.2f}B{tag('SHARES_OUTSTANDING')}")
    print(f"  Market Cap           : ${market_cap / 1e9:>10.2f}B")
    print(f"  Net Debt (Cash)      : ${cfg['NET_DEBT'] / 1e9:>10.2f}B{tag('NET_DEBT')}")
    print(f"  Implied EV           : ${enterprise_value / 1e9:>10.2f}B")
    print(f"\n  Base Revenue         : ${cfg['BASE_REVENUE'] / 1e9:>10.2f}B{tag('BASE_REVENUE')}")
    print(f"  EBIT Margin          : {cfg['EBIT_MARGIN'] * 100:>10.1f}%{tag('EBIT_MARGIN')}")
    print(f"  Tax Rate             : {cfg['TAX_RATE'] * 100:>10.1f}%{tag('TAX_RATE')}")
    print(f"  D&A % Rev            : {cfg['DA_PCT_REV'] * 100:>10.1f}%{tag('DA_PCT_REV')}")
    print(f"  CapEx % Rev          : {cfg['CAPEX_PCT_REV'] * 100:>10.1f}%{tag('CAPEX_PCT_REV')}")
    print(f"  dNWC % dRev          : {cfg['NWC_PCT_REV_CHANGE'] * 100:>10.1f}%")
    print(f"\n  WACC                 : {cfg['WACC'] * 100:>10.1f}%")
    print(f"  Terminal Growth      : {cfg['TERMINAL_GROWTH'] * 100:>10.1f}%")
    print(f"  Forecast Period      : {cfg['FORECAST_YEARS']:>10d} years")
    print(f"{'=' * 60}")

    implied_g = solve_implied_growth(cfg['CURRENT_PRICE'], cfg)

    if implied_g is None:
        print(f"\n  [!] No implied growth rate found in range "
              f"[{cfg['GROWTH_LOW'] * 100:.0f}%, {cfg['GROWTH_HIGH'] * 100:.0f}%].")
        print(f"      The current price may be inconsistent with these model assumptions.")
    else:
        print(f"\n  Implied Revenue Growth Rate: {implied_g * 100:.2f}% per year")
        print(f"\n  Interpretation: The market is pricing in {implied_g * 100:.1f}% annual")
        print(f"  revenue growth for {cfg['FORECAST_YEARS']} years to justify a ${cfg['CURRENT_PRICE']:.2f} share price,")
        print(f"  given a {cfg['WACC'] * 100:.1f}% WACC and {cfg['TERMINAL_GROWTH'] * 100:.1f}% terminal growth rate.")

        check = intrinsic_price(implied_g, cfg)
        if check is not None and abs(check - cfg['CURRENT_PRICE']) > 0.01:
            print(f"\n  [WARN] Solver round-trip mismatch: model price ${check:.2f} vs ${cfg['CURRENT_PRICE']:.2f}")

    print_sensitivity_table(cfg, auto_fetched)
    print()


def main():
    for ticker in TICKERS:
        run_ticker(ticker)


if __name__ == "__main__":
    main()
