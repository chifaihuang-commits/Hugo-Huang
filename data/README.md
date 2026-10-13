# INTC daily price snapshot

`intc_daily.csv` contains **2,011 observed trading days**, January 2, 2018 through
December 31, 2025, retrieved from the
[Yahoo Finance INTC chart endpoint](https://query1.finance.yahoo.com/v8/finance/chart/INTC?period1=1514764800&period2=1767225600&interval=1d&events=div%2Csplits).
The request starts January 1, 2018 and ends January 1, 2026 **exclusive**.
`provenance.json` records the exact retrieval time, URL, date range, observation
counts, price convention, and SHA-256 checksums.

Columns: `Date, Open, High, Low, Close, Volume, Dividends`. Dates are exchange-local
trading dates; prices and dividends are USD per share. Dividends is the cash
dividend amount on its ex-date and zero on other observed days. No dates are
forward filled and no observed crash is smoothed.

## Price convention

The snapshot uses Yahoo's `indicators.quote` OHLC prices, **not** `adjclose`.
Yahoo describes Close as split adjusted and Adjusted Close as adjusted for splits
and dividends/distributions. The endpoint reported **no splits** inside this
sample. The downloader rejects any requested sample with split events because
the model does not adjust outstanding option strikes or contract deliverables.
The downloader does not undo a provider adjustment caused by a split after the
requested date range; manually review the price convention before extending this
project to another symbol or a future corporate action.

The 27 dividend events are retained as source information. The strategy's
accounting does not credit dividends. A dividend-adjusted total-return series
would not be an appropriate replacement for nominal prices against fixed strikes.
See [Yahoo's adjusted-close definition](https://in.help.yahoo.com/kb/adjusted-close-sln28256.html)
and [the historical-price page](https://finance.yahoo.com/quote/INTC/history/).

## Scope and reuse

This is an attributed educational research snapshot of public market facts.
Market-data rights remain with the applicable providers; the repository's code
license does not grant a license to provider data. Review provider terms for your
own use and redistribution. The unofficial endpoint may become unavailable or
revise history. The snapshot is not audited exchange data and contains **no
historical option quotes, implied volatility, bid/ask spreads, or executions**.

Daily bars do not establish an executable price or an intraday path. Validation
rejects null, nonfinite, nonpositive, duplicate, unsorted, or internally inconsistent
rows. It does not infer holidays, missing sessions, or trading halts; supply a
verified exchange calendar if calendar-completeness checks are required.
