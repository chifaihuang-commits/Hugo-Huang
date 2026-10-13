"""Command line entry point. Cached real data is the default; no silent fallback."""

import argparse
import hashlib
import json
from pathlib import Path

from .data import download_prices, load_prices
from .engine import Config, run_backtest
from .reports import save_report, save_scenario_comparison
from .scenarios import run_scenarios


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="INTC model-based cash-secured short-put backtest")
    parser.add_argument("--data", type=Path, default=root / "data" / "intc_daily.csv")
    parser.add_argument("--output", type=Path, default=Path("reports"))
    parser.add_argument("--download", action="store_true", help="Explicitly refresh Yahoo daily prices; failure stops the run")
    parser.add_argument("--start", default="2018-01-01", help="Download start, inclusive")
    parser.add_argument("--end", default="2026-01-01", help="Download end, exclusive")
    parser.add_argument("--initial-cash", type=float, default=10000.)
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--otm-pct", type=float, default=.05)
    parser.add_argument("--tenor-days", type=int, default=30)
    parser.add_argument("--hold-days", type=int, default=5, help="Sessions to hold assigned stock")
    parser.add_argument("--tree-steps", type=int, default=100)
    args = parser.parse_args()
    try:
        config = Config(initial_cash=args.initial_cash, contracts=args.contracts,
                        otm_pct=args.otm_pct, tenor_days=args.tenor_days,
                        assignment_hold_days=args.hold_days, tree_steps=args.tree_steps)
        if args.download:
            download_prices(start=args.start, end=args.end, destination=args.data)
        prices = load_prices(args.data)
        result = run_backtest(prices, config)
        summary = save_report(result, args.output / "historical")
        scenarios = run_scenarios()
        scenario_summaries = {name: save_report(run, args.output / name,
            title="SYNTHETIC | " + name.replace("_", " ").title()) for name, run in scenarios.items()}
        save_scenario_comparison(scenarios, args.output)
        manifest = {"historical": summary, "synthetic_scenarios": scenario_summaries,
                    "input_file": args.data.name,
                    "input_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest()}
        (args.output / "run_manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
        print("INTC MODEL-BASED BACKTEST (not historical option quotes)")
        print(f"Sessions: {summary['sessions']} | P&L: ${summary['net_pnl']:,.2f} | "
              f"Return: {summary['total_return_pct']:.2f}% | Max drawdown: {summary['max_drawdown_pct']:.2f}%")
        print(f"Reports saved to: {args.output.resolve()}")
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
