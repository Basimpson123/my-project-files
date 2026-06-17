# Requirements: yfinance, pandas, numpy, scipy, matplotlib
#   pip install yfinance pandas numpy scipy matplotlib

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Portfolio configuration — edit here or pass --config path/to/file.json
# ---------------------------------------------------------------------------
DEFAULT_PORTFOLIO = {
    "tickers": {
        "VOO": 0.65,
        "MELI": 0.07,
        "QCOM": 0.05,
        "GOOGL": 0.04,
        "CEG": 0.03,
        "AMD":  0.03,
        "BWXT":  0.03,
        "MSFT":  0.03,
        "LHX":  0.02,
        "AVGO":  0.02,
        "DECK":  0.01,
        "VEEV":  0.01,
        "ADBE":  0.01,
    },
    "benchmark": "SPY",
    "lookback": "3y",          # yfinance period string: 1y, 2y, 3y, 5y, 10y
    "frequency": "daily",      # "daily" or "monthly"
    "risk_free_rate": 0.041,   # 4.1% — Pablo Fernandez survey of 54 countries, 2025
}
# ---------------------------------------------------------------------------


def load_prices(tickers: list[str], period: str) -> pd.DataFrame:
    """Download adjusted-close prices for all tickers from Yahoo Finance."""
    raw = yf.download(tickers, period=period, auto_adjust=True, progress=False)

    # yfinance returns a MultiIndex when >1 ticker, single-level otherwise
    if isinstance(raw.columns, pd.MultiIndex):
        prices = raw["Close"]
    else:
        prices = raw[["Close"]].rename(columns={"Close": tickers[0]})

    # Warn and drop any ticker that came back empty
    missing = [t for t in tickers if t not in prices.columns or prices[t].isna().all()]
    if missing:
        print(f"[WARNING] Could not download data for: {missing} — dropping them.")
        prices = prices.drop(columns=[t for t in missing if t in prices.columns])

    prices = prices.dropna(how="all")
    return prices


def compute_returns(
    prices: pd.DataFrame,
    weights: dict[str, float],
    frequency: str,
) -> tuple[pd.Series, pd.DataFrame, dict[str, float], int]:
    """
    Build the weighted portfolio return series and per-holding return series.

    Returns
    -------
    port_rets   : weighted portfolio periodic return series
    hold_rets   : DataFrame of individual holding returns (only loaded tickers)
    weights     : renormalized weights (drops tickers that failed to download)
    periods_per_year : 252 for daily, 12 for monthly
    """
    available = [t for t in weights if t in prices.columns]
    dropped = set(weights) - set(available)
    if dropped:
        print(f"[WARNING] Dropping tickers from weights: {dropped}")

    # Renormalize weights so they still sum to 1.0
    raw_w = {t: weights[t] for t in available}
    total = sum(raw_w.values())
    weights = {t: v / total for t, v in raw_w.items()}

    prices = prices[available]

    if frequency == "monthly":
        # Resample to month-end before computing returns
        prices = prices.resample("ME").last()
        periods_per_year = 12
    else:
        periods_per_year = 252

    hold_rets = prices.pct_change().dropna()

    w_series = pd.Series(weights)
    # Weighted sum of each day's/month's holding returns
    port_rets = hold_rets.dot(w_series[hold_rets.columns])
    port_rets.name = "portfolio"

    return port_rets, hold_rets, weights, periods_per_year


def risk_metrics(port_rets: pd.Series, rf_annual: float, periods_per_year: int) -> dict:
    """
    Compute standalone risk/return metrics for a return series.

    CAGR    : compound annual growth rate — total wealth effect per year
    Vol     : annualized standard deviation of periodic returns
    Sharpe  : excess return per unit of total risk
    Sortino : like Sharpe but penalizes only downside volatility
    Max DD  : largest peak-to-trough decline in cumulative wealth
    """
    ppy = periods_per_year
    rf_per_period = (1 + rf_annual) ** (1 / ppy) - 1  # risk-free rate per period

    # CAGR: grow $1 by each period's return, then annualize
    cum_growth = (1 + port_rets).prod()
    n_periods = len(port_rets)
    cagr = cum_growth ** (ppy / n_periods) - 1

    # Annualized volatility: scale periodic std by sqrt(periods per year)
    vol = port_rets.std() * np.sqrt(ppy)

    # Sharpe: mean excess return divided by total volatility, annualized
    excess = port_rets - rf_per_period
    sharpe = (excess.mean() / port_rets.std()) * np.sqrt(ppy)

    # Sortino: only penalize returns below the risk-free hurdle (downside dev)
    downside = excess[excess < 0]
    downside_dev = np.sqrt((downside**2).mean()) * np.sqrt(ppy)
    sortino = (excess.mean() * ppy) / downside_dev if downside_dev > 0 else np.nan

    # Max drawdown: biggest drop from a running peak in the cumulative return index
    cum_idx = (1 + port_rets).cumprod()
    rolling_peak = cum_idx.cummax()
    drawdown = (cum_idx - rolling_peak) / rolling_peak
    max_dd = drawdown.min()  # most negative value

    return {
        "CAGR": cagr,
        "Annualized Volatility": vol,
        "Sharpe Ratio": sharpe,
        "Sortino Ratio": sortino,
        "Max Drawdown": max_dd,
    }


