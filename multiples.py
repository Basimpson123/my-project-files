import yfinance as yf
import statistics

try:
    from colorama import init, Fore, Style
    init(autoreset=True)
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False
    class _Stub:
        def __getattr__(self, _): return ""
    Fore = Style = _Stub()

# ── helpers ──────────────────────────────────────────────────────────────────

def _valid(v, lo, hi):
    return v if (v is not None and lo < v < hi) else None

def _fmt(v, spec=".1f"):
    return format(v, spec) if v is not None else "N/A"

def _fi(v):
    return f"${v:.2f}" if v is not None else "N/A"

def _pct(current, target):
    if current and target:
        return (target - current) / current * 100
    return None

def _color_pct(pct, width=10):
    if pct is None:
        return f"{'N/A':>{width}}"
    sign = "+" if pct >= 0 else ""
    raw = f"{sign}{pct:.1f}%"
    padded = f"{raw:>{width}}"
    if not HAS_COLOR:
        return padded
    color = Fore.GREEN if pct >= 10 else (Fore.RED if pct <= -10 else Fore.YELLOW)
    return color + padded + Style.RESET_ALL

# ── config ────────────────────────────────────────────────────────────────────

TICKERS = ["QCOM", "AMD", "GOOGL", "BWXT", "CEG", "LHX", "VEEV", "DECK", "ADBE", "MELI"]
TICKER_SET = set(TICKERS)
PEER_LIMIT = 10
MULT_FIELDS = ["pe", "forward_pe", "ev_ebitda", "ev_revenue", "pfcf", "peg"]

# ── 1. fetch watchlist data ───────────────────────────────────────────────────

print("Fetching stock data, please wait...")
data = []

for ticker in TICKERS:
    stock = yf.Ticker(ticker)
    fast = stock.fast_info
    info = stock.info

    price      = fast.last_price
    market_cap = info.get("marketCap")
    ev         = info.get("enterpriseValue")
    shares     = market_cap / price if market_cap and price else None
    net_debt   = (ev - market_cap) if ev is not None and market_cap else None

    trailing_pe  = info.get("trailingPE")
    forward_pe   = info.get("forwardPE")
    eps          = price / trailing_pe if trailing_pe and price else None
    forward_eps  = price / forward_pe  if forward_pe  and price else None

    fcf          = info.get("freeCashflow")
    fcf_per_share = fcf / shares if fcf and shares else None
    pfcf         = market_cap / fcf if market_cap and fcf and fcf > 0 else None

    data.append({
        "ticker":          ticker,
        "price":           price,
        "prev_close":      fast.previous_close,
        "day_high":        fast.day_high,
        "day_low":         fast.day_low,
        "pe":              trailing_pe,
        "forward_pe":      forward_pe,
        "ev_ebitda":       info.get("enterpriseToEbitda"),
        "ev_revenue":      info.get("enterpriseToRevenue"),
        "pfcf":            pfcf,
        "peg":             info.get("trailingPegRatio"),
        "eps":             eps,
        "forward_eps":     forward_eps,
        "ebitda":          info.get("ebitda"),
        "revenue":         info.get("totalRevenue"),
        "fcf_per_share":   fcf_per_share,
        "earnings_growth": info.get("earningsGrowth"),
        "shares":          shares,
        "net_debt":        net_debt,
        "industry_key":    info.get("industryKey", ""),
        "industry":        info.get("industry", "N/A"),
    })

# ── 2. fetch industry peers ───────────────────────────────────────────────────

print("Fetching industry peer lists...")
industry_peers = {}
for key in {d["industry_key"] for d in data if d["industry_key"]}:
    try:
        df   = yf.Industry(key).top_companies
        syms = list(df["symbol"]) if "symbol" in df.columns else list(df.index)
        industry_peers[key] = [s for s in syms if s not in TICKER_SET][:PEER_LIMIT]
    except Exception:
        industry_peers[key] = []

# ── 3. fetch peer multiples ───────────────────────────────────────────────────

all_peers = list({s for syms in industry_peers.values() for s in syms})
print(f"Fetching data for {len(all_peers)} industry peers...")
peer_data = {}
for sym in all_peers:
    try:
        info   = yf.Ticker(sym).info
        mc     = info.get("marketCap")
        fcf_p  = info.get("freeCashflow")
        pfcf_p = mc / fcf_p if mc and fcf_p and fcf_p > 0 else None
        peer_data[sym] = {
            "pe":         _valid(info.get("trailingPE"),          0, 500),
            "forward_pe": _valid(info.get("forwardPE"),           0, 500),
            "ev_ebitda":  _valid(info.get("enterpriseToEbitda"),  0, 200),
            "ev_revenue": _valid(info.get("enterpriseToRevenue"), 0, 100),
            "pfcf":       _valid(pfcf_p,                          0, 500),
            "peg":        _valid(info.get("trailingPegRatio"),    0,  20),
        }
    except Exception:
        peer_data[sym] = {k: None for k in MULT_FIELDS}

# ── 4. industry median averages ───────────────────────────────────────────────

