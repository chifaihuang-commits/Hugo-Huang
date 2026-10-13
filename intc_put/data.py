"""Read an offline INTC snapshot or explicitly refresh it from Yahoo Finance.

Price assumptions and limitations
---------------------------------
* ``Close`` is Yahoo's ``indicators.quote.close``, not ``adjclose``. Yahoo's
  Close is split adjusted but not dividend adjusted; no splits were reported
  during the bundled 2018--2025 sample. Never use a total-return adjusted price
  against a historical nominal option strike.
* Daily bars cannot expose intraday paths, executable quotes, option spreads,
  assignment notices, or whether a fill was possible. No rows are forward filled.
* The optional Dividends column records ex-date cash amounts for transparency;
  it does not itself credit dividends to the backtest's cash account.
* The public chart endpoint is unofficial and may change, rate-limit requests,
  or revise history. A failed refresh raises an error; it never fabricates data.
* Data-provider terms apply to market data independently of this project's code
  license. This small snapshot is attributed educational research data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests


DEFAULT_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "intc_daily.csv"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/INTC"
PRICE_BASIS = (
    "Yahoo quote OHLC (split adjusted, NOT dividend adjusted); adjclose unused. "
    "No splits reported inside the requested sample."
)


def _validate_prices(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate daily data without repairing, sorting, or imputing observations."""
    required = {"Date", "Close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing required price columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("Price data is empty")
    frame = frame.copy()
    try:
        dates = pd.to_datetime(frame["Date"], format="%Y-%m-%d", errors="raise")
    except (ValueError, TypeError) as exc:
        raise ValueError("Date must contain valid YYYY-MM-DD trading dates") from exc
    if dates.isna().any():
        raise ValueError("Date contains missing values")
    if dates.dt.tz is not None:
        raise ValueError("Date must be timezone-naive trading dates")
    if not (dates == dates.dt.normalize()).all():
        raise ValueError("Date must be daily dates without intraday timestamps")
    if dates.duplicated().any():
        raise ValueError("Price data contains duplicate dates")
    if not dates.is_monotonic_increasing:
        raise ValueError("Price data must be in strictly increasing date order")
    frame["Date"] = dates
    for column in ("Open", "High", "Low", "Close", "Volume", "Dividends"):
        if column not in frame:
            continue
        try:
            values = pd.to_numeric(frame[column], errors="raise")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{column} must contain numeric values") from exc
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"{column} contains missing or non-finite values")
        if column in ("Volume", "Dividends"):
            if (values < 0).any():
                raise ValueError(f"{column} must be nonnegative")
        elif (values <= 0).any():
            raise ValueError(f"{column} prices must be positive")
        if column == "Volume" and (values != np.floor(values)).any():
            raise ValueError("Volume must contain whole-share counts")
        frame[column] = values
    if {"Open", "High", "Low", "Close"}.issubset(frame.columns):
        if (frame["High"] < frame[["Open", "Low", "Close"]].max(axis=1)).any():
            raise ValueError("High must be at least Open, Low, and Close")
        if (frame["Low"] > frame[["Open", "High", "Close"]].min(axis=1)).any():
            raise ValueError("Low must be at most Open, High, and Close")
    # Weekends/holidays/halts cannot be inferred reliably from weekdays alone.
    # Validate supplied observations, but never invent a missing trading bar.
    return frame.reset_index(drop=True)


def load_prices(path: str | Path = DEFAULT_DATA_PATH) -> pd.DataFrame:
    """Return validated data with a datetime64 ``Date`` column and numeric Close.

    Reads only the supplied CSV; the default bundled snapshot works offline.
    OHLC, Volume and Dividends are retained and validated when supplied. Missing
    observations, duplicate dates, unsorted rows and invalid prices raise errors.
    """
    return _validate_prices(pd.read_csv(Path(path)))


def _date_timestamp(value: str) -> int:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as exc:
        raise ValueError("start and end must be YYYY-MM-DD dates") from exc
    return int(parsed.timestamp())