def benchmark_metrics(
    port_rets: pd.Series,
    bench_rets: pd.Series,
    rf_annual: float,
    periods_per_year: int,
) -> dict:
    """
    Regress portfolio excess returns on benchmark excess returns.

    Beta    : sensitivity to the benchmark (slope of regression)
    Alpha   : annualized return not explained by benchmark exposure (intercept)
    R²      : fraction of portfolio variance explained by the benchmark
    Up/Down capture : how much portfolio participates in benchmark up/down moves
    """
    ppy = periods_per_year
    rf_per_period = (1 + rf_annual) ** (1 / ppy) - 1

    # Align both series on the same dates
    aligned = pd.concat([port_rets, bench_rets], axis=1).dropna()
    aligned.columns = ["port", "bench"]

    p_excess = aligned["port"] - rf_per_period
    b_excess = aligned["bench"] - rf_per_period

    slope, intercept, r_value, _, _ = stats.linregress(b_excess, p_excess)

    beta = slope
    # Annualize the per-period intercept (alpha)
    alpha = (1 + intercept) ** ppy - 1
    r_squared = r_value**2

    # Upside capture: how portfolio does vs benchmark when benchmark is up
    up_mask = aligned["bench"] > 0
    up_capture = (
        aligned.loc[up_mask, "port"].mean() / aligned.loc[up_mask, "bench"].mean()
        if up_mask.any() else np.nan
    )

    # Downside capture: how portfolio does vs benchmark when benchmark is down
    dn_mask = aligned["bench"] < 0
    dn_capture = (
        aligned.loc[dn_mask, "port"].mean() / aligned.loc[dn_mask, "bench"].mean()
        if dn_mask.any() else np.nan
    )

    return {
        "Beta": beta,
        "Alpha (annualized)": alpha,
        "R-Squared": r_squared,
        "Upside Capture": up_capture,
        "Downside Capture": dn_capture,
    }


def diversification_metrics(
    hold_rets: pd.DataFrame,
    weights: dict[str, float],
) -> tuple[dict, pd.DataFrame]:
    """
    Quantify how spread out and correlated the holdings are.

    Correlation matrix : pairwise linear dependence between holding returns
    Top-5 weight       : sum of the five largest positions
    HHI (Herfindahl)   : sum of squared weights; higher = more concentrated
    Effective N        : 1/HHI — the equivalent number of equal-weight positions
    """
    corr_matrix = hold_rets.corr()

    w = pd.Series(weights).sort_values(ascending=False)
    top5 = w.head(5).sum()

    hhi = (w**2).sum()  # Herfindahl-Hirschman Index
    effective_n = 1 / hhi  # intuition: 4 = like holding 4 equal positions

    div_metrics = {
        "Number of Holdings": len(weights),
        "Top-5 Weight": top5,
        "HHI (Concentration)": hhi,
        "Effective N (1/HHI)": effective_n,
        "Largest Holding": w.index[0],
        "Largest Weight": w.iloc[0],
    }

    return div_metrics, corr_matrix


def _fmt(value, as_pct=False, decimals=4) -> str:
    """Format a number for the summary table."""
    if isinstance(value, float) and np.isnan(value):
        return "N/A"
    if isinstance(value, str):
        return value
    if as_pct:
        return f"{value:.2%}"
    return f"{value:.{decimals}f}"


def print_summary(
    weights: dict,
    r_metrics: dict,
    b_metrics: dict,
    d_metrics: dict,
    benchmark: str,
    rf_rate: float,
    frequency: str,
) -> None:
    """Print a formatted summary to the console."""
    w = 52
    sep = "=" * w

    print(f"\n{sep}")
    print("  PORTFOLIO ANALYSIS SUMMARY")
    print(sep)

    print(f"\n  Frequency : {frequency.capitalize()}")
    print(f"  Benchmark : {benchmark}")
    print(f"  Risk-Free : {rf_rate:.2%} annual\n")

    # Weights
    print("  HOLDINGS & WEIGHTS")
    print("  " + "-" * (w - 2))
    for t, wt in sorted(weights.items(), key=lambda x: -x[1]):
        bar = "#" * int(wt * 40)
        print(f"  {t:<8} {wt:>6.2%}  {bar}")

    # Return / risk
    print(f"\n  RETURN & RISK METRICS")
    print("  " + "-" * (w - 2))
    pct_keys = {"CAGR", "Annualized Volatility", "Max Drawdown"}
    for k, v in r_metrics.items():
        print(f"  {k:<28} {_fmt(v, as_pct=(k in pct_keys))}")

    # Benchmark
    print(f"\n  BENCHMARK METRICS  (vs {benchmark})")
    print("  " + "-" * (w - 2))
    pct_b = {"Alpha (annualized)"}
    for k, v in b_metrics.items():
        print(f"  {k:<28} {_fmt(v, as_pct=(k in pct_b))}")

    # Diversification
    print(f"\n  DIVERSIFICATION")
    print("  " + "-" * (w - 2))
    pct_d = {"Top-5 Weight", "Largest Weight"}
    for k, v in d_metrics.items():
        print(f"  {k:<28} {_fmt(v, as_pct=(k in pct_d))}")

    print(f"\n{sep}\n")


