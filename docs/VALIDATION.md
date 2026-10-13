# Validation record

Validation date: 2026-10-13. Executed locally on macOS with Python 3.14,
pandas 3.0.6, NumPy 2.5.4, matplotlib 3.11.2, requests 2.34.2 and pytest 9.1.1.

## Executed checks

```text
python -m pytest -q
47 passed

python -m intc_put
INTC MODEL-BASED BACKTEST (not historical option quotes)
Sessions: 2011 | P&L: $298.40 | Return: 2.98% | Max drawdown: 22.58%

python -m pip check
No broken requirements found.
```

- Installed the project using `pip install -e ".[dev]"` in an isolated environment.
- Downloaded and validated actual Yahoo INTC data; 2,011 observations, 27
  dividend events and no reported splits within the sample.
- Executed independent model, engine, data, reporting and scenario tests.
- Ran the full historical backtest and both reference scenarios to generate
  four PNG charts, daily ledgers, trade/event records, annual statistics and
  JSON summaries. Opened the historical dashboard and scenario comparison
  for visual inspection; labels, curves, legends and annual bars are readable.
- Reconciled historical ending equity to cash and retained shares, and net
  P&L to realized plus unrealized P&L. All annual P&L sums reconcile to the
  full-period P&L within CSV rounding tolerance.

## Test coverage by economic behavior

- One-step CRR hand calculation, zero-rate European benchmark, volatility
  monotonicity, intrinsic/strike bounds and American exercise value.
- Entry premium is offset by option liability; collateral is not double-counted.
- Worthless expiry earns net credit; physical assignment transfers strike cash
  into shares; stock sale realizes the remaining loss exactly once.
- Cashflow events reconcile to cash balances and marked equity every day.
- Multiple contracts, zero-day stock holding, insufficient cash, retained
  stock at the end, lagged strike/volatility and future-price mutation checks.
- Source validation, malformed inputs, duplicate dates, out-of-order data,
  missing values, OHLC consistency, splits and network failure preservation.
- Annual opening capital, total P&L reconciliation and full-period drawdown.
- The paired reference scenarios share an entry premium but settle differently.

## Fixes and iteration limit

Work stayed within the requested maximum of 15 execution/review iterations:
12 development/execution cycles were counted conservatively across parallel
components (3 data acquisition/validation, 4 model/engine/report test runs,
2 engine smoke runs, 2 integrated test/backtest runs, and 1 final ledger/package
integrity check). The final check verified all four PNG files, every report's
cash/equity reconciliation, source checksum and the complete 45-file ZIP.

- A local certificate-store issue in the first data retrieval attempt was fixed
  by using requests with its normal verified CA bundle. TLS was not disabled.
- A test initially used an up-gap that correctly caused the strategy to skip
  a sale because premium did not cover costs; its fixture was corrected.
- Stock-event contract counts were corrected to zero to avoid presenting stock
  sale events as option contracts.
- Reference scenarios retain default parameters so a historical CLI override
  cannot accidentally remove the promised crash-assignment demonstration.

## Result interpretation

Historical put outcomes: 85 completed puts, including 60 worthless expirations
and 25 assignments. There are 24 subsequent stock sales; the final assigned
100 shares remain marked at the sample endpoint. Realized P&L is $408.40;
unrealized stock P&L is -$110.00; combined P&L is $298.40.

The synthetic normal-expiry P&L is +$52.57. The synthetic crash-assignment
P&L is -$1,763.16, including stock-sale slippage. These are controlled examples,
not observations from the historical INTC dataset.

No remote GitHub Actions run is claimed in this record. The workflow is
included, but remote execution depends on publishing the repository.
