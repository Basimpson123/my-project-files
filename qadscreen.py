"""
S&P 500 Stock Screen
Criteria:
  - S&P 500 constituent
  - Market Cap > $2B
  - P/E < 25
  - Forward P/E < 25
  - Gross Margin > 25%
  - Operating Margin > 15%
  - Return on Equity > 10%
  - Debt/Equity < 1
  - Price >= 15% below 52-week high
  - ND/EBITDA < 4.0x  (deal-breaker)
  - Interest Coverage > 4.0x  (deal-breaker)

Each run compares against the previous run's CSV and reports what entered,
exited, and held. Only the most recent results are kept; no archive.
"""

import io
import os
import pandas as pd
import yfinance as yf
import requests
import time

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qadscreen_results.csv")


def get_sp500_tickers():
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    df = pd.read_html(io.StringIO(resp.text))[0]
    return df["Symbol"].str.replace(".", "-", regex=False).tolist()


def load_previous():
    """Previous run's results as {ticker: row}, or None if there is no prior run."""
    if not os.path.exists(OUTPUT_PATH):
        return None
    try:
        df = pd.read_csv(OUTPUT_PATH)
        if "Ticker" not in df.columns:
            return None
        return {row["Ticker"]: row for _, row in df.iterrows()}
    except Exception:
        return None