def save_outputs(
    r_metrics: dict,
    b_metrics: dict,
    d_metrics: dict,
    corr_matrix: pd.DataFrame,
) -> None:
    """Save metrics summary and correlation matrix to CSV files."""
    all_metrics = {**r_metrics, **b_metrics, **d_metrics}
    metrics_df = pd.DataFrame.from_dict(
        all_metrics, orient="index", columns=["Value"]
    )
    out_dir = Path(__file__).parent
    metrics_df.index.name = "Metric"
    metrics_df.to_csv(out_dir / "portfolio_metrics.csv")
    print(f"  Saved: {out_dir / 'portfolio_metrics.csv'}")

    corr_matrix.to_csv(out_dir / "portfolio_correlation.csv")
    print(f"  Saved: {out_dir / 'portfolio_correlation.csv'}\n")


def plot_cumulative(
    port_rets: pd.Series,
    bench_rets: pd.Series,
    benchmark: str,
) -> None:
    """Plot portfolio vs benchmark cumulative return (requires --plot flag)."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("[WARNING] matplotlib not installed — skipping plot.")
        return

    aligned = pd.concat([port_rets, bench_rets], axis=1).dropna()
    aligned.columns = ["Portfolio", benchmark]
    cum = (1 + aligned).cumprod()

    fig, ax = plt.subplots(figsize=(10, 5))
    cum["Portfolio"].plot(ax=ax, label="Portfolio", linewidth=2)
    cum[benchmark].plot(ax=ax, label=benchmark, linewidth=1.5, linestyle="--")
    ax.set_title("Cumulative Return: Portfolio vs Benchmark")
    ax.set_ylabel("Growth of $1")
    ax.set_xlabel("")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    out_path = Path(__file__).parent / "portfolio_cumulative.png"
    plt.savefig(out_path, dpi=150)
    print(f"  Saved: {out_path}")
    plt.show()


def validate_weights(weights: dict) -> None:
    total = sum(weights.values())
    if not np.isclose(total, 1.0, atol=1e-4):
        sys.exit(f"[ERROR] Weights sum to {total:.6f}, must equal 1.0.")
    if any(w < 0 for w in weights.values()):
        sys.exit("[ERROR] All weights must be non-negative.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Portfolio risk/return analyzer.")
    parser.add_argument(
        "--config", type=str, default=None,
        help="Path to a JSON config file (overrides DEFAULT_PORTFOLIO).",
    )
    parser.add_argument(
        "--plot", action="store_true",
        help="Show and save a cumulative return chart.",
    )
    args = parser.parse_args()

    # Load config
    if args.config:
        cfg = json.loads(Path(args.config).read_text())
    else:
        cfg = DEFAULT_PORTFOLIO

    weights: dict[str, float] = cfg["tickers"]
    benchmark: str = cfg.get("benchmark", "SPY")
    lookback: str = cfg.get("lookback", "3y")
    frequency: str = cfg.get("frequency", "daily")
    rf_rate: float = cfg.get("risk_free_rate", 0.04)

    validate_weights(weights)

    # --- Data loading ---
    print(f"\nDownloading price data ({lookback}, {frequency})...")
    all_tickers = list(weights.keys()) + [benchmark]
    prices = load_prices(all_tickers, lookback)

    bench_prices = prices[[benchmark]] if benchmark in prices.columns else pd.DataFrame()
    port_prices = prices.drop(columns=[benchmark], errors="ignore")

    # --- Returns ---
    port_rets, hold_rets, weights, ppy = compute_returns(
        port_prices, weights, frequency
    )

    if benchmark in prices.columns:
        if frequency == "monthly":
            bench_px = prices[[benchmark]].resample("ME").last()
        else:
            bench_px = prices[[benchmark]]
        bench_rets = bench_px[benchmark].pct_change().dropna()
    else:
        print(f"[WARNING] Benchmark {benchmark} not available — skipping benchmark metrics.")
        bench_rets = None

    # --- Metrics ---
    r_metrics = risk_metrics(port_rets, rf_rate, ppy)

    if bench_rets is not None:
        b_metrics = benchmark_metrics(port_rets, bench_rets, rf_rate, ppy)
    else:
        b_metrics = {k: np.nan for k in ["Beta", "Alpha (annualized)", "R-Squared",
                                          "Upside Capture", "Downside Capture"]}

    d_metrics, corr_matrix = diversification_metrics(hold_rets, weights)

    # --- Output ---
    print_summary(weights, r_metrics, b_metrics, d_metrics, benchmark, rf_rate, frequency)
    save_outputs(r_metrics, b_metrics, d_metrics, corr_matrix)

    if args.plot and bench_rets is not None:
        plot_cumulative(port_rets, bench_rets, benchmark)
    elif args.plot:
        print("[WARNING] Cannot plot without benchmark data.")


if __name__ == "__main__":
    main()
