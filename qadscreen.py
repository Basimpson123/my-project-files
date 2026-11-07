"""
S&P 500 Stock Screen (quality at a discount)

Criteria (thresholds are defined once, in the constants below):
  - S&P 500 constituent (one share class per company)
  - Market Cap > $2B
  - 0 < P/E < 25
  - 0 < Forward P/E < 25
  - Gross Margin > 25%
  - Operating Margin > 15%
  - Return on Equity > 10%
  - Positive shareholder equity
  - Debt/Equity < 1
  - Price >= 15% below 52-week high
  - ND/EBITDA < 4.0x  (deal-breaker)
  - Interest Coverage > 4.0x  (deal-breaker)

Each run compares against the previous run's CSV and reports what entered,
exited, and held. The CSV's Status column marks each passing stock as Added or
Held, and stocks removed this run are listed at the bottom with the reason.
Only the most recent run is kept; no archive.
"""

import io
import os
import pandas as pd
import yfinance as yf
import requests
import time

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qadscreen_results.csv")

# --- Screen thresholds (edit here to tune the screen) ---
MIN_MARKET_CAP     = 2_000_000_000  # dollars
MAX_PE             = 25
MAX_FWD_PE         = 25
MIN_GROSS_MARGIN   = 0.25           # fraction
MIN_OP_MARGIN      = 0.15
MIN_ROE            = 0.10
MAX_DEBT_EQUITY    = 1.0            # ratio (x), not percent
MIN_BELOW_52W_HIGH = 0.15           # price must be at least this far below the 52-week high
MAX_ND_EBITDA      = 4.0
MIN_INT_COVERAGE   = 4.0
NEGLIGIBLE_DEBT    = 100_000_000    # below this, interest coverage counts as infinite

# --- Yahoo Finance fetching ---
REQUEST_PAUSE = 0.1  # seconds between tickers, to stay under rate limits
RETRY_PAUSE   = 2.0  # seconds before retrying a held stock whose data came back incomplete


def get_sp500_tickers():
    """Return (tickers, share_class_dupes).

    Companies with several share classes in the index (e.g. GOOGL/GOOG) are
    screened once, using the Class A ticker. share_class_dupes holds the
    tickers that were skipped so they aren't reported as leaving the index.
    """
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    df = pd.read_html(io.StringIO(resp.text))[0]
    df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)

    # Sort so a "(Class A)" listing comes first within each company, then keep it.
    df["_class_a"] = df["Security"].str.contains(r"\(Class A\)", regex=True)
    ordered = df.sort_values("_class_a", ascending=False, kind="stable")
    kept = ordered.drop_duplicates("CIK", keep="first")
    dupes = set(df["Symbol"]) - set(kept["Symbol"])

    tickers = df.loc[df["Symbol"].isin(kept["Symbol"]), "Symbol"].tolist()  # original order
    return tickers, dupes


def load_previous():
    """Previous run's passing stocks as {ticker: row}, or None if there is no prior run.

    Rows marked Removed are last run's exits, not passes, so they're skipped.
    """
    if not os.path.exists(OUTPUT_PATH):
        return None
    try:
        df = pd.read_csv(OUTPUT_PATH)
        if "Ticker" not in df.columns:
            return None
        if "Status" in df.columns:
            df = df[~df["Status"].astype(str).str.startswith("Removed")]
        return {row["Ticker"]: row for _, row in df.iterrows()}
    except Exception:
        return None


def _clean(v):
    return v if v not in (None, "N/A", "Infinity", float("inf")) else None


def _fetch_info(t, required_keys, attempts):
    """Fetch t.info, retrying if it errors or comes back missing required fields."""
    info = {}
    for attempt in range(attempts):
        try:
            info = t.info or {}
        except Exception:
            info = {}
        price = _clean(info.get("currentPrice")) or _clean(info.get("regularMarketPrice"))
        if price is not None and all(_clean(info.get(k)) is not None for k in required_keys):
            break
        if attempt < attempts - 1:
            time.sleep(RETRY_PAUSE)
    return info


def _interest_coverage(t, total_debt, attempts):
    """EBIT / interest expense from the income statement; inf if debt is negligible; None if unknown."""
    for attempt in range(attempts):
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
                return ebit / interest_exp
            if total_debt < NEGLIGIBLE_DEBT:
                return float("inf")
            return None
        except Exception:
            if attempt < attempts - 1:
                time.sleep(RETRY_PAUSE)
    return None


