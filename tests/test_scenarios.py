"""Reference examples must demonstrate their promised economic outcomes."""

from intc_put.scenarios import run_scenarios


def test_reference_scenarios_have_same_entry_but_different_settlement():
    runs = run_scenarios()
    normal, crash = runs["normal_expiration"], runs["crash_assignment"]
    assert normal.events.iloc[0]["cash_flow"] == crash.events.iloc[0]["cash_flow"]
    assert normal.trades.iloc[0]["outcome"] == "expired"
    assert crash.trades.iloc[0]["outcome"] == "assigned"
    assert "stock_sold" in set(crash.events["event"])
    assert normal.daily["equity"].iloc[-1] > normal.config.initial_cash
    assert crash.daily["equity"].iloc[-1] < crash.config.initial_cash
    assert crash.daily["shares"].max() == 100
    assert crash.daily["shares"].iloc[-1] == 0
