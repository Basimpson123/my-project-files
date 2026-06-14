import yfinance as yf
import numpy as np
import pandas as pd

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
TICKERS = ["QCOM", "AMD", "GOOGL", "BWXT", "CEG", "LHX", "AVGO", "VEEV", "DECK", "ADBE", "MELI", "MSFT"]

TERMINAL_GROWTH       = 0.025
PROJECTION_YEARS      = 5
FALLBACK_TAX_RATE     = 0.25
FALLBACK_WACC         = 0.10

# WACC from fundamentals (#1)
RISK_FREE_RATE        = 0.041   # Fernandez 2026 survey (97 countries)
EQUITY_RISK_PREMIUM   = 0.053   # Fernandez 2026 survey (97 countries)
FALLBACK_BETA         = 1.0
FALLBACK_COST_OF_DEBT = 0.05

# Growth rate (#6)
EWMA_DECAY            = 0.85    # exponential weight per year going backward

# Normalized base FCFF (#2)
NORMALIZE_YEARS       = 3       # years to average as the starting FCFF

# Bear: half the growth, WACC 2% higher
# Base: growth as derived, WACC unchanged
# Bull: 1.5x the growth, WACC 2% lower
SCENARIOS = {
    "Bear": {"growth_mult": 0.5,  "wacc_adj": +0.02},
    "Base": {"growth_mult": 1.0,  "wacc_adj":  0.00},
    "Bull": {"growth_mult": 1.5,  "wacc_adj": -0.02},
}

DEBUG = False   # when True, log the label get_row() selected for each field

# How to treat stock-based compensation in FCFF:
#   "expense"  -> SBC is a real cost; NOT added back. Conservative; matches the textbook
#                 EBIT-based buildup the tool already does. RECOMMENDED for strict valuation.
#   "addback"  -> add SBC back to FCFF, matching street/OCF-based reported FCF.
#   "both"     -> compute and print both, so the spread is visible per ticker.
SBC_TREATMENT = "both"
# ─────────────────────────────────────────────────────────────────────────────


def get_row(df, candidates, field_name=""):
    """Return the candidate row with the most non-null values."""
    best, best_count, chosen_label = None, -1, None
    for key in candidates:
        if key in df.index:
            count = df.loc[key].notna().sum()
            if count > best_count:
                best, best_count, chosen_label = df.loc[key], count, key
    if DEBUG and chosen_label:
        print(f"  [get_row] {field_name}: selected '{chosen_label}' ({best_count} non-null)")
    return best


