"""Cash-secured short-put research simulation with an explicit cash ledger.

Core assumptions / limitations (read before interpreting results):
* Daily closes must represent executable, consistently scaled stock prices.
  No actual historical option quotes, corporate-action option deliverables,
  exchange holiday calendar, intraday prices, or option-chain strikes are used.
* A signal uses yesterday's close and volatility estimated only through the
  preceding session. Strike is floor(yesterday_close * (1-otm_pct)) in $1 steps.
  Execution and valuation at today's close are a market-at-close approximation;
  there is no guarantee a real order would fill at the model-derived premium.
* Premiums/marks come from an American CRR model. Assignment is simulated only
  at expiration, although American puts can be assigned earlier in reality.
  Ignoring early assignment, dividend dates and exercise instructions is a
  material limitation. We exercise any positive terminal intrinsic value;
  actual broker/OCC thresholds, pin risk and contrary instructions can differ.
* Expiration is the first supplied stock session on or after a target calendar
  date. This is a hypothetical maturity, not a listed exchange option series.
  We know the supplied session calendar and do not open a put whose target
  maturity lies beyond the sample. Terminal assigned stock is retained/marked.
* One fixed-size position is held at a time, without reinvestment-based sizing,
  rolling, stop-losses or leverage. Cash must cover strike * shares BEFORE the
  premium is collected. Actual collateral is cash, earns no interest and is not
  a second expense. A separate Reg-T-style requirement is only a diagnostic.
* Costs are a per-contract opening commission, a dollar-per-share option sale
  discount, and stock-sale commission/slippage. There are no expiration or
  assignment fees, tax, financing interest, exchange fees, or cash dividends.
  ``dividend_yield`` is solely a model input, not a simulated cash payment.
* Physical assignment pays strike and receives shares. We recognize net option
  premium at expiration, keep shares at strike cost basis, and realize their
  price loss only on sale. This accounting convention avoids counting the
  same assignment loss twice; it is not tax reporting advice.
* Short puts have capped upside and potentially large downside (stock to zero).
  Volatility jumps, gap risk, model error, illiquidity and concentration can
  overwhelm premiums. Neither cash collateral nor a small observed drawdown
  makes the strategy safe. A single stock and parameter set invite selection
  and overfitting bias; historical simulation is not a performance promise.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from .model import american_put_price


@dataclass(frozen=True)
class Config:
    initial_cash: float = 10_000.0
    contracts: int = 1
    multiplier: int = 100
    otm_pct: float = 0.05
    tenor_days: int = 30
    vol_window: int = 20
    vol_floor: float = 0.10
    vol_cap: float = 1.50
    risk_free_rate: float = 0.03
    dividend_yield: float = 0.0
    option_commission: float = 0.65
    option_slippage: float = 0.02
    stock_slippage_bps: float = 5.0
    stock_commission: float = 0.0
    tree_steps: int = 100
    assignment_hold_days: int = 5

    def __post_init__(self) -> None:
        for name in ("contracts", "multiplier", "tenor_days", "vol_window", "tree_steps"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.vol_window < 2:
            raise ValueError("vol_window must be at least 2 for sample volatility")
        if (
            isinstance(self.assignment_hold_days, bool)
            or not isinstance(self.assignment_hold_days, (int, np.integer))
            or self.assignment_hold_days < 0
        ):
            raise ValueError("assignment_hold_days must be a nonnegative integer")
        for name in (
            "initial_cash", "otm_pct", "vol_floor", "vol_cap", "risk_free_rate",
            "dividend_yield", "option_commission", "option_slippage",
            "stock_slippage_bps", "stock_commission",
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float, np.number)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not 0 <= self.otm_pct < 1:
            raise ValueError("otm_pct must be in [0, 1)")
        if not 0 < self.vol_floor <= self.vol_cap:
            raise ValueError("require 0 < vol_floor <= vol_cap")
        for name in ("dividend_yield", "option_commission", "option_slippage", "stock_slippage_bps", "stock_commission"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.stock_slippage_bps >= 10_000:
            raise ValueError("stock_slippage_bps must be less than 10,000")


@dataclass
class BacktestResult:
    daily: pd.DataFrame
    trades: pd.DataFrame
    events: pd.DataFrame
    config: Config


TRADE_COLUMNS = [
    "trade_id", "kind", "entry_date", "exit_date", "strike", "contracts",
    "shares", "entry_price", "exit_price", "gross_premium", "fees",
    "realized_pnl", "outcome", "holding_days", "target_expiry", "expiry_date",
]
EVENT_COLUMNS = [
    "Date", "event", "trade_id", "spot", "strike", "contracts", "shares",
    "price", "cash_flow", "fees", "note",
]


def _validated_prices(prices: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(prices, pd.DataFrame) or not {"Date", "Close"}.issubset(prices.columns):
        raise ValueError("prices must be a DataFrame with Date and Close columns")
    if prices.empty:
        raise ValueError("prices cannot be empty")
    result = prices.loc[:, ["Date", "Close"]].copy()
    try:
        result["Date"] = pd.to_datetime(result["Date"], errors="raise")
        # Daily rows are normalized; two intraday observations on the same date
        # are rejected instead of accidentally becoming two trading sessions.
        if result["Date"].dt.tz is not None:
            result["Date"] = result["Date"].dt.tz_localize(None)
        result["Date"] = result["Date"].dt.normalize()
        result["Close"] = pd.to_numeric(result["Close"], errors="raise").astype(float)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Date must contain valid daily dates and Close numeric prices") from exc
    if result["Date"].isna().any() or result["Date"].duplicated().any():
        raise ValueError("Date values must be nonmissing and unique")
    if not np.isfinite(result["Close"]).all() or (result["Close"] <= 0).any():
        raise ValueError("Close values must be finite and positive")
    return result.sort_values("Date").reset_index(drop=True)


def run_backtest(
    prices: pd.DataFrame,
    config: Config | None = None,
    entry_mode: str = "recurring",
    fixed_vol: float | None = None,
) -> BacktestResult:
    """Simulate one cash-secured put at a time, returning auditable ledgers.

    ``fixed_vol`` supports controlled deterministic scenarios; normal research
    uses lagged rolling sample standard deviation of log returns, annualized by
    sqrt(252) and clipped to the configured range. It does not use future prices.
    ``entry_mode='once'`` allows one successfully funded opening in the sample.

    Daily ``realized_pnl`` is cumulative, ``unrealized_pnl`` is the residual to
    total equity P&L, and ``drawdown`` is a nonpositive fraction. Equity is cash
    plus shares at close minus the short option's model liability. Receiving
    premium increases cash, but is not an immediate profit. ``margin`` and
    ``cash_secured_reserve`` denote the same actual reserved collateral, while
    ``indicative_margin`` is a separate unlevered Reg-T-style diagnostic.
    """
    cfg = config if config is not None else Config()
    if not isinstance(cfg, Config):
        raise ValueError("config must be a Config instance")
    if entry_mode not in {"recurring", "once"}:
        raise ValueError("entry_mode must be 'recurring' or 'once'")
    if fixed_vol is not None and (
        not isinstance(fixed_vol, (int, float, np.number))
        or not math.isfinite(fixed_vol)
        or fixed_vol <= 0
    ):
        raise ValueError("fixed_vol must be finite and positive")

    data = _validated_prices(prices)
    if fixed_vol is None:
        lagged_vol = np.log(data["Close"]).diff().rolling(cfg.vol_window).std(ddof=1).shift(1) * math.sqrt(252)
        lagged_vol = lagged_vol.clip(lower=cfg.vol_floor, upper=cfg.vol_cap)
    else:
        # Explicit scenario override is not clipped, so a scenario can stress
        # volatility outside the historical strategy's configured estimate cap.
        lagged_vol = pd.Series(float(fixed_vol), index=data.index)

    cash = float(cfg.initial_cash)
    shares = 0
    stock_basis = 0.0
    stock_entry_date = None
    stock_sale_index = None
    stock_trade_id = None
    position = None
    realized_pnl = 0.0
    peak_equity = float(cfg.initial_cash)
    openings = 0
    units = cfg.contracts * cfg.multiplier
    daily_rows: list[dict] = []
    trades: list[dict] = []
    events: list[dict] = []
    dates = data["Date"]

    def event(date, action, spot, *, trade_id=None, strike=None, quantity=0,
              price=None, cash_flow=0.0, fees=0.0, note=""):
        events.append({
            "Date": date, "event": action, "trade_id": trade_id, "spot": spot,
            "strike": strike,
            "contracts": cfg.contracts if quantity and not action.startswith("stock_") else 0,
            "shares": quantity, "price": price, "cash_flow": cash_flow,
            "fees": fees, "note": note,
        })

    for index, row in data.iterrows():
        date, spot = row["Date"], float(row["Close"])
        vol = float(lagged_vol.iloc[index])
        closed_today = False

        if position is not None and index >= position["expiry_index"]:
            p = position
            is_assigned = spot < p["strike"]
            outcome = "assigned" if is_assigned else "expired"
            # Recognize premium once. Physical assignment loss stays in the
            # acquired shares' mark-to-market until those shares are sold.
            realized_pnl += p["net_premium"]
            trades.append({
                "trade_id": p["trade_id"], "kind": "put", "entry_date": p["entry_date"],
                "exit_date": date, "strike": p["strike"], "contracts": cfg.contracts,
                "shares": units, "entry_price": p["fill_price"],
                "exit_price": max(p["strike"] - spot, 0.0),
                "gross_premium": p["gross_premium"], "fees": p["fees"],
                "realized_pnl": p["net_premium"], "outcome": outcome,
                "holding_days": (date - p["entry_date"]).days,
                "target_expiry": p["target_expiry"], "expiry_date": p["expiry_date"],
            })
            if is_assigned:
                cash -= p["strike"] * units
                shares = units
                stock_basis = p["strike"]
                stock_entry_date = date
                stock_sale_index = index + cfg.assignment_hold_days
                stock_trade_id = p["trade_id"]
                event(date, "assigned", spot, trade_id=p["trade_id"], strike=p["strike"],
                      quantity=units, price=p["strike"], cash_flow=-p["strike"] * units,
                      note="Physical assignment: strike cash paid; shares received.")
            else:
                event(date, "expired", spot, trade_id=p["trade_id"], strike=p["strike"],
                      quantity=units, price=0.0, note="Put expires worthless; no closing fee.")
            position = None
            closed_today = True

        if shares and stock_sale_index is not None and index >= stock_sale_index:
            fill = spot * (1.0 - cfg.stock_slippage_bps / 10_000.0)
            net_proceeds = shares * fill - cfg.stock_commission
            stock_pnl = net_proceeds - shares * stock_basis
            cash += net_proceeds
            realized_pnl += stock_pnl
            trades.append({
                "trade_id": stock_trade_id, "kind": "stock", "entry_date": stock_entry_date,
                "exit_date": date, "strike": stock_basis, "contracts": 0, "shares": shares,
                "entry_price": stock_basis, "exit_price": fill, "gross_premium": 0.0,
                "fees": cfg.stock_commission, "realized_pnl": stock_pnl,
                "outcome": "stock_sold", "holding_days": (date - stock_entry_date).days,
                "target_expiry": pd.NaT, "expiry_date": pd.NaT,
            })
            event(date, "stock_sold", spot, trade_id=stock_trade_id, strike=stock_basis,
                  quantity=shares, price=fill, cash_flow=net_proceeds, fees=cfg.stock_commission,
                  note=f"Sold after {cfg.assignment_hold_days} subsequent supplied sessions.")
            shares = 0
            stock_basis = 0.0
            stock_sale_index = None
            stock_entry_date = None
            stock_trade_id = None
            closed_today = True

        can_enter = (
            position is None and shares == 0 and not closed_today and index > 0
            and math.isfinite(vol) and (entry_mode == "recurring" or openings == 0)
        )
        if can_enter:
            target_expiry = date + pd.Timedelta(days=cfg.tenor_days)
            expiry_index = int(dates.searchsorted(target_expiry, side="left"))
            # Sample endpoint is explicit. We do not fabricate future sessions,
            # force early option settlement, or sell retained stock at a phantom
            # close merely to make the final ledger appear fully realized.
            if expiry_index < len(data):
                strike = float(math.floor(float(data["Close"].iloc[index - 1]) * (1.0 - cfg.otm_pct)))
                collateral = strike * units
                if strike <= 0:
                    event(date, "entry_skipped", spot, note="$1 strike grid produces a nonpositive strike.")
                elif cash + 1e-9 < collateral:
                    event(date, "entry_skipped", spot, strike=strike,
                          note="Available pre-premium cash does not fully secure assignment.")
                else:
                    expiry_date = dates.iloc[expiry_index]
                    years = (expiry_date - date).days / 365.0
                    model_mark = american_put_price(spot, strike, years, vol,
                                                   cfg.risk_free_rate, cfg.dividend_yield, cfg.tree_steps)
                    fill = max(model_mark - cfg.option_slippage, 0.0)
                    gross_premium = fill * units
                    fees = cfg.option_commission * cfg.contracts
                    net_premium = gross_premium - fees
                    if net_premium > 0:
                        openings += 1
                        cash += net_premium
                        position = {
                            "trade_id": openings, "entry_date": date, "strike": strike,
                            "target_expiry": target_expiry, "expiry_date": expiry_date,
                            "expiry_index": expiry_index, "fill_price": fill,
                            "gross_premium": gross_premium, "net_premium": net_premium,
                            "fees": fees,
                        }
                        event(date, "put_sold", spot, trade_id=openings, strike=strike,
                              quantity=units, price=fill, cash_flow=net_premium, fees=fees,
                              note="Model sale credit less commission; premium is not immediate profit.")
                    else:
                        event(date, "entry_skipped", spot, strike=strike,
                              note="Model premium does not cover configured opening costs.")

        option_liability = 0.0
        reserve = 0.0
        indicative_margin = 0.0
        put_contracts = 0
        put_strike = np.nan
        put_expiry = pd.NaT
        if position is not None:
            p = position
            years = max((p["expiry_date"] - date).days, 0) / 365.0
            mark = american_put_price(spot, p["strike"], years, vol,
                                      cfg.risk_free_rate, cfg.dividend_yield, cfg.tree_steps)
            option_liability = mark * units
            reserve = p["strike"] * units
            # Simplified standard equity uncovered-put maintenance diagnostic:
            # option market value + max(20% underlying - OTM amount, 10% strike).
            # A broker can impose higher/other requirements. This account NEVER
            # uses this smaller figure to authorize leverage or release cash.
            indicative_margin = option_liability + max(
                0.20 * spot - max(spot - p["strike"], 0.0), 0.10 * p["strike"]
            ) * units
            put_contracts, put_strike, put_expiry = cfg.contracts, p["strike"], p["expiry_date"]

        stock_value = shares * spot
        equity = cash + stock_value - option_liability
        peak_equity = max(peak_equity, equity)
        unrealized_pnl = equity - cfg.initial_cash - realized_pnl
        daily_rows.append({
            "Date": date, "Close": spot, "cash": cash, "shares": shares,
            "stock_value": stock_value, "stock_cost_basis": shares * stock_basis,
            "option_liability": option_liability, "equity": equity,
            "realized_pnl": realized_pnl, "unrealized_pnl": unrealized_pnl,
            "cash_secured_reserve": reserve, "margin": reserve,
            "indicative_margin": indicative_margin, "buying_power": cash - reserve,
            "volatility": vol, "put_contracts": put_contracts,
            "put_strike": put_strike, "put_expiry": put_expiry,
            "peak_equity": peak_equity, "drawdown": equity / peak_equity - 1.0,
        })

    if shares:
        event(dates.iloc[-1], "stock_retained", float(data["Close"].iloc[-1]),
              trade_id=stock_trade_id, strike=stock_basis, quantity=shares,
              note="Sample ended before the planned stock sale; position remains marked, not liquidated.")
    return BacktestResult(
        daily=pd.DataFrame(daily_rows),
        trades=pd.DataFrame(trades, columns=TRADE_COLUMNS),
        events=pd.DataFrame(events, columns=EVENT_COLUMNS),
        config=cfg,
    )
