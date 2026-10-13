"""Reproducible reporting. Returns use total marked equity, never cash alone.

All returns are pre-tax. No cash interest or stock dividends are credited.
The benchmark is an explicitly price-only, fully invested fractional-share INTC
buy-and-hold reference, not an equal-exposure or total-return comparison.
"""

from dataclasses import asdict
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # Works on CI and machines without a graphical display.
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, PercentFormatter
import numpy as np
import pandas as pd


def annual_statistics(daily: pd.DataFrame, initial_cash: float) -> pd.DataFrame:
    """Calendar-year mark-to-market P&L; partial years are not annualized.

    The prior year's last equity is the next year's opening capital, so the
    first session's loss is included. Each annual drawdown resets its peak
    at that opening capital; full-period drawdown is calculated separately.
    """
    rows = []
    opening = float(initial_cash)
    for year, group in daily.groupby(pd.to_datetime(daily["Date"]).dt.year):
        ending = float(group["equity"].iloc[-1])
        curve = np.r_[opening, group["equity"].to_numpy(dtype=float)]
        drawdown = curve / np.maximum.accumulate(curve) - 1
        rows.append({"year": int(year), "start_equity": opening,
                     "end_equity": ending, "pnl": ending - opening,
                     "return_pct": 100 * (ending / opening - 1),
                     "max_drawdown_pct": -100 * float(drawdown.min())})
        opening = ending
    return pd.DataFrame(rows)


def performance_summary(daily: pd.DataFrame, initial_cash: float) -> dict:
    equity = daily["equity"].to_numpy(dtype=float)
    curve = np.r_[initial_cash, equity]
    dd = curve / np.maximum.accumulate(curve) - 1
    days = (pd.Timestamp(daily["Date"].iloc[-1]) - pd.Timestamp(daily["Date"].iloc[0])).days
    return {
        "start_date": str(pd.Timestamp(daily["Date"].iloc[0]).date()),
        "end_date": str(pd.Timestamp(daily["Date"].iloc[-1]).date()),
        "sessions": len(daily), "initial_cash": initial_cash,
        "final_equity": float(equity[-1]), "net_pnl": float(equity[-1] - initial_cash),
        "total_return_pct": float(100 * (equity[-1] / initial_cash - 1)),
        "cagr_pct": float(100 * ((equity[-1] / initial_cash) ** (365.25 / days) - 1)) if days > 0 and equity[-1] > 0 else None,
        "max_drawdown_pct": float(-100 * dd.min()),
        "max_cash_secured_reserve": float(daily["cash_secured_reserve"].max()),
        "max_indicative_margin": float(daily["indicative_margin"].max()),
        "ending_shares": int(daily["shares"].iloc[-1]),
        "ending_option_liability": float(daily["option_liability"].iloc[-1]),
        "model_based": True,
    }


def _style():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                        "axes.spines.top": False, "axes.spines.right": False,
                        "axes.titleweight": "bold", "axes.labelcolor": "#334155",
                        "text.color": "#142337", "figure.facecolor": "#f8fafc",
                        "axes.facecolor": "#ffffff", "grid.alpha": 0.20})


