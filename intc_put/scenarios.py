"""Deterministic synthetic scenarios, separate from historical INTC data.

This is a stress demonstration, not a forecast or calibrated probability.
Both paths share an initial $50 stock price and 30% volatility. The crash
path gaps down 40%, remains depressed through expiry, then loses more while
assigned stock is held. A crash alone does not force assignment before expiry:
this simulator assigns only in-the-money options on the modeled expiry date.
"""

import numpy as np
import pandas as pd

from .engine import Config, run_backtest


def run_scenarios():
    # Reference scenarios deliberately retain default parameters. Historical
    # CLI overrides must not turn the promised crash/assignment demonstration
    # into a no-trade case or let a short tenor expire before the crash.
    config = Config()
    # Business-day labels are synthetic; they are not an exchange calendar.
    count = max(55, config.tenor_days + config.assignment_hold_days + 15)
    dates = pd.bdate_range("2025-01-06", periods=count)
    normal = np.r_[50., np.linspace(50., 52., count - 1)]
    crash = normal.copy()
    crash[12:] = np.linspace(30., 27., count - 12)
    return {
        name: run_backtest(pd.DataFrame({"Date": dates, "Close": prices}),
                           config, entry_mode="once", fixed_vol=.30)
        for name, prices in [("normal_expiration", normal), ("crash_assignment", crash)]
    }
