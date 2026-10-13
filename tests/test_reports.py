"""Year-boundary and full-period return checks with hand-calculated answers."""

import pandas as pd
import pytest

from intc_put.reports import annual_statistics, performance_summary


def equity_path():
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(
                ["2019-12-31", "2020-01-02", "2020-12-31", "2021-01-04", "2021-12-31"]
            ),
            "equity": [100.0, 90.0, 120.0, 60.0, 150.0],
            "cash_secured_reserve": [0.0] * 5,
            "indicative_margin": [0.0] * 5,
            "shares": [0] * 5,
            "option_liability": [0.0] * 5,
        }
    )


def test_annual_pnl_uses_previous_year_close_as_opening_capital():
    annual = annual_statistics(equity_path(), 100).set_index("year")
    assert annual.loc[2020, "start_equity"] == pytest.approx(100)
    assert annual.loc[2020, "pnl"] == pytest.approx(20)
    assert annual.loc[2020, "return_pct"] == pytest.approx(20)
    assert annual.loc[2021, "start_equity"] == pytest.approx(120)
    assert annual.loc[2021, "pnl"] == pytest.approx(30)
    assert annual.loc[2021, "return_pct"] == pytest.approx(25)
    assert annual["pnl"].sum() == pytest.approx(50)


def test_annual_drawdown_includes_loss_on_first_session_of_year():
    annual = annual_statistics(equity_path(), 100).set_index("year")
    assert annual.loc[2020, "max_drawdown_pct"] == pytest.approx(10)
    assert annual.loc[2021, "max_drawdown_pct"] == pytest.approx(50)


def test_full_period_drawdown_and_pnl_have_known_values():
    summary = performance_summary(equity_path(), 100)
    assert summary["max_drawdown_pct"] == pytest.approx(50)
    assert summary["final_equity"] == pytest.approx(150)
    assert summary["net_pnl"] == pytest.approx(50)
    assert summary["total_return_pct"] == pytest.approx(50)


def test_initial_capital_is_included_in_drawdown_peak():
    data = equity_path().iloc[:2].copy()
    data["equity"] = [90.0, 80.0]
    summary = performance_summary(data, 100)
    assert summary["max_drawdown_pct"] == pytest.approx(20)
