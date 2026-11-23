import math

import pandas as pd
import pytest

from vnmkt import market
from vnmkt.portfolio import Holding, Portfolio, PortfolioError
from vnmkt.whatif import BUY, SELL, Order, apply, evaluate


def liquidity():
    pos = pd.DataFrame([{"board": b, "position": p, "sessions": 10, "locked_share": 0.2, "median": 1.0}
                        for b in ("HOSE", "HNX", "UPCOM") for p in range(1, 5)])
    return market.FloorLiquidity(pos, pd.Series(dtype=float), since=None)


QUOTES = pd.DataFrame([{"symbol": "AAA", "board": "HOSE", "price": 20000.0, "adv": 1e7},
                       {"symbol": "BBB", "board": "HOSE", "price": 10000.0, "adv": 1e7}]).set_index("symbol")
BASE = Portfolio((Holding("AAA", 50_000, 18_000.0),), cash=100_000_000, debt=400_000_000)  # L = 1 tỷ


def test_buy_uses_cash_first_then_margin_and_averages_cost():
    after, value, costs = apply(BASE, Order(BUY, "AAA", 10_000, 20_000.0), fee_rate=0.001)
    assert value == 200_000_000 and math.isclose(costs, 200_000)
    assert after.cash == 0 and math.isclose(after.debt, 400_000_000 + 200_200_000 - 100_000_000)
    aaa = {h.symbol: h for h in after.holdings}["AAA"]
    assert aaa.quantity == 60_000
    assert math.isclose(aaa.cost_price, (50_000 * 18_000 + 200_200_000) / 60_000)


def test_sell_repays_debt_and_removes_empty_holding():
    after, value, costs = apply(BASE, Order(SELL, "AAA", 50_000, 20_000.0), fee_rate=0.0, tax_rate=0.001)
    assert after.holdings == () and math.isclose(costs, 1_000_000)
    assert after.debt == 0 and math.isclose(after.cash, 100_000_000 + 1_000_000_000 - 1_000_000 - 400_000_000)


def test_invalid_orders_are_rejected():
    for order in (Order(SELL, "BBB", 100, 10_000.0), Order(SELL, "AAA", 60_000, 20_000.0),
                  Order(BUY, "AAA", 0, 20_000.0), Order(BUY, "AAA", 100, 0.0), Order("short", "AAA", 100, 1.0)):
        with pytest.raises(PortfolioError):
            apply(BASE, order)


def test_evaluate_compares_before_after():
    r = evaluate(BASE, Order(BUY, "BBB", 30_000, 10_000.0), QUOTES, liquidity())
    assert math.isclose(r.before.ratio, (1e9 - 3e8) / 1e9)
    assert math.isclose(r.after.market_value, 1.3e9) and math.isclose(r.after.net_debt, 6e8)
    assert r.after.ratio < r.before.ratio
    assert r.after.call_gap < r.before.call_gap                       # vay thêm thì chịu được ít giảm giá hơn
    assert math.isclose(r.before.call_gap, 1e9 - 3e8 / 0.65)
    assert set(r.after.weights) == {"AAA", "BBB"} and math.isclose(sum(r.after.weights.values()), 1)
    assert r.before.floor_sessions is None                            # R = 70%: cần hơn 10 phiên sàn HOSE
    assert r.after.floor_sessions == 5                                # R = 53,8%: 0,93^5 × 1,3 tỷ < 600 tr / 0,65
    assert r.cash_change_t2 == -300_000_000


def test_evaluate_warns_about_band_lot_and_call():
    r = evaluate(BASE, Order(BUY, "BBB", 150_050, 12_000.0), QUOTES, liquidity())
    text = " ".join(r.warnings)
    assert "biên độ" in text and "lô lẻ" in text and "dưới ngưỡng gọi ký quỹ" in text


def test_no_debt_has_no_call_gap():
    r = evaluate(Portfolio((Holding("AAA", 100, None),), cash=1e9), Order(SELL, "AAA", 100, 20_000.0),
                 QUOTES, liquidity())
    assert r.after.call_gap is None and r.after.floor_sessions is None and math.isinf(r.after.ratio)
