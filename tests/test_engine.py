"""Economic invariants and independently calculated cash-flow scenarios."""

import numpy as np
import pandas as pd
import pytest

from intc_put.engine import Config, run_backtest


def prices(closes, start="2024-01-02"):
    return pd.DataFrame(
        {"Date": pd.bdate_range(start, periods=len(closes)), "Close": closes}
    )


def config(**changes):
    settings = dict(
        initial_cash=20_000.0,
        contracts=1,
        otm_pct=0.0,
        tenor_days=2,
        vol_window=3,
        assignment_hold_days=2,
        option_commission=0.65,
        option_slippage=0.02,
        stock_slippage_bps=5.0,
        stock_commission=1.25,
        tree_steps=100,
    )
    settings.update(changes)
    return Config(**settings)


def assert_accounting(result):
    daily = result.daily
    np.testing.assert_allclose(
        daily["equity"],
        daily["cash"] + daily["shares"] * daily["Close"] - daily["option_liability"],
        atol=1e-8,
    )
    np.testing.assert_allclose(
        daily["equity"] - result.config.initial_cash,
        daily["realized_pnl"] + daily["unrealized_pnl"],
        atol=1e-8,
    )
    assert (daily["cash"] >= -1e-8).all(), "Cash-secured strategy must not borrow."
    assert (daily["option_liability"] >= -1e-8).all()
    assert (daily["cash_secured_reserve"] >= -1e-8).all()
    assert not (
        (daily["shares"] > 0) & (daily["cash_secured_reserve"] > 0)
    ).any(), "This implementation permits only one put or assigned stock at a time."
    assert daily.iloc[-1]["realized_pnl"] == pytest.approx(
        result.trades["realized_pnl"].sum(), abs=1e-8
    )


def test_otm_expiration_collects_exact_premium_less_fees():
    result = run_backtest(prices([100.0] * 9), config(), "once", fixed_vol=0.3)
    puts = result.trades[result.trades["kind"] == "put"]
    assert len(puts) == 1
    put = puts.iloc[0]
    assert put["outcome"] == "expired"
    expected_profit = put["gross_premium"] - put["fees"]
    assert expected_profit > 0
    assert result.daily.iloc[-1]["equity"] == pytest.approx(20_000 + expected_profit)
    assert result.daily.iloc[-1]["shares"] == 0
    assert result.daily.iloc[-1]["cash_secured_reserve"] == 0
    assert_accounting(result)


def test_premium_receipt_is_offset_by_option_liability_at_entry():
    cfg = config(tenor_days=5)
    result = run_backtest(prices([100.0] * 9), cfg, "once", fixed_vol=0.3)
    entry = result.daily.iloc[1]
    assert entry["cash"] > cfg.initial_cash
    assert entry["option_liability"] > 0
    # A new short option is not an immediate profit. Only execution costs
    # reduce marked equity; collateral is restricted cash, not an expense.
    expected_cost = cfg.option_commission + cfg.option_slippage * 100
    assert entry["equity"] == pytest.approx(cfg.initial_cash - expected_cost)
    assert entry["cash_secured_reserve"] == pytest.approx(10_000)
    assert entry["margin"] == pytest.approx(10_000)
    assert_accounting(result)


def test_physical_assignment_and_stock_sale_do_not_double_count_loss():
    cfg = config()
    data = prices([100, 100, 90, 80, 70, 72, 75, 76, 78])
    result = run_backtest(data, cfg, "once", fixed_vol=0.3)
    puts = result.trades[result.trades["kind"] == "put"]
    stocks = result.trades[result.trades["kind"] == "stock"]
    assert len(puts) == len(stocks) == 1
    put, stock = puts.iloc[0], stocks.iloc[0]
    assert put["outcome"] == "assigned"
    assert put["strike"] == 100
    premium_net = put["gross_premium"] - put["fees"]

    assigned = result.daily.iloc[3]
    assert assigned["Date"] == put["exit_date"]
    assert assigned["shares"] == 100
    assert assigned["cash"] == pytest.approx(cfg.initial_cash + premium_net - 10_000)
    assert assigned["option_liability"] == 0
    assert assigned["cash_secured_reserve"] == 0
    assert assigned["equity"] == pytest.approx(cfg.initial_cash + premium_net - 2_000)
    assert assigned["unrealized_pnl"] == pytest.approx(-2_000)

    assert result.daily.iloc[4]["shares"] == 100
    assert result.daily.iloc[5]["shares"] == 0
    assert stock["exit_date"] == data.iloc[5]["Date"]
    sale_price = 72 * (1 - cfg.stock_slippage_bps / 10_000)
    stock_pnl = (sale_price - 100) * 100 - cfg.stock_commission
    assert stock["realized_pnl"] == pytest.approx(stock_pnl)
    assert result.daily.iloc[-1]["equity"] == pytest.approx(
        cfg.initial_cash + premium_net + stock_pnl
    )
    assert result.daily.iloc[-1]["unrealized_pnl"] == pytest.approx(0, abs=1e-8)
    assert_accounting(result)