def fetch_data(symbol):
    ticker = yf.Ticker(symbol)

    cf  = ticker.cashflow
    inc = ticker.income_stmt if hasattr(ticker, "income_stmt") else ticker.financials
    bs  = ticker.balance_sheet

    ebit_row    = get_row(inc, ["EBIT", "Ebit", "Operating Income"], "EBIT")
    tax_row     = get_row(inc, ["Tax Provision", "Income Tax Expense"], "Tax")
    pretax_row  = get_row(inc, ["Pretax Income", "Income Before Tax"], "Pretax Income")
    int_exp_row = get_row(inc, ["Interest Expense", "Interest Expense Non Operating"], "Interest Expense")

    da_row    = get_row(cf, ["Depreciation And Amortization",
                              "Depreciation Amortization Depletion",
                              "Reconciled Depreciation",
                              "Depreciation"], "D&A")
    capex_row = get_row(cf, ["Capital Expenditure", "Capital Expenditures"], "CapEx")
    wc_row    = get_row(cf, ["Change In Working Capital"], "WC Change")
    sbc_row   = get_row(cf, ["Stock Based Compensation",
                              "Share Based Compensation",
                              "StockBasedCompensation"], "SBC")
    ocf_row   = get_row(cf, ["Operating Cash Flow",
                              "Cash Flowsfromusedin Operating Activities Direct"], "OCF")

    # Diluted shares from income statement (#5)
    diluted_row = get_row(inc, ["Diluted Average Shares", "Diluted Shares"], "Diluted Shares")

    if any(r is None for r in [ebit_row, da_row]):
        return None
    # CapEx is optional — asset-light companies may not report it separately; default to 0

    # Index by year string to avoid timestamp metadata mismatches across statements
    def to_year_dict(row):
        return {str(d)[:4]: v for d, v in row.dropna().items()}

    ebit_y    = to_year_dict(ebit_row)
    da_y      = to_year_dict(da_row)
    capex_y   = to_year_dict(capex_row) if capex_row is not None else {}
    wc_y      = to_year_dict(wc_row) if wc_row is not None else {}
    pretax_y  = to_year_dict(pretax_row) if pretax_row is not None else {}
    tax_y     = to_year_dict(tax_row) if tax_row is not None else {}
    sbc_y     = to_year_dict(sbc_row) if sbc_row is not None else {}
    ocf_y     = to_year_dict(ocf_row) if ocf_row is not None else {}

    common_years = sorted(set(ebit_y) & set(da_y), reverse=True)

    fcff_expense_by_date = {}
    fcff_addback_by_date = {}
    tax_rates            = []

    for year in common_years:
        ebit  = ebit_y[year]
        da    = da_y[year]
        capex = capex_y.get(year, 0.0)
        wc    = wc_y.get(year, 0.0)
        sbc   = sbc_y.get(year, 0.0)
        ocf   = ocf_y.get(year, 0.0)

        if pd.isna(ebit) or pd.isna(da):
            continue
        if pd.isna(wc):
            wc = 0.0
        if pd.isna(sbc):
            sbc = 0.0

        try:
            pretax = pretax_y.get(year)
            tax    = tax_y.get(year)
            if pretax is None or tax is None or pd.isna(pretax) or pretax <= 0 or pd.isna(tax) or tax < 0:
                tax_rate = FALLBACK_TAX_RATE
            else:
                tax_rate = max(0.0, min(float(tax) / float(pretax), 0.40))
        except Exception:
            tax_rate = FALLBACK_TAX_RATE

        tax_rates.append(tax_rate)
        nopat        = ebit * (1 - tax_rate)
        fcff_expense = nopat + da + wc + capex
        fcff_addback = fcff_expense + sbc

        # Reconciliation: warn when tool's FCFF diverges >25% from street OCF−CapEx.
        # A consistent gap signals an SBC or deferred-revenue definitional mismatch.
        if ocf != 0.0:
            ocf_minus_capex = ocf + capex  # capex is negative per yfinance convention
            if ocf_minus_capex != 0 and abs(fcff_expense - ocf_minus_capex) > 0.25 * abs(ocf_minus_capex):
                print(f"  [WARN] {symbol} {year}: FCFF ({fcff_expense/1e9:.2f}B) diverges "
                      f">25% from OCF−CapEx ({ocf_minus_capex/1e9:.2f}B) — "
                      f"likely SBC/deferred-revenue definitional gap.")

        fcff_expense_by_date[year] = fcff_expense
        fcff_addback_by_date[year] = fcff_addback

    if not fcff_expense_by_date:
        return None

    sorted_years        = sorted(fcff_expense_by_date.keys(), reverse=True)
    fcff_expense_values = np.array([fcff_expense_by_date[y] for y in sorted_years])
    fcff_addback_values = np.array([fcff_addback_by_date[y] for y in sorted_years])

    cash_row = get_row(bs, ["Cash And Cash Equivalents",
                             "Cash Cash Equivalents And Short Term Investments",
                             "Cash"], "Cash")
    debt_row = get_row(bs, ["Total Debt",
                             "Long Term Debt And Capital Lease Obligation",
                             "Long Term Debt"], "Debt")

    cash = float(cash_row.iloc[0]) if cash_row is not None else 0.0
    debt = float(debt_row.iloc[0]) if debt_row is not None else 0.0

    info       = ticker.info
    shares     = info.get("sharesOutstanding")
    price      = info.get("currentPrice") or info.get("regularMarketPrice")
    beta       = info.get("beta") or FALLBACK_BETA
    market_cap = info.get("marketCap") or 0.0

    # Prefer diluted shares from income statement (#5)
    if diluted_row is not None:
        try:
            diluted = float(diluted_row.iloc[0])
            if diluted > 0:
                shares = diluted
        except Exception:
            pass

    # Interest expense for cost-of-debt calculation (#1)
    interest_expense = None
    if int_exp_row is not None:
        try:
            interest_expense = abs(float(int_exp_row.iloc[0]))
        except Exception:
            pass

    avg_tax_rate = float(np.mean(tax_rates)) if tax_rates else FALLBACK_TAX_RATE

    return {
        "fcff_expense_values": fcff_expense_values,
        "fcff_addback_values": fcff_addback_values,
        "dates":               sorted_years,
        "cash":                cash,
        "debt":                debt,
        "shares":              shares,
        "price":               price,
        "beta":                beta,
        "market_cap":          market_cap,
        "interest_expense":    interest_expense,
        "avg_tax_rate":        avg_tax_rate,
    }


