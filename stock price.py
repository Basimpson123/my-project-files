import yfinance as yf

tickers = ["QCOM", "AMD", "GOOGL", "BWXT", "CEG", "LULU", "VEEV", "DECK", "ADBE", "NVO", "MELI"]

print("Fetching data, please wait...")
data = []
for ticker in tickers:
    stock = yf.Ticker(ticker)
    fast = stock.fast_info
    info = stock.info
    data.append({
        "ticker": ticker,
        "price": fast.last_price,
        "prev_close": fast.previous_close,
        "day_high": fast.day_high,
        "day_low": fast.day_low,
        "pe": info.get("trailingPE"),
        "ev_ebitda": info.get("enterpriseToEbitda"),
    })

print()
print("PRICE TABLE")
print(f"{'Ticker':<8} {'Price':>10} {'Prev Close':>12} {'Day High':>10} {'Day Low':>10}")
print("-" * 55)
for d in data:
    print(f"{d['ticker']:<8} ${d['price']:>9.2f} ${d['prev_close']:>11.2f} ${d['day_high']:>9.2f} ${d['day_low']:>9.2f}")

print()
print("VALUATION TABLE")
print(f"{'Ticker':<8} {'P/E':>8} {'EV/EBITDA':>12}")
print("-" * 30)
for d in data:
    pe_str = f"{d['pe']:.2f}" if d['pe'] else "N/A"
    ev_str = f"{d['ev_ebitda']:.2f}" if d['ev_ebitda'] else "N/A"
    print(f"{d['ticker']:<8} {pe_str:>8} {ev_str:>12}")