industry_avgs = {}
for key, peers in industry_peers.items():
    valid_peers = [s for s in peers if s in peer_data]
    avgs = {}
    for field in MULT_FIELDS:
        vals = [peer_data[s][field] for s in valid_peers if peer_data[s].get(field)]
        avgs[field] = (statistics.median(vals), len(vals)) if vals else (None, 0)
    industry_avgs[key] = avgs

for d in data:
    avgs = industry_avgs.get(d["industry_key"], {})
    for field in MULT_FIELDS:
        val, count = avgs.get(field, (None, 0))
        d[f"ind_{field}"] = val
        d[f"n_{field}"]   = count

# ── 5. implied fair values ────────────────────────────────────────────────────

def _implied(d):
    shares, net_debt = d["shares"], d["net_debt"]

    def from_ev(median_mult, base):
        if median_mult and base and base > 0 and shares and net_debt is not None:
            return (median_mult * base - net_debt) / shares
        return None

    eps, feps, fcf_ps = d["eps"], d["forward_eps"], d["fcf_per_share"]
    growth = d["earnings_growth"]

    return {
        "P/E":       d["ind_pe"]         * eps    if d["ind_pe"]         and eps    else None,
        "Fwd P/E":   d["ind_forward_pe"] * feps   if d["ind_forward_pe"] and feps   else None,
        "EV/EBITDA": from_ev(d["ind_ev_ebitda"], d["ebitda"]),
        "EV/Rev":    from_ev(d["ind_ev_revenue"], d["revenue"]),
        "P/FCF":     d["ind_pfcf"]       * fcf_ps if d["ind_pfcf"]       and fcf_ps else None,
        "PEG":       d["ind_peg"] * (growth * 100) * eps
                     if d["ind_peg"] and growth and growth > 0 and eps else None,
    }

for d in data:
    d["implied"] = _implied(d)
    valid = [v for v in d["implied"].values() if v and v > 0]
    d["blended"] = statistics.mean(valid) if valid else None

# ── output ────────────────────────────────────────────────────────────────────

print()
print("PRICE TABLE")
print(f"{'Ticker':<8} {'Price':>10} {'Prev Close':>12} {'Day High':>10} {'Day Low':>10}")
print("-" * 55)
for d in data:
    print(f"{d['ticker']:<8} ${d['price']:>9.2f} ${d['prev_close']:>11.2f} ${d['day_high']:>9.2f} ${d['day_low']:>9.2f}")

print()
print("MULTIPLES TABLE")
print(f"{'Ticker':<8} {'Tr P/E':>8} {'Fwd P/E':>9} {'EV/EBITDA':>11} {'EV/Rev':>8} {'P/FCF':>7} {'PEG':>6}  Industry")
print("-" * 105)
for d in data:
    print(
        f"{d['ticker']:<8}"
        f" {_fmt(d['pe']):>8}"
        f" {_fmt(d['forward_pe']):>9}"
        f" {_fmt(d['ev_ebitda']):>11}"
        f" {_fmt(d['ev_revenue']):>8}"
        f" {_fmt(d['pfcf']):>7}"
        f" {_fmt(d['peg']):>6}"
        f"  {d['industry']}"
    )

print()
print("INDUSTRY MEDIAN MULTIPLES  (n = peers used, up to 10 per industry)")
print(f"{'Ticker':<8} {'Tr P/E':>10} {'Fwd P/E':>11} {'EV/EBITDA':>13} {'EV/Rev':>10} {'P/FCF':>9} {'PEG':>8}")
print("-" * 75)
for d in data:
    def _fa(f, _d=d):
        v, n = _d.get(f"ind_{f}"), _d.get(f"n_{f}", 0)
        return f"{_fmt(v)}({n})" if v is not None else "N/A"
    print(
        f"{d['ticker']:<8}"
        f" {_fa('pe'):>10}"
        f" {_fa('forward_pe'):>11}"
        f" {_fa('ev_ebitda'):>13}"
        f" {_fa('ev_revenue'):>10}"
        f" {_fa('pfcf'):>9}"
        f" {_fa('peg'):>8}"
    )

print()
print("IMPLIED FAIR VALUE  (implied price per multiple; blended = mean of available multiples)")
print(f"{'Ticker':<8} {'Price':>9} {'P/E':>9} {'Fwd P/E':>9} {'EV/EBITDA':>11} {'EV/Rev':>9} {'P/FCF':>8} {'PEG':>8} {'Blended':>9} {'vs Price':>10}")
print("-" * 100)
for d in data:
    imp = d["implied"]
    print(
        f"{d['ticker']:<8}"
        f" {_fi(d['price']):>9}"
        f" {_fi(imp['P/E']):>9}"
        f" {_fi(imp['Fwd P/E']):>9}"
        f" {_fi(imp['EV/EBITDA']):>11}"
        f" {_fi(imp['EV/Rev']):>9}"
        f" {_fi(imp['P/FCF']):>8}"
        f" {_fi(imp['PEG']):>8}"
        f" {_fi(d['blended']):>9}"
        f"{_color_pct(_pct(d['price'], d['blended']))}"
    )
