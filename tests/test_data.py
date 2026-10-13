"""Data integrity tests use small explicit fixtures; no network is required."""

import hashlib
import json

import numpy as np
import pandas as pd
import pytest
import requests

from intc_put import data


def test_bundled_snapshot_matches_provenance():
    prices = data.load_prices()
    manifest = json.loads((data.DEFAULT_DATA_PATH.parent / "provenance.json").read_text())
    assert len(prices) == manifest["rows"] == 2011
    assert prices.Date.iloc[0] == pd.Timestamp("2018-01-02")
    assert prices.Date.iloc[-1] == pd.Timestamp("2025-12-31")
    assert prices.groupby(prices.Date.dt.year).size().to_dict() == {
        2018: 251, 2019: 252, 2020: 253, 2021: 252,
        2022: 251, 2023: 250, 2024: 252, 2025: 250,
    }
    assert hashlib.sha256(data.DEFAULT_DATA_PATH.read_bytes()).hexdigest() == manifest["csv_sha256"]
    assert (prices.Dividends > 0).sum() == manifest["dividend_events"] == 27
    # A real discontinuity must survive ingestion rather than be interpolated.
    crash = prices.set_index("Date").loc["2024-08-01":"2024-08-02", "Close"]
    assert crash.iloc[-1] / crash.iloc[0] - 1 == pytest.approx(-0.260585, abs=1e-6)


@pytest.mark.parametrize(
    ("values", "match"),
    [([0, 10], "positive"), ([-1, 10], "positive"), ([np.nan, 10], "non-finite"),
     ([np.inf, 10], "non-finite"), (["invalid", "10"], "numeric")],
)
def test_rejects_invalid_close(tmp_path, values, match):
    path = tmp_path / "bad.csv"
    pd.DataFrame({"Date": ["2024-01-02", "2024-01-03"], "Close": values}).to_csv(path, index=False)
    with pytest.raises(ValueError, match=match):
        data.load_prices(path)


@pytest.mark.parametrize(
    ("dates", "match"),
    [(["2024-01-02", "2024-01-02"], "duplicate"),
     (["2024-01-03", "2024-01-02"], "increasing"),
     (["2024-01-02", None], "missing"),
     (["2024-01-02", "2024-99-99"], "valid")],
)
def test_rejects_invalid_dates(tmp_path, dates, match):
    path = tmp_path / "bad.csv"
    pd.DataFrame({"Date": dates, "Close": [30, 31]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match=match):
        data.load_prices(path)


def test_requires_columns_and_rows(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Date,Adjusted Close\n2024-01-02,30\n")
    with pytest.raises(ValueError, match="Missing required"):
        data.load_prices(path)
    path.write_text("Date,Close\n")
    with pytest.raises(ValueError, match="empty"):
        data.load_prices(path)


def test_does_not_fill_unsupplied_days(tmp_path):
    path = tmp_path / "two.csv"
    path.write_text("Date,Close\n2024-01-02,30\n2024-01-05,29\n")
    assert len(data.load_prices(path)) == 2


def test_rejects_inconsistent_ohlc():
    with pytest.raises(ValueError, match="High"):
        data._validate_prices(pd.DataFrame({
            "Date": ["2024-01-02"], "Open": [30], "High": [29], "Low": [28], "Close": [30],
        }))


def chart_fixture():
    # This tiny test-only fixture is not market data and is never used in reports.
    timestamp = 1704205800  # 2024-01-02 14:30 UTC, 09:30 New York.
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": "INTC", "currency": "USD", "exchangeTimezoneName": "America/New_York"},
        "timestamp": [timestamp],
        "indicators": {
            "quote": [{"open": [30], "high": [31], "low": [29], "close": [30], "volume": [100]}],
            "adjclose": [{"adjclose": [25]}],
        },
        "events": {"dividends": {str(timestamp): {"date": timestamp, "amount": 0.125}}},
    }]}}


def test_parse_uses_quote_close_and_preserves_dividend():
    prices = data._parse_chart(chart_fixture(), "2024-01-01", "2024-01-04")
    assert prices.Close.tolist() == [30]
    assert prices.Dividends.tolist() == [0.125]
    assert prices.Date.iloc[0] == pd.Timestamp("2024-01-02")


def test_rejects_splits_and_out_of_range_data():
    payload = chart_fixture()
    payload["chart"]["result"][0]["events"]["splits"] = {"1": {"numerator": 2}}
    with pytest.raises(ValueError, match="splits"):
        data._parse_chart(payload, "2024-01-01", "2024-01-04")
    with pytest.raises(ValueError, match="outside"):
        data._parse_chart(chart_fixture(), "2024-01-03", "2024-01-04")


def test_network_failure_preserves_existing_snapshot(tmp_path, monkeypatch):
    destination = tmp_path / "prices.csv"
    original = b"Date,Close\n2024-01-02,30\n"
    destination.write_bytes(original)

    def unavailable(*args, **kwargs):
        raise requests.ConnectionError("test offline")

    monkeypatch.setattr(data.requests, "get", unavailable)
    with pytest.raises(RuntimeError, match="Existing local data was not changed"):
        data.download_prices(destination=destination)
    assert destination.read_bytes() == original


def test_refresh_writes_verifiable_csv_and_manifest(tmp_path, monkeypatch):
    class Response:
        content = json.dumps(chart_fixture()).encode()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(data.requests, "get", lambda *args, **kwargs: Response())
    destination = data.download_prices("2024-01-01", "2024-01-04", tmp_path / "prices.csv")
    manifest = json.loads((tmp_path / "provenance.json").read_text())
    assert len(data.load_prices(destination)) == 1
    assert manifest["csv_sha256"] == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert manifest["requested_end_exclusive"] == "2024-01-04"
    assert manifest["dividend_events"] == 1


def test_rejects_invalid_request_dates_before_network():
    with pytest.raises(ValueError, match="later"):
        data.download_prices("2024-01-04", "2024-01-01")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        data.download_prices("bad", "2024-01-01")
