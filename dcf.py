import yfinance as yf
import numpy as np
import pandas as pd

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
TICKERS = ["QCOM", "AMD", "GOOGL", "BWXT", "CEG", "LULU", "VEEV", "DECK", "ADBE", "NVO", "MELI"]

WACC              = 0.10
TERMINAL_GROWTH   = 0.025
PROJECTION_YEARS  = 5
FALLBACK_TAX_RATE = 0.25

# Bear: half the growth, WACC 2% higher
# Base: growth as derived, WACC unchanged
# Bull: 1.5x the growth, WACC 2% lower
SCENARIOS = {
    "Bear": {"growth_mult": 0.5,  "wacc_adj": +0.02},
    "Base": {"growth_mult": 1.0,  "wacc_adj":  0.00},
    "Bull": {"growth_mult": 1.5,  "wacc_adj": -0.02},
}
# ─────────────────────────────────────────────────────────────────────────────


def get_row(df, candidates):
    """Return the first matching row from a DataFrame given a list of possible names."""
    for key in candidates:
        if key in df.index:
            return df.loc[key]
    return None


def fetch_data(symbol):
    ticker = yf.Ticker(symbol)

    cf  = ticker.cashflow
    inc = ticker.income_stmt if hasattr(ticker, "income_stmt") else ticker.financials
    bs  = ticker.balance_sheet

    ebit_row   = get_row(inc, ["EBIT", "Ebit", "Operating Income"])
    tax_row    = get_row(inc, ["Tax Provision", "Income Tax Expense"])
    pretax_row = get_row(inc, ["Pretax Income", "Income Before Tax"])

    da_row    = get_row(cf, ["Depreciation And Amortization",
                              "Depreciation Amortization Depletion",
                              "Depreciation"])
    capex_row = get_row(cf, ["Capital Expenditure", "Capital Expenditures"])
    wc_row    = get_row(cf, ["Change In Working Capital"])

    if any(r is None for r in [ebit_row, da_row, capex_row]):
        return None

    common_dates = (ebit_row.dropna().index
                    .intersection(da_row.dropna().index)
                    .intersection(capex_row.dropna().index))

    fcff_by_date = {}
    for date in common_dates:
        ebit  = ebit_row[date]
        da    = da_row[date]
        capex = capex_row[date]
        wc    = (wc_row[date] if wc_row is not None and date in wc_row.index else 0.0)

        if pd.isna(ebit) or pd.isna(da) or pd.isna(capex):
            continue
        if pd.isna(wc):
            wc = 0.0

        try:
            pretax = pretax_row[date]
            tax    = tax_row[date]
            if pd.isna(pretax) or pretax <= 0 or pd.isna(tax) or tax < 0:
                tax_rate = FALLBACK_TAX_RATE
            else:
                tax_rate = max(0.0, min(float(tax) / float(pretax), 0.40))
        except Exception:
            tax_rate = FALLBACK_TAX_RATE

        nopat = ebit * (1 - tax_rate)
        fcff_by_date[date] = nopat + da + wc + capex

    if not fcff_by_date:
        return None

    sorted_dates = sorted(fcff_by_date.keys(), reverse=True)
    fcff_values  = np.array([fcff_by_date[d] for d in sorted_dates])

    cash_row = get_row(bs, ["Cash And Cash Equivalents",
                             "Cash Cash Equivalents And Short Term Investments",
                             "Cash"])
    debt_row = get_row(bs, ["Total Debt",
                             "Long Term Debt And Capital Lease Obligation",
                             "Long Term Debt"])

    cash = float(cash_row.iloc[0]) if cash_row is not None else 0.0
    debt = float(debt_row.iloc[0]) if debt_row is not None else 0.0

    info   = ticker.info
    shares = info.get("sharesOutstanding")
    price  = info.get("currentPrice") or info.get("regularMarketPrice")

    return {
        "fcff_values": fcff_values,
        "dates":       sorted_dates,
        "cash":        cash,
        "debt":        debt,
        "shares":      shares,
        "price":       price,
    }


