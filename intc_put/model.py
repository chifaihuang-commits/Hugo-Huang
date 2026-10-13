"""Small, dependency-light American put valuation model.

These are *synthetic model marks*, not historical option quotes. The CRR tree
allows early exercise when valuing an American put, assumes constant volatility,
a flat continuously compounded risk-free rate and continuous dividend yield.
It does not model discrete dividends, volatility smiles, bid/ask spreads, trading
halts, liquidity, or an actual option chain. A finite tree introduces numerical
error; increasing ``steps`` is a useful sensitivity check, not market validation.
"""

from __future__ import annotations

import math

import numpy as np


def american_put_price(
    spot: float,
    strike: float,
    time_years: float,
    volatility: float,
    risk_free_rate: float = 0.03,
    dividend_yield: float = 0.0,
    steps: int = 100,
) -> float:
    """Value a put with a Cox-Ross-Rubinstein American exercise tree.

    Time uses ACT/365 calendar years. The zero-volatility boundary considers
    exercise along the deterministic risk-neutral price path. Ordinary inputs
    use a recombining tree and compare continuation with intrinsic value at
    every node. We increase the tree resolution when needed for valid CRR
    probabilities; pathological inputs that require over 20,000 steps fail
    explicitly instead of silently clipping invalid probabilities.
    """
    values = {
        "spot": spot,
        "strike": strike,
        "time_years": time_years,
        "volatility": volatility,
        "risk_free_rate": risk_free_rate,
        "dividend_yield": dividend_yield,
    }
    for name, value in values.items():
        if not isinstance(value, (int, float, np.number)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if time_years < 0 or volatility < 0:
        raise ValueError("time_years and volatility cannot be negative")
    if isinstance(steps, bool) or not isinstance(steps, (int, np.integer)) or steps < 1:
        raise ValueError("steps must be a positive integer")
    intrinsic = max(float(strike - spot), 0.0)
    if time_years == 0:
        return intrinsic

    if volatility == 0:
        exercise_times = np.linspace(0.0, time_years, steps + 1)
        path = spot * np.exp((risk_free_rate - dividend_yield) * exercise_times)
        discounted_payoffs = np.exp(-risk_free_rate * exercise_times) * np.maximum(
            strike - path, 0.0
        )
        return float(np.max(discounted_payoffs))

    # Valid probabilities require d <= exp((r-q)dt) <= u. The ceiling plus
    # one avoids equality/rounding at the threshold, including negative rates.
    minimum_steps = int(math.ceil(time_years * ((risk_free_rate - dividend_yield) / volatility) ** 2)) + 1
    steps = max(int(steps), minimum_steps)
    if steps > 20_000:
        raise ValueError("CRR inputs require more than 20,000 steps; use realistic rates/volatility")
    dt = time_years / steps
    log_up = volatility * math.sqrt(dt)
    up = math.exp(log_up)
    down = math.exp(-log_up)
    growth = math.exp((risk_free_rate - dividend_yield) * dt)
    probability = (growth - down) / (up - down)
    if not 0.0 <= probability <= 1.0:
        raise ValueError("invalid CRR transition probability")
    discount = math.exp(-risk_free_rate * dt)

    node_numbers = np.arange(steps + 1)
    terminal_spots = spot * np.exp((2 * node_numbers - steps) * log_up)
    option_values = np.maximum(strike - terminal_spots, 0.0)
    for level in range(steps - 1, -1, -1):
        continuation = discount * (
            probability * option_values[1:] + (1.0 - probability) * option_values[:-1]
        )
        node_spots = spot * np.exp((2 * node_numbers[: level + 1] - level) * log_up)
        option_values = np.maximum(continuation, strike - node_spots)
    return max(float(option_values[0]), intrinsic, 0.0)