def check_stock(ticker: str, need_reason: bool = False) -> tuple[dict | None, str | None]:
    """Screen one ticker.

    Returns (result, reason). On a pass, result is the row dict and reason is
    None. On a fail, result is None and reason explains what went wrong -- so a
    stock that dropped out of the screen can say why, and a Yahoo data gap can
    be told apart from genuine deterioration.

    need_reason=True (used for stocks that passed last run) retries flaky
    Yahoo responses and always runs every check, so the exit reason is complete.
    Otherwise the income statement is only fetched when the cheaper checks pass.
    """
    attempts = 2 if need_reason else 1
    required_keys = ("marketCap", "trailingPE", "forwardPE", "grossMargins", "operatingMargins",
                     "returnOnEquity", "fiftyTwoWeekHigh", "ebitda")

    t = yf.Ticker(ticker)
    info = _fetch_info(t, required_keys, attempts)
    if not info:
        return None, "no data returned from Yahoo Finance"

    def get(key):
        return _clean(info.get(key))

    required = {k: get(k) for k in required_keys}
    current_price = get("currentPrice") or get("regularMarketPrice")
    if current_price is None:
        required["currentPrice"] = None

    total_debt = get("totalDebt") or 0
    total_cash = get("totalCash") or 0

    # Yahoo reports debtToEquity as a percentage (45.3 means 0.453x), and omits
    # it entirely for companies with no debt.
    debt_equity_pct = get("debtToEquity")
    if debt_equity_pct is None and total_debt == 0:
        debt_equity_pct = 0.0
    required["debtToEquity"] = debt_equity_pct

    # Yahoo leaves ROE and D/E blank when equity is negative, so check this
    # first -- otherwise it would be misreported as a data gap.
    book_value = get("bookValue")
    if book_value is not None and book_value <= 0:
        return None, "Equity negative shareholder equity"

    missing = [k for k, v in required.items() if v is None]
    if missing:
        return None, "MISSING DATA: " + ", ".join(missing)

    market_cap   = required["marketCap"]
    pe           = required["trailingPE"]
    fwd_pe       = required["forwardPE"]
    gross_margin = required["grossMargins"]
    op_margin    = required["operatingMargins"]
    roe          = required["returnOnEquity"]
    week_high_52 = required["fiftyTwoWeekHigh"]
    ebitda       = required["ebitda"]
    de_ratio     = debt_equity_pct / 100

    negative_equity = de_ratio < 0  # backstop if bookValue was missing

    pct_below_high = (week_high_52 - current_price) / week_high_52

    net_debt  = total_debt - total_cash
    nd_ebitda = net_debt / ebitda if ebitda > 0 else float("inf")
    nd_desc = ("negative EBITDA" if ebitda <= 0
               else f"{nd_ebitda:.2f}x (need < {MAX_ND_EBITDA}x)")

    checks = [
        ("Market Cap",       market_cap > MIN_MARKET_CAP,
         f"${market_cap / 1e9:.1f}B (need > ${MIN_MARKET_CAP / 1e9:g}B)"),
        ("P/E",              0 < pe < MAX_PE,
         f"{pe:.1f} (need 0 to {MAX_PE:g})"),
        ("Fwd P/E",          0 < fwd_pe < MAX_FWD_PE,
         f"{fwd_pe:.1f} (need 0 to {MAX_FWD_PE:g})"),
        ("Gross Margin",     gross_margin > MIN_GROSS_MARGIN,
         f"{gross_margin * 100:.1f}% (need > {MIN_GROSS_MARGIN * 100:g}%)"),
        ("Op Margin",        op_margin > MIN_OP_MARGIN,
         f"{op_margin * 100:.1f}% (need > {MIN_OP_MARGIN * 100:g}%)"),
        ("ROE",              roe > MIN_ROE,
         f"{roe * 100:.1f}% (need > {MIN_ROE * 100:g}%)"),
        ("Equity",           not negative_equity,
         "negative shareholder equity"),
        ("D/E",              de_ratio < MAX_DEBT_EQUITY,
         f"{de_ratio:.2f} (need < {MAX_DEBT_EQUITY:g})"),
        ("% Below 52W High", pct_below_high >= MIN_BELOW_52W_HIGH,
         f"{pct_below_high * 100:.1f}% (need >= {MIN_BELOW_52W_HIGH * 100:g}%)"),
        ("ND/EBITDA",        nd_ebitda < MAX_ND_EBITDA, nd_desc),
    ]
    failed = [f"{name} {desc}" for name, ok, desc in checks if not ok]

    # The income statement is a second Yahoo request; skip it when the stock has
    # already failed and we don't need a complete exit reason.
    if failed and not need_reason:
        return None, "; ".join(failed)

    interest_coverage = _interest_coverage(t, total_debt, attempts)

    if interest_coverage is None:
        if failed:
            return None, "; ".join(failed)
        # Can't verify interest coverage for a levered company -> disqualify
        return None, f"MISSING DATA: interest coverage unavailable (total debt ${total_debt / 1e9:.1f}B)"

    if not interest_coverage > MIN_INT_COVERAGE:
        failed.append(f"Int Coverage {interest_coverage:.1f}x (need > {MIN_INT_COVERAGE}x)")
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