def run_dcf(fcff_values, cash, debt, shares, growth_rate, wacc):
    """Run DCF for one scenario. Returns intrinsic value per share, or None."""
    if wacc <= TERMINAL_GROWTH:
        return None

    base_fcff = fcff_values[0]
    pv_fcffs  = []
    projected_fcff = base_fcff

    for year in range(1, PROJECTION_YEARS + 1):
        projected_fcff *= (1 + growth_rate)
        pv_fcffs.append(projected_fcff / ((1 + wacc) ** year))

    pv_terminal = ((projected_fcff * (1 + TERMINAL_GROWTH))
                   / (wacc - TERMINAL_GROWTH)
                   / ((1 + wacc) ** PROJECTION_YEARS))

    enterprise_value = sum(pv_fcffs) + pv_terminal
    equity_value     = enterprise_value + cash - debt

    if not shares or shares == 0:
        return None
    return equity_value / shares


def calculate_dcf(data, symbol):
    fcff_values = data["fcff_values"]

    if len(fcff_values) < 2:
        print(f"{symbol}: Not enough FCFF history.")
        return

    fcff_chron = fcff_values[::-1]
    growth_rates = []
    for i in range(1, len(fcff_chron)):
        prev, curr = fcff_chron[i - 1], fcff_chron[i]
        if prev > 0:
            growth_rates.append((curr / prev) - 1)

    if not growth_rates:
        print(f"{symbol}: Could not compute a valid growth rate.")
        return

    base_growth   = float(np.mean(growth_rates))
    base_growth   = max(min(base_growth, 0.30), -0.05)
    current_price = data["price"]

    print(f"\n{'=' * 60}")
    print(f"  DCF Analysis (FCFF): {symbol}")
    print(f"{'=' * 60}")
    print(f"  Historical FCFF (most recent first):")
    for date, v in zip(data["dates"], fcff_values):
        print(f"    {str(date)[:4]}  ${v / 1e9:>8.2f}B")
    print(f"\n  Base Growth Rate     : {base_growth * 100:.1f}%")
    print(f"  Terminal Growth Rate : {TERMINAL_GROWTH * 100:.1f}%")
    print(f"  Projection Period    : {PROJECTION_YEARS} years")
    if current_price:
        print(f"  Current Price        : ${current_price:.2f}")

    # Compute each scenario
    names   = list(SCENARIOS.keys())
    growths = []
    waccs   = []
    values  = []

    for name, params in SCENARIOS.items():
        g    = base_growth * params["growth_mult"]
        g    = max(min(g, 0.50), -0.10)   # wider cap for bear/bull extremes
        wacc = WACC + params["wacc_adj"]
        iv   = run_dcf(fcff_values, data["cash"], data["debt"], data["shares"], g, wacc)
        growths.append(g)
        waccs.append(wacc)
        values.append(iv)

    # Print scenario table
    col = 12
    print(f"\n  {'':22}" + "".join(f"{n:>{col}}" for n in names))
    print(f"  {'-' * (22 + col * len(names))}")
    print(f"  {'Growth Rate':<22}" + "".join(f"{g*100:>{col-1}.1f}%" for g in growths))
    print(f"  {'WACC':<22}"        + "".join(f"{w*100:>{col-1}.1f}%" for w in waccs))
    print(f"  {'Intrinsic Value':<22}" + "".join(
        f"${iv:>{col-1}.2f}" if iv is not None else f"{'N/A':>{col}}" for iv in values
    ))
    if current_price:
        print(f"  {'Upside / (Downside)':<22}" + "".join(
            f"{((iv/current_price)-1)*100:>+{col-1}.1f}%" if iv is not None else f"{'N/A':>{col}}"
            for iv in values
        ))
    print(f"  {'=' * (22 + col * len(names))}")


def main():
    for symbol in TICKERS:
        print(f"\nFetching data for {symbol}...")
        data = fetch_data(symbol)
        if data is None:
            print(f"{symbol}: Could not retrieve financial data.")
            continue
        calculate_dcf(data, symbol)


if __name__ == "__main__":
    main()