def save_report(result, destination, title="INTC | Historical model-based short put") -> dict:
    path = Path(destination)
    path.mkdir(parents=True, exist_ok=True)
    daily = result.daily
    initial = result.config.initial_cash
    annual = annual_statistics(daily, initial)
    summary = performance_summary(daily, initial)
    for name, frame in [("daily", daily), ("trades", result.trades),
                        ("events", result.events), ("annual_pnl", annual)]:
        frame.to_csv(path / f"{name}.csv", index=False, float_format="%.8f")
    payload = {"performance": summary, "config": asdict(result.config),
               "interpretation": "Hypothetical model-valued options; not historical option fills. "
               "Cash secured. No taxes, dividends or cash interest. Open positions, if any, remain marked."}
    (path / "summary.json").write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")

    _style()
    fig, axes = plt.subplots(4, 1, figsize=(12, 13), gridspec_kw={"height_ratios": [2, 1, 1.3, 1.2]})
    fig.suptitle(title, fontsize=19, x=0.075, ha="left", y=0.975)
    fig.text(0.075, 0.943,
             f"Return {summary['total_return_pct']:+.2f}%  |  Max drawdown {summary['max_drawdown_pct']:.2f}%  |  "
             f"Starting capital ${initial:,.0f}", fontsize=11)
    dates = pd.to_datetime(daily["Date"])
    axes[0].plot(dates, daily["equity"], color="#087f8c", lw=2, label="Cash-secured short put")
    benchmark = initial * daily["Close"] / daily["Close"].iloc[0]
    axes[0].plot(dates, benchmark, color="#94a3b8", lw=1.2, label="INTC price-only buy & hold (different exposure)")
    axes[0].set_ylabel("Equity (USD)")
    axes[0].legend(loc="best", fontsize=9)
    axes[0].yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"${value:,.0f}"))
    axes[1].fill_between(dates, daily["drawdown"] * 100, 0, color="#dc5b5b", alpha=.65)
    axes[1].set_ylabel("Drawdown (%)")
    axes[1].yaxis.set_major_formatter(PercentFormatter())
    axes[2].plot(dates, daily["cash_secured_reserve"], color="#1671b5", label="Actual cash reserve")
    axes[2].plot(dates, daily["indicative_margin"], color="#dc8a24", alpha=.8,
                 label="Indicative uncovered-put margin (diagnostic only)")
    axes[2].set_ylabel("Capital (USD)")
    axes[2].legend(loc="best", fontsize=9)
    colors = ["#087f8c" if pnl >= 0 else "#dc5b5b" for pnl in annual["pnl"]]
    axes[3].bar(annual["year"].astype(str), annual["pnl"], color=colors, width=.6)
    axes[3].axhline(0, color="#64748b", lw=.8)
    axes[3].set_ylabel("Annual P&L (USD)")
    for axis in axes:
        axis.grid(axis="y")
    fig.text(.075, .018, "Model premiums from lagged realized volatility. Synthetic strikes/expiries. "
             "Fees and slippage included. Research only.", fontsize=9, color="#64748b")
    fig.tight_layout(rect=[.035, .04, .99, .935], h_pad=1.5)
    fig.savefig(path / "performance.png", dpi=160)
    plt.close(fig)
    return summary


def save_scenario_comparison(results: dict, destination):
    """Two deterministic teaching scenarios; never label these market history."""
    _style()
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), sharex="col")
    for column, (name, result) in enumerate(results.items()):
        d = result.daily
        axes[0, column].plot(d["Date"], d["Close"], color="#1671b5", label="Synthetic stock close")
        axes[0, column].set_title(name.replace("_", " ").title(), fontsize=14)
        axes[0, column].set_ylabel("Stock price (USD)")
        axes[1, column].plot(d["Date"], d["equity"] - result.config.initial_cash,
                            color="#087f8c", lw=2, label="Total marked P&L")
        axes[1, column].plot(d["Date"], d["unrealized_pnl"], color="#dc8a24", ls="--", label="Unrealized P&L")
        axes[1, column].axhline(0, color="#94a3b8", lw=.7)
        axes[1, column].set_ylabel("P&L (USD)")
        assigned = d.loc[d["shares"] > 0]
        if not assigned.empty:
            for ax in axes[:, column]:
                ax.axvspan(assigned["Date"].iloc[0], assigned["Date"].iloc[-1], alpha=.12, color="#dc5b5b", label="Assigned stock held")
        for ax in axes[:, column]:
            ax.grid(axis="y")
            ax.legend(fontsize=8, loc="best")
            ax.tick_params(axis="x", rotation=25)
    fig.suptitle("Expiration vs. crash & physical assignment", fontsize=19, y=.99)
    fig.text(.05, .02, "DETERMINISTIC SYNTHETIC SCENARIOS  |  Same initial capital, strike rule and entry volatility; one put per scenario.", fontsize=9)
    fig.tight_layout(rect=[.01, .065, 1, .95])
    fig.savefig(Path(destination) / "scenario_comparison.png", dpi=160)
    plt.close(fig)