def check_stock(ticker: str) -> tuple[dict | None, str | None]:
    """Screen one ticker.

    Returns (result, reason). On a pass, result is the row dict and reason is
    None. On a fail, result is None and reason explains what went wrong -- so a
    stock that dropped out of the screen can say why, and a Yahoo data gap can
    be told apart from genuine deterioration.
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info
    except Exception:
        return None, "no data returned from Yahoo Finance"

    def get(key):
        v = info.get(key)
        return v if v not in (None, "N/A", "Infinity", float("inf")) else None

    required = {
        "marketCap":        get("marketCap"),
        "trailingPE":       get("trailingPE"),
        "forwardPE":        get("forwardPE"),
        "grossMargins":     get("grossMargins"),
        "operatingMargins": get("operatingMargins"),
        "returnOnEquity":   get("returnOnEquity"),
        "debtToEquity":     get("debtToEquity"),
        "fiftyTwoWeekHigh": get("fiftyTwoWeekHigh"),
        "ebitda":           get("ebitda"),
    }
    current_price = get("currentPrice") or get("regularMarketPrice")
    if current_price is None:
        required["currentPrice"] = None

    missing = [k for k, v in required.items() if v is None]
    if missing:
        return None, "MISSING DATA: " + ", ".join(missing)

    market_cap   = required["marketCap"]
    pe           = required["trailingPE"]
    fwd_pe       = required["forwardPE"]
    gross_margin = required["grossMargins"]
    op_margin    = required["operatingMargins"]
    roe          = required["returnOnEquity"]
    debt_equity  = required["debtToEquity"]
    week_high_52 = required["fiftyTwoWeekHigh"]
    ebitda       = required["ebitda"]
    total_debt   = get("totalDebt") or 0
    total_cash   = get("totalCash") or 0

    # yfinance returns debtToEquity as a ratio * 100 for some tickers; normalize
    # Values > 10 are likely in percent form (e.g. 45 means 0.45)
    de_ratio = debt_equity / 100 if debt_equity > 10 else debt_equity

    pct_below_high = (week_high_52 - current_price) / week_high_52

    net_debt  = total_debt - total_cash
    nd_ebitda = net_debt / ebitda if ebitda > 0 else float("inf")

    # Interest coverage (EBIT / Interest Expense) from income statement.
    # Avoids deferred-revenue distortion that inflates simple D/E-based checks.
    interest_coverage = None
    try:
        stmt = t.income_stmt
        ebit = None
        interest_exp = None
        for key in ("EBIT", "Operating Income"):
            if key in stmt.index and pd.notna(stmt.loc[key].iloc[0]):
                ebit = stmt.loc[key].iloc[0]
                break
        for key in ("Interest Expense", "Interest Expense Non Operating"):
            if key in stmt.index and pd.notna(stmt.loc[key].iloc[0]):
                interest_exp = abs(stmt.loc[key].iloc[0])
                break
        if ebit is not None and interest_exp and interest_exp > 0:
            interest_coverage = ebit / interest_exp
        elif ebit is not None and total_debt < 1e8:
            # negligible debt → effectively infinite coverage
            interest_coverage = float("inf")
    except Exception:
        pass

    # Can't verify interest coverage for a levered company → disqualify
    if interest_coverage is None:
        return None, f"MISSING DATA: interest coverage unavailable (total debt ${total_debt / 1e9:.1f}B)"

    nd_desc = ("negative EBITDA" if ebitda <= 0
               else f"{nd_ebitda:.2f}x (need < 4.0x)")
    ic_desc = ("effectively debt-free" if interest_coverage == float("inf")
               else f"{interest_coverage:.1f}x (need > 4.0x)")

    checks = [
        ("Market Cap",       market_cap > 2_000_000_000, f"${market_cap / 1e9:.1f}B (need > $2B)"),
        ("P/E",              pe < 25,                    f"{pe:.1f} (need < 25)"),
        ("Fwd P/E",          fwd_pe < 25,                f"{fwd_pe:.1f} (need < 25)"),
        ("Gross Margin",     gross_margin > 0.25,        f"{gross_margin * 100:.1f}% (need > 25%)"),
        ("Op Margin",        op_margin > 0.15,           f"{op_margin * 100:.1f}% (need > 15%)"),
        ("ROE",              roe > 0.10,                 f"{roe * 100:.1f}% (need > 10%)"),
        ("D/E",              de_ratio < 1.0,             f"{de_ratio:.2f} (need < 1.0)"),
        ("% Below 52W High", pct_below_high >= 0.15,     f"{pct_below_high * 100:.1f}% (need >= 15%)"),
        ("ND/EBITDA",        nd_ebitda < 4.0,            nd_desc),
        ("Int Coverage",     interest_coverage > 4.0,    ic_desc),
    ]

    failed = [f"{name} {desc}" for name, ok, desc in checks if not ok]
    if failed:
        return None, "; ".join(failed)

    ic_display = round(interest_coverage, 1) if interest_coverage != float("inf") else 999.9

    return {
        "Ticker":           ticker,
        "Company":          info.get("longName", ""),
        "Industry":         info.get("industry", "Unknown"),
        "Market Cap ($B)":  round(market_cap / 1e9, 2),
        "P/E":              round(pe, 1),
        "Fwd P/E":          round(fwd_pe, 1),
        "Gross Margin %":   round(gross_margin * 100, 1),
        "Op Margin %":      round(op_margin * 100, 1),
        "ROE %":            round(roe * 100, 1),
        "D/E":              round(de_ratio, 2),
        "ND/EBITDA":        round(nd_ebitda, 2),
        "Int Coverage":     ic_display,
        "% Below 52W High": round(pct_below_high * 100, 1),
    }, None


def print_diff(previous, results, reasons, universe):
    """Report what changed since the previous run."""
    if previous is None:
        print("=== No previous run found - this run becomes the baseline ===\n")
        return

    current = {r["Ticker"]: r for r in results}
    prev_tickers = set(previous)
    curr_tickers = set(current)

    entered = sorted(curr_tickers - prev_tickers)
    held    = sorted(curr_tickers & prev_tickers)
    exited  = sorted(prev_tickers - curr_tickers)

    # Three different kinds of exit, and they mean very different things.
    dropped_index, data_gap, genuine = [], [], []
    for ticker in exited:
        reason = reasons.get(ticker)
        if ticker not in universe:
            dropped_index.append(ticker)
        elif reason is None or reason.startswith("MISSING DATA") or reason.startswith("no data"):
            data_gap.append((ticker, reason or "not screened"))
        else:
            genuine.append((ticker, reason))

    print("=" * 70)
    print("CHANGES SINCE LAST RUN")
    print("=" * 70)

    if not (entered or genuine or data_gap or dropped_index):
        noun = "stock" if len(held) == 1 else "stocks"
        print(f"\nNo changes. All {len(held)} {noun} from the previous run still pass.\n")
        return

    if entered:
        print(f"\nENTERED ({len(entered)})")
        for ticker in entered:
            row = current[ticker]
            print(f"  + {ticker:<6} {str(row['Company'])[:32]:<32} "
                  f"${row['Market Cap ($B)']:>8.1f}B  {row['Industry']}")

    if genuine:
        print(f"\nEXITED ({len(genuine)})")
        for ticker, reason in genuine:
            company = str(previous[ticker].get("Company", ""))[:32]
            print(f"  - {ticker:<6} {company}")
            print(f"    {reason}")

    if data_gap:
        print(f"\nEXITED - DATA UNAVAILABLE ({len(data_gap)})")
        print("    Likely a Yahoo Finance gap rather than a real change. Re-check next run.")
        for ticker, reason in data_gap:
            company = str(previous[ticker].get("Company", ""))[:32]
            print(f"  ? {ticker:<6} {company}")
            print(f"    {reason}")

    if dropped_index:
        print(f"\nEXITED - NO LONGER IN S&P 500 ({len(dropped_index)})")
        for ticker in dropped_index:
            company = str(previous[ticker].get("Company", ""))[:32]
            print(f"  x {ticker:<6} {company}")

    if held:
        print(f"\nHELD ({len(held)})")
        for i in range(0, len(held), 10):
            print("    " + ", ".join(held[i:i + 10]))

    print()


def run_screen():
    previous = load_previous()
    if previous is None:
        print("No previous results found - this run will become the baseline.")
    else:
        print(f"Loaded {len(previous)} stocks from the previous run.")

    print("Fetching S&P 500 tickers...")
    tickers = get_sp500_tickers()
    universe = set(tickers)
    print(f"Screening {len(tickers)} tickers...\n")

    # Failure reasons are only needed for stocks that were on the previous list.
    watch = set(previous) if previous else set()

    results = []
    reasons = {}
    for i, ticker in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {ticker}", end="\r")
        result, reason = check_stock(ticker)
        if result:
            results.append(result)
        elif ticker in watch:
            reasons[ticker] = reason
        time.sleep(0.1)  # be polite to Yahoo Finance

    print("\n")

    print_diff(previous, results, reasons, universe)

    if not results:
        print("No stocks passed the screen.")
        return

    df = pd.DataFrame(results).sort_values(
        ["Industry", "Market Cap ($B)"], ascending=[True, False]
    )
    df.index = range(1, len(df) + 1)

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 120)
    pd.set_option("display.float_format", "{:.1f}".format)

    print(f"=== {len(df)} stocks passed the screen ===\n")
    print(df.to_string())
    print()

    df.to_csv(OUTPUT_PATH, index=False)
    print(f"Results saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    run_screen()
