"""Independent pricing checks, including a hand-calculated binomial tree."""

import math

import pytest

from intc_put.model import american_put_price


def european_put(spot, strike, maturity, volatility, rate):
    """Black-Scholes benchmark; independent of the implementation's tree."""
    normal_cdf = lambda x: 0.5 * math.erfc(-x / math.sqrt(2.0))
    d1 = (
        math.log(spot / strike) + (rate + volatility**2 / 2.0) * maturity
    ) / (volatility * math.sqrt(maturity))
    d2 = d1 - volatility * math.sqrt(maturity)
    return (
        strike * math.exp(-rate * maturity) * normal_cdf(-d2)
        - spot * normal_cdf(-d1)
    )


def test_one_step_tree_matches_hand_calculation():
    # u=2, d=1/2, r=0 => risk-neutral up probability 1/3.
    # Terminal payoffs are 0 and 50, so today's value is 100/3.
    price = american_put_price(100, 100, 1.0, math.log(2), 0.0, steps=1)
    assert price == pytest.approx(100 / 3, abs=1e-10)


@pytest.mark.parametrize("spot,expected", [(80, 20), (100, 0), (120, 0)])
def test_expiration_value_is_intrinsic(spot, expected):
    assert american_put_price(spot, 100, 0.0, 0.2) == pytest.approx(expected)


def test_zero_rate_tree_converges_to_european_value():
    # With zero rates and dividends, early put exercise has no advantage.
    expected = european_put(100, 100, 0.5, 0.3, 0.0)
    actual = american_put_price(100, 100, 0.5, 0.3, 0.0, steps=800)
    assert actual == pytest.approx(expected, abs=0.02)


def test_deep_itm_american_put_can_be_exercised_immediately():
    price = american_put_price(1, 100, 1.0, 0.1, 0.05, steps=100)
    assert price == pytest.approx(99.0, abs=1e-8)


@pytest.mark.parametrize("spot", [50, 80, 100, 120, 180])
def test_price_respects_american_put_bounds(spot):
    price = american_put_price(spot, 100, 0.5, 0.3, 0.03)
    assert max(100 - spot, 0) - 1e-9 <= price <= 100 + 1e-9


def test_lower_spot_and_higher_volatility_increase_put_value():
    base = american_put_price(100, 100, 0.5, 0.2)
    assert american_put_price(90, 100, 0.5, 0.2) > base
    assert american_put_price(100, 100, 0.5, 0.4) > base


def test_american_put_is_not_cheaper_than_european_benchmark():
    expected = european_put(80, 100, 0.5, 0.3, 0.05)
    actual = american_put_price(80, 100, 0.5, 0.3, 0.05, steps=800)
    assert actual >= expected - 0.02