def _parse_chart(payload: dict, start: str, end: str) -> pd.DataFrame:
    """Convert only source-reported INTC daily observations, preserving nulls."""
    try:
        chart = payload["chart"]
        if chart.get("error"):
            raise ValueError(f"Yahoo Finance returned an error: {chart['error']}")
        results = chart.get("result")
        if not results or len(results) != 1:
            raise ValueError("Yahoo Finance returned no unique INTC result")
        result = results[0]
        if result["meta"]["symbol"] != "INTC":
            raise ValueError("Yahoo Finance returned an unexpected symbol")
        if result["meta"]["currency"] != "USD":
            raise ValueError("Yahoo Finance returned a non-USD price series")
        events = result.get("events", {})
        # Corporate-action adjustments to contract strikes/share counts are not
        # implemented. Reject splits instead of silently mixing nominal units.
        if events.get("splits"):
            raise ValueError("Stock splits are unsupported by this backtest")
        exchange_tz = ZoneInfo(result["meta"].get("exchangeTimezoneName", "America/New_York"))
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        dates = [datetime.fromtimestamp(ts, exchange_tz).strftime("%Y-%m-%d") for ts in timestamps]
        frame = pd.DataFrame(
            {"Date": dates, **{name.title(): quote[name] for name in ("open", "high", "low", "close", "volume")}}
        )
        dividends = {}
        for event in events.get("dividends", {}).values():
            date = datetime.fromtimestamp(event["date"], exchange_tz).strftime("%Y-%m-%d")
            dividends[date] = dividends.get(date, 0.0) + float(event["amount"])
        frame["Dividends"] = frame["Date"].map(dividends).fillna(0.0)
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Malformed Yahoo Finance chart response") from exc
    frame = _validate_prices(frame)
    if (frame["Date"] < pd.Timestamp(start)).any() or (frame["Date"] >= pd.Timestamp(end)).any():
        raise ValueError("Yahoo Finance returned dates outside the requested interval")
    return frame


def download_prices(
    start: str = "2018-01-01",
    end: str = "2026-01-01",
    destination: str | Path = DEFAULT_DATA_PATH,
) -> Path:
    """Download INTC daily bars in [start, end), write CSV + provenance, return Path.

    Uses requests with normal TLS certificate verification. ``end`` is exclusive. The complete
    download is validated before replacing files; bad data/network failures leave
    an existing snapshot unchanged. Refreshed data may change historical results.
    """
    period1, period2 = _date_timestamp(start), _date_timestamp(end)
    if period2 <= period1:
        raise ValueError("end must be later than start")
    query = urlencode({"period1": period1, "period2": period2, "interval": "1d", "events": "div,splits"})
    url = f"{YAHOO_CHART_URL}?{query}"
    try:
        response = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (educational INTC backtest)"},
        )
        response.raise_for_status()
        raw = response.content
    except requests.RequestException as exc:
        raise RuntimeError(f"Unable to download INTC from Yahoo Finance: {exc}. Existing local data was not changed.") from exc
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Yahoo Finance did not return valid JSON") from exc
    frame = _parse_chart(payload, start, end)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Keep numeric source precision in the snapshot; display rounding belongs in
    # reports. This avoids silently changing prices used against option strikes.
    csv_text = frame.to_csv(index=False, date_format="%Y-%m-%d", lineterminator="\n")
    csv_bytes = csv_text.encode("utf-8")
    provenance = {
        "symbol": "INTC",
        "provider": "Yahoo Finance chart endpoint",
        "source_url": url,
        "source_history_page": "https://finance.yahoo.com/quote/INTC/history/",
        "adjusted_close_definition": "https://in.help.yahoo.com/kb/adjusted-close-sln28256.html",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested_start_inclusive": start,
        "requested_end_exclusive": end,
        "actual_start": frame["Date"].iloc[0].strftime("%Y-%m-%d"),
        "actual_end": frame["Date"].iloc[-1].strftime("%Y-%m-%d"),
        "rows": len(frame),
        "currency": "USD",
        "exchange_timezone": "America/New_York",
        "price_basis": PRICE_BASIS,
        "dividends": "Ex-date dividend amounts are retained; the strategy does not credit dividends.",
        "dividend_events": int((frame["Dividends"] > 0).sum()),
        "split_events": 0,
        "missing_data_policy": "Reject null or invalid rows; never forward fill or invent observations.",
        "csv_file": destination.name,
        "csv_sha256": hashlib.sha256(csv_bytes).hexdigest(),
        "source_response_sha256": hashlib.sha256(raw).hexdigest(),
        "data_notice": "Source data remains subject to provider terms. Code license does not grant market-data rights. Educational research snapshot; not audited exchange data.",
    }
    temporary_csv = destination.with_suffix(destination.suffix + ".tmp")
    provenance_path = destination.parent / "provenance.json"
    temporary_provenance = provenance_path.with_suffix(".json.tmp")
    temporary_csv.write_bytes(csv_bytes)
    temporary_provenance.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    temporary_csv.replace(destination)
    temporary_provenance.replace(provenance_path)
    return destination