def compute_diff(previous, results, reasons, universe, share_class_dupes=frozenset()):
    """Work out what changed since the previous run, or None if there is no prior run.

    Tickers in share_class_dupes (e.g. GOOG when GOOGL is screened) are not
    counted as exits -- the company is still covered by its other share class.
    """
    if previous is None:
        return None

    current = {r["Ticker"]: r for r in results}
    prev_tickers = set(previous) - set(share_class_dupes)
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

    return {
        "current": current, "previous": previous,
        "entered": entered, "held": held,
        "genuine": genuine, "data_gap": data_gap, "dropped_index": dropped_index,
    }


def build_output_df(results_df, diff):
    """Passing stocks tagged Added/Held, followed by this run's removals with reasons."""
    out = results_df.copy()
    if diff is None:
        out.insert(0, "Status", "Baseline")
        out["Reason"] = ""
        return out

    entered = set(diff["entered"])
    out.insert(0, "Status", ["Added" if t in entered else "Held" for t in out["Ticker"]])
    out["Reason"] = ""

    previous = diff["previous"]
    removed = (
        [(t, "Removed - failed criteria", r) for t, r in diff["genuine"]]
        + [(t, "Removed - data unavailable", f"{r} (likely a Yahoo gap - re-check next run)")
           for t, r in diff["data_gap"]]
        + [(t, "Removed - left S&P 500", "No longer an S&P 500 constituent")
           for t in diff["dropped_index"]]
    )
    rows = []
    for ticker, status, reason in removed:
        # Carry over last run's figures so you can see what the stock looked like when it passed.
        row = {k: v for k, v in previous[ticker].items() if k in out.columns}
        row.update(Status=status, Ticker=ticker, Reason=reason)
        rows.append(row)
    if rows:
        out = pd.concat([out, pd.DataFrame(rows, columns=out.columns)], ignore_index=True)
    return out


def print_diff(diff):
    """Report what changed since the previous run."""
    if diff is None:
        print("=== No previous run found - this run becomes the baseline ===\n")
        return

    current, previous = diff["current"], diff["previous"]
    entered, held = diff["entered"], diff["held"]
    genuine, data_gap, dropped_index = diff["genuine"], diff["data_gap"], diff["dropped_index"]

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
    tickers, share_class_dupes = get_sp500_tickers()
    universe = set(tickers)
    if share_class_dupes:
        print(f"Skipping duplicate share classes: {', '.join(sorted(share_class_dupes))}")
    print(f"Screening {len(tickers)} tickers...\n")

    # Stocks that passed last run get retries and a complete exit reason.
    watch = set(previous) if previous else set()

    results = []
    reasons = {}
    for i, ticker in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {ticker:<6}", end="\r")
        result, reason = check_stock(ticker, need_reason=ticker in watch)
        if result:
            results.append(result)
        elif ticker in watch:
            reasons[ticker] = reason
        time.sleep(REQUEST_PAUSE)  # be polite to Yahoo Finance

    print("\n")

    diff = compute_diff(previous, results, reasons, universe, share_class_dupes)
    print_diff(diff)

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

    try:
        build_output_df(df, diff).to_csv(OUTPUT_PATH, index=False)
    except PermissionError:
        print(f"Could not write {OUTPUT_PATH} - is it open in Excel? Close it and re-run.")
        return
    print(f"Results saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    run_screen()