def compute_wacc(data):
    """Derive WACC from CAPM cost of equity + after-tax cost of debt (#1)."""
    beta       = data["beta"] or FALLBACK_BETA
    market_cap = data["market_cap"]
    debt       = data["debt"]
    tax_rate   = data["avg_tax_rate"]

    cost_of_equity = RISK_FREE_RATE + beta * EQUITY_RISK_PREMIUM

    if debt > 0 and data["interest_expense"]:
        cost_of_debt = min(data["interest_expense"] / debt, 0.15)
    else:
        cost_of_debt = FALLBACK_COST_OF_DEBT
    after_tax_cod = cost_of_debt * (1 - tax_rate)

    total_capital = market_cap + debt
    if total_capital <= 0:
        return FALLBACK_WACC

    w_equity = market_cap / total_capital
    w_debt   = debt / total_capital

    wacc = w_equity * cost_of_equity + w_debt * after_tax_cod
    return max(min(wacc, 0.20), 0.05)


def run_dcf(base_fcff, cash, debt, shares, growth_rate, wacc):
    """Two-stage DCF: growth fades linearly to terminal rate over projection period (#4)."""
    if wacc <= TERMINAL_GROWTH:
        return None

    pv_fcffs       = []
    projected_fcff = base_fcff

    for year in range(1, PROJECTION_YEARS + 1):
        # Linearly blend growth_rate → TERMINAL_GROWTH (year 1 = full, year N = terminal)
        t              = (year - 1) / max(PROJECTION_YEARS - 1, 1)
        year_growth    = growth_rate * (1 - t) + TERMINAL_GROWTH * t
        projected_fcff *= (1 + year_growth)
        pv_fcffs.append(projected_fcff / ((1 + wacc) ** year))

    pv_terminal = ((projected_fcff * (1 + TERMINAL_GROWTH))
                   / (wacc - TERMINAL_GROWTH)
                   / ((1 + wacc) ** PROJECTION_YEARS))

    enterprise_value = sum(pv_fcffs) + pv_terminal
    equity_value     = enterprise_value + cash - debt

    if not shares or shares == 0:
        return None
    return equity_value / shares


