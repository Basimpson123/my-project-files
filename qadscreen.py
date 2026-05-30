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
"""

import io
import pandas as pd
import yfinance as yf
import requests
import time


def get_sp500_tickers():
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    df = pd.read_html(io.StringIO(resp.text))[0]
    return df["Symbol"].str.replace(".", "-", regex=False).tolist()


def check_stock(ticker: str) -> dict | None:
    try:
        t = yf.Ticker(ticker)
        info = t.info
    except Exception:
        return None

    def get(key):
        v = info.get(key)
        return v if v not in (None, "N/A", "Infinity", float("inf")) else None

    market_cap    = get("marketCap")
    pe            = get("trailingPE")
    fwd_pe        = get("forwardPE")
    gross_margin  = get("grossMargins")
    op_margin     = get("operatingMargins")
    roe           = get("returnOnEquity")
    debt_equity   = get("debtToEquity")
    week_high_52  = get("fiftyTwoWeekHigh")
    current_price = get("currentPrice") or get("regularMarketPrice")
    ebitda        = get("ebitda")
    total_debt    = get("totalDebt") or 0
    total_cash    = get("totalCash") or 0

    if any(v is None for v in [market_cap, pe, fwd_pe, gross_margin,
                                op_margin, roe, debt_equity,
                                week_high_52, current_price, ebitda]):
        return None

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
        return None

    passes = (
        market_cap        >  2_000_000_000 and
        pe                <  25            and
        fwd_pe            <  25            and
        gross_margin      >  0.25          and
        op_margin         >  0.15          and
        roe               >  0.10          and
        de_ratio          <  1.0           and
        pct_below_high    >= 0.15          and
        nd_ebitda         <  4.0           and
        interest_coverage >  4.0
    )

    if not passes:
        return None

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
    }


def run_screen():
    print("Fetching S&P 500 tickers...")
    tickers = get_sp500_tickers()
    print(f"Screening {len(tickers)} tickers...\n")

    results = []
    for i, ticker in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {ticker}", end="\r")
        result = check_stock(ticker)
        if result:
            results.append(result)
        time.sleep(0.1)  # be polite to Yahoo Finance

    print("\n")

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

    df.to_csv("qadscreen_results.csv", index=False)
    print("Results saved to qadscreen_results.csv")


if __name__ == "__main__":
    run_screen()