def test_assignment_can_exit_on_the_same_session_when_hold_is_zero():
    cfg = config(assignment_hold_days=0, stock_slippage_bps=0, stock_commission=0)
    result = run_backtest(prices([100, 100, 90, 80, 75, 70]), cfg, "once", fixed_vol=0.3)
    puts = result.trades[result.trades["kind"] == "put"]
    stocks = result.trades[result.trades["kind"] == "stock"]
    assert len(puts) == len(stocks) == 1
    assert puts.iloc[0]["exit_date"] == stocks.iloc[0]["exit_date"]
    assert stocks.iloc[0]["realized_pnl"] == pytest.approx(-2_000)
    assert result.daily.iloc[3]["shares"] == 0
    assert_accounting(result)


def test_sample_ending_before_stock_sale_keeps_unrealized_loss():
    cfg = config(assignment_hold_days=10)
    result = run_backtest(
        prices([100, 100, 90, 80, 70, 60]), cfg, "once", fixed_vol=0.3
    )
    assert len(result.trades) == 1
    assert result.trades.iloc[0]["outcome"] == "assigned"
    last = result.daily.iloc[-1]
    assert last["shares"] == 100
    assert last["stock_value"] == pytest.approx(6_000)
    assert last["unrealized_pnl"] == pytest.approx(-4_000)
    assert "stock_retained" in result.events["event"].values
    assert "stock_sold" not in result.events["event"].values
    assert_accounting(result)


def test_cash_event_ledger_reconciles_every_session():
    result = run_backtest(
        prices([100, 100, 90, 80, 70, 72, 75, 76, 78]),
        config(), "once", fixed_vol=0.3,
    )
    event_flows = (
        result.events.groupby("Date")["cash_flow"].sum()
        .reindex(result.daily["Date"], fill_value=0)
    )
    observed_changes = result.daily["cash"].diff()
    observed_changes.iloc[0] = result.daily.iloc[0]["cash"] - result.config.initial_cash
    np.testing.assert_allclose(observed_changes, event_flows, atol=1e-8)


def test_insufficient_collateral_prevents_an_unfunded_trade():
    cfg = config(initial_cash=1_000)
    result = run_backtest(prices([100.0] * 9), cfg, "once", fixed_vol=0.3)
    assert result.trades.empty
    assert (result.daily["cash_secured_reserve"] == 0).all()
    assert (result.daily["equity"] == 1_000).all()
    assert_accounting(result)


def test_two_contracts_scale_collateral_and_premium():
    data = prices([100.0] * 9)
    one = run_backtest(data, config(initial_cash=30_000), "once", fixed_vol=0.3)
    two = run_backtest(data, config(initial_cash=30_000, contracts=2), "once", fixed_vol=0.3)
    assert two.daily.iloc[1]["cash_secured_reserve"] == pytest.approx(20_000)
    assert two.trades.iloc[0]["gross_premium"] == pytest.approx(
        2 * one.trades.iloc[0]["gross_premium"]
    )
    assert two.daily.iloc[-1]["equity"] - 30_000 == pytest.approx(
        2 * (one.daily.iloc[-1]["equity"] - 30_000)
    )
    assert_accounting(two)


def test_strike_is_selected_from_previous_observed_close():
    result = run_backtest(
        prices([100, 90, 90, 90, 90, 90]),
        config(otm_pct=0.05),
        "once",
        fixed_vol=0.8,
    )
    assert result.trades.iloc[0]["strike"] == pytest.approx(95)


def test_future_price_mutation_cannot_change_past_results():
    # Same session calendar isolates price look-ahead from end-of-data
    # scheduling: no new trade is opened unless its expiry fits in the data.
    closes = 100 + np.sin(np.arange(90) / 3) * 4
    data = prices(closes)
    changed = data.copy()
    changed.loc[50:, "Close"] *= np.linspace(0.2, 2.0, 40)
    cfg = config(tenor_days=7, otm_pct=0.03, assignment_hold_days=2)
    original = run_backtest(data, cfg)
    mutated = run_backtest(changed, cfg)
    pd.testing.assert_frame_equal(
        original.daily.iloc[:50].reset_index(drop=True),
        mutated.daily.iloc[:50].reset_index(drop=True),
        check_exact=False,
        atol=1e-9,
        rtol=1e-10,
    )


def test_truncating_distant_future_preserves_earlier_daily_results():
    data = prices(100 + np.sin(np.arange(90) / 3) * 4)
    cfg = config(tenor_days=7, otm_pct=0.03)
    complete = run_backtest(data, cfg)
    truncated = run_backtest(data.iloc[:60], cfg)
    # Leave ample room for the final expiry; the intentionally different
    # no-new-trade zone near each dataset's tail is not compared.
    pd.testing.assert_frame_equal(
        complete.daily.iloc[:40].reset_index(drop=True),
        truncated.daily.iloc[:40].reset_index(drop=True),
        check_exact=False,
        atol=1e-9,
        rtol=1e-10,
    )
