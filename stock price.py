import yfinance as yf
import statistics

tickers = ["QCOM", "AMD", "GOOGL", "BWXT", "CEG", "LULU", "VEEV", "DECK", "ADBE", "NVO", "MELI"]
ticker_set = set(tickers)

print("Fetching stock data, please wait...")
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
        "industry_key": info.get("industryKey", ""),
        "industry": info.get("industry", "N/A"),
    })

# Collect industry peer symbols
print("Fetching industry peer lists...")
industry_peers = {}
unique_keys = {d["industry_key"] for d in data if d["industry_key"]}

for key in unique_keys:
    try:
        companies_df = yf.Industry(key).top_companies
        syms = list(companies_df["symbol"]) if "symbol" in companies_df.columns else list(companies_df.index)
        industry_peers[key] = [s for s in syms if s not in ticker_set][:5]
    except Exception:
        industry_peers[key] = []

# Fetch peer valuation data
all_peers = list({sym for syms in industry_peers.values() for sym in syms})
print(f"Fetching data for {len(all_peers)} industry peers...")
peer_data = {}
for sym in all_peers:
    try:
        info = yf.Ticker(sym).info
        pe = info.get("trailingPE")
        ev = info.get("enterpriseToEbitda")
        peer_data[sym] = {
            "pe": pe if pe and 0 < pe < 500 else None,
            "ev_ebitda": ev if ev and 0 < ev < 200 else None,
        }
    except Exception:
        peer_data[sym] = {"pe": None, "ev_ebitda": None}

# Calculate median industry averages
industry_avgs = {}
for key, peers in industry_peers.items():
    pe_vals = [peer_data[s]["pe"] for s in peers if s in peer_data and peer_data[s]["pe"]]
    ev_vals = [peer_data[s]["ev_ebitda"] for s in peers if s in peer_data and peer_data[s]["ev_ebitda"]]
    industry_avgs[key] = {
        "pe": statistics.median(pe_vals) if pe_vals else None,
        "ev_ebitda": statistics.median(ev_vals) if ev_vals else None,
    }

for d in data:
    avgs = industry_avgs.get(d["industry_key"], {"pe": None, "ev_ebitda": None})
    d["ind_avg_pe"] = avgs["pe"]
    d["ind_avg_ev_ebitda"] = avgs["ev_ebitda"]

print()
print("PRICE TABLE")
print(f"{'Ticker':<8} {'Price':>10} {'Prev Close':>12} {'Day High':>10} {'Day Low':>10}")
print("-" * 55)
for d in data:
    print(f"{d['ticker']:<8} ${d['price']:>9.2f} ${d['prev_close']:>11.2f} ${d['day_high']:>9.2f} ${d['day_low']:>9.2f}")

print()
print("VALUATION TABLE  (industry average = median of top 5 peers)")
print(f"{'Ticker':<8} {'P/E':>8} {'Ind Avg P/E':>13} {'EV/EBITDA':>12} {'Ind Avg EV/EBITDA':>20}  Industry")
print("-" * 100)
for d in data:
    pe_str = f"{d['pe']:.2f}" if d['pe'] else "N/A"
    ev_str = f"{d['ev_ebitda']:.2f}" if d['ev_ebitda'] else "N/A"
    ind_pe_str = f"{d['ind_avg_pe']:.2f}" if d['ind_avg_pe'] else "N/A"
    ind_ev_str = f"{d['ind_avg_ev_ebitda']:.2f}" if d['ind_avg_ev_ebitda'] else "N/A"
    print(f"{d['ticker']:<8} {pe_str:>8} {ind_pe_str:>13} {ev_str:>12} {ind_ev_str:>20}  {d['industry']}")