def calculate_dcf(data, symbol, fcff_values=None, label_suffix=""):
    if fcff_values is None:
        fcff_values = data["fcff_expense_values"]

    if len(fcff_values) < 2:
        print(f"{symbol}: Not enough FCFF history.")
        return

    fcff_chron = fcff_values[::-1]  # oldest first

    # CAGR (geometric mean) from oldest to most recent positive endpoint (#3)
    cagr = None
    if fcff_chron[0] > 0 and fcff_chron[-1] > 0:
        n    = len(fcff_chron) - 1
        cagr = (fcff_chron[-1] / fcff_chron[0]) ** (1 / n) - 1

    # EWMA-weighted YoY rates — recent years carry more weight (#6)
    # Use (curr - prev) / abs(prev) so negative-base years get the right sign
    yoy_pairs = []
    for i in range(1, len(fcff_chron)):
        prev, curr = fcff_chron[i - 1], fcff_chron[i]
        if prev != 0:
            yoy_pairs.append((i, (curr - prev) / abs(prev)))

    ewma_growth = None
    if yoy_pairs:
        indices = np.array([p[0] for p in yoy_pairs])
        rates   = np.array([p[1] for p in yoy_pairs])
        weights = EWMA_DECAY ** (len(yoy_pairs) - indices)  # highest index = most recent = weight 1.0
        ewma_growth = float(np.average(rates, weights=weights))

    # Blend CAGR and EWMA when both are available
    if cagr is not None and ewma_growth is not None:
        base_growth = 0.5 * cagr + 0.5 * ewma_growth
    elif cagr is not None:
        base_growth = cagr
    elif ewma_growth is not None:
        base_growth = ewma_growth
    else:
        print(f"{symbol}: Could not compute a valid growth rate.")
        return

    base_growth = max(min(base_growth, 0.30), -0.05)

    # Normalized base FCFF: use most recent year if FCFF grew consistently,
    # otherwise average to smooth out one-time anomalies (#2)
    n_norm  = min(NORMALIZE_YEARS, len(fcff_values))
    recent  = fcff_values[:n_norm]  # most recent first
    consistent_growth = all(recent[i] < recent[i - 1] for i in range(1, len(recent)))
    if consistent_growth:
        base_fcff   = float(fcff_values[0])
        base_label  = "most recent year"
    else:
        base_fcff   = float(np.mean(recent))
        base_label  = f"{n_norm}-yr avg"

    # WACC from fundamentals (#1)
    base_wacc     = compute_wacc(data)
    current_price = data["price"]

    suffix_str = f" [{label_suffix}]" if label_suffix else ""
    print(f"\n{'=' * 60}")
    print(f"  DCF Analysis (FCFF): {symbol}{suffix_str}")
    print(f"{'=' * 60}")
    print(f"  Historical FCFF (most recent first):")
    for date, v in zip(data["dates"], fcff_values):
        print(f"    {date}  ${v / 1e9:>8.2f}B")
    print(f"\n  Base FCFF            : ${base_fcff / 1e9:.2f}B  ({base_label})")
    print(f"  Base Growth Rate     : {base_growth * 100:.1f}%")
    print(f"  Terminal Growth Rate : {TERMINAL_GROWTH * 100:.1f}%")
    print(f"  Projection Period    : {PROJECTION_YEARS} years  (two-stage fade)")
    print(f"  Beta                 : {data['beta']:.2f}")
    print(f"  Computed WACC        : {base_wacc * 100:.1f}%")
    if current_price:
        print(f"  Current Price        : ${current_price:.2f}")

    names   = list(SCENARIOS.keys())
    growths = []
    waccs   = []
    values  = []

    for name, params in SCENARIOS.items():
        g    = base_growth * params["growth_mult"]
        g    = max(min(g, 0.50), -0.10)
        wacc = max(min(base_wacc + params["wacc_adj"], 0.20), 0.05)
        iv   = run_dcf(base_fcff, data["cash"], data["debt"], data["shares"], g, wacc)
        growths.append(g)
        waccs.append(wacc)
        values.append(iv)

    col = 12
    print(f"\n  {'':22}" + "".join(f"{n:>{col}}" for n in names))
    print(f"  {'-' * (22 + col * len(names))}")
    print(f"  {'Growth Rate':<22}" + "".join(f"{g*100:>{col-1}.1f}%" for g in growths))
    print(f"  {'WACC':<22}"        + "".join(f"{w*100:>{col-1}.1f}%" for w in waccs))
    print(f"  {'Intrinsic Value':<22}" + "".join(
        f"${iv:>{col-1}.2f}" if iv is not None else f"{'N/A':>{col}}" for iv in values
    ))
    upsides = []
    if current_price:
        print(f"  {'Upside / (Downside)':<22}" + "".join(
            f"{((iv / current_price) - 1) * 100:>+{col-1}.1f}%" if iv is not None else f"{'N/A':>{col}}"
            for iv in values
        ))
        upsides = [((iv / current_price) - 1) * 100 if iv is not None else None for iv in values]
    print(f"  {'=' * (22 + col * len(names))}")
    return {"symbol": symbol, "upsides": upsides}


def main():
    summary = []
    for symbol in TICKERS:
        print(f"\nFetching data for {symbol}...")
        data = fetch_data(symbol)
        if data is None:
            print(f"{symbol}: Could not retrieve financial data.")
            continue
        if SBC_TREATMENT == "both":
            r1 = calculate_dcf(data, symbol, data["fcff_expense_values"], "SBC as Expense")
            r2 = calculate_dcf(data, symbol, data["fcff_addback_values"], "SBC as Add-back")
            if r1: summary.append({**r1, "symbol": symbol + " (E)"})
            if r2: summary.append({**r2, "symbol": symbol + " (A)"})
        elif SBC_TREATMENT == "addback":
            result = calculate_dcf(data, symbol, data["fcff_addback_values"])
            if result: summary.append(result)
        else:  # "expense" (default conservative)
            result = calculate_dcf(data, symbol, data["fcff_expense_values"])
            if result: summary.append(result)

    if summary:
        names = list(SCENARIOS.keys())
        col   = 12
        print(f"\n\n{'=' * 60}")
        print(f"  Summary: Upside / (Downside) by Scenario")
        print(f"{'=' * 60}")
        print(f"  {'Ticker':<10}" + "".join(f"{n:>{col}}" for n in names))
        print(f"  {'-' * (10 + col * len(names))}")
        for row in summary:
            ups = row["upsides"]
            line = f"  {row['symbol']:<10}"
            for u in ups:
                line += f"{u:>+{col-1}.1f}%" if u is not None else f"{'N/A':>{col}}"
            print(line)
        print(f"  {'=' * (10 + col * len(names))}")


if __name__ == "__main__":
    main()
