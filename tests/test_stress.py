import math
from pathlib import Path

import pandas as pd
import pytest

from vnmkt import exchange, market, store
from vnmkt.portfolio import Holding, Portfolio, PortfolioError, from_dict, load, save
from vnmkt.stress import CALL, FORCE, NO_DEBT, SAFE, Assumptions, Scenario, sessions_to_sell, simulate

SAMPLE = Path(__file__).resolve().parents[1] / "data_sample" / "lake"


def liquidity(ratio=1.0):
    pos = pd.DataFrame([{"board": b, "position": p, "sessions": 10, "locked_share": 0.2, "median": ratio}
                        for b in ("HOSE", "HNX", "UPCOM") for p in range(1, 5)])
    return market.FloorLiquidity(pos, pd.Series(dtype=float), since=None)


def quotes(**rows):
    """quotes(AAA=("HOSE", 20000, 1_000_000)) -> bảng giống market.quotes."""
    return pd.DataFrame([{"symbol": s, "board": b, "price": p, "adv": a} for s, (b, p, a) in rows.items()]
                        ).set_index("symbol")


def book(debt=0.0, cash=0.0, **holdings):
    return Portfolio(tuple(Holding(s, q) for s, q in holdings.items()), cash=cash, debt=debt)


def test_no_debt_never_called_and_equity_follows_prices():
    q = quotes(AAA=("HOSE", 20000, 1e6))
    result = simulate(book(AAA=1000), q, liquidity(), Scenario.floor(3))
    prices = [20000]
    for _ in range(3):
        prices.append(exchange.floor_price("HOSE", prices[-1]))
    assert list(result.sessions["status"]) == [NO_DEBT] * 4
    assert list(result.sessions["market_value"]) == [1000 * p for p in prices]
    assert result.first_call is None and result.first_force is None and result.sales.empty


def test_force_sale_restores_call_ratio_when_liquid():
    q = quotes(AAA=("HOSE", 20000, 1e7), BBB=("HNX", 10000, 1e7))
    p = book(debt=1_400_000_000, AAA=100_000, BBB=100_000)  # L = 3 tỷ, R = 0,533
    result = simulate(p, q, liquidity(), Scenario.floor(8), Assumptions(tax_rate=0.001))
    s = result.sessions.set_index("session")
    first = result.first_force
    assert first is not None and s.loc[first, "status"] == FORCE
    after = s.loc[first]
    # Sau khi bán: R về ngưỡng gọi ký quỹ, không bán thừa quá một lô của mã đắt nhất
    assert after["ratio"] >= p.call_ratio - 1e-9
    excess_equity = after["equity"] - p.call_ratio * after["market_value"]
    assert excess_equity <= exchange.LOT * 20000
    assert after["shortfall_value"] == 0
    # Thứ tự trạng thái hợp lệ
    assert set(s["status"]) <= {SAFE, CALL, FORCE}


def test_illiquid_force_sale_leaves_shortfall():
    q = quotes(AAA=("UPCOM", 10000, 1e6))
    p = book(debt=650_000_000, AAA=100_000)                  # R = 0,35 trước kịch bản
    result = simulate(p, q, liquidity(ratio=0.0), Scenario.floor(2))
    s = result.sessions.set_index("session")
    assert result.first_force == 1                           # 1 phiên sàn UPCoM: R ≈ 0,235 < 0,25
    assert s.loc[1, "sold_value"] == 0 and s.loc[1, "shortfall_value"] > 0
    assert result.unsold_value == s.loc[2, "shortfall_value"] > 0


def test_call_grace_triggers_force_after_three_sessions():
    q = quotes(AAA=("HOSE", 10000, 1e7))
    p = book(debt=645_000_000, AAA=100_000)                  # R = 0,355
    result = simulate(p, q, liquidity(), Scenario.uniform(0.01, 4))
    statuses = list(result.sessions["status"])[1:]
    assert statuses[:3] == [CALL, CALL, FORCE]               # R ≈ 0,348; 0,342; 0,335 — vi phạm phiên thứ 3 thì bị bán
    assert result.first_call == 1 and result.first_force == 3


def test_force_sale_cures_breach_count():
    """Sau khi bán về lại ngưỡng gọi ký quỹ, đếm phiên vi phạm bắt đầu lại từ đầu."""
    q = quotes(AAA=("HOSE", 10000, 1e9))
    p = book(debt=645_000_000, AAA=100_000)                  # R = 0,355; sau mỗi phiên: 0,347; 0,339; 0,332
    result = simulate(p, q, liquidity(), Scenario.uniform(0.012, 6))
    statuses = list(result.sessions["status"])[1:]
    assert statuses[:3] == [CALL, CALL, FORCE]
    assert statuses[3] == CALL                                # vi phạm mới: phiên 1 của lần đếm sau, chưa bị bán


def test_equity_accounting_includes_sale_costs():
    q = quotes(AAA=("HOSE", 20000, 1e7))
    p = book(debt=1_000_000_000, AAA=100_000)
    a = Assumptions(tax_rate=0.001, fee_rate=0.0015)
    result = simulate(p, q, liquidity(), Scenario.floor(3), a)
    price_loss = 0.0
    held, last = 100_000, 20000.0
    sales = result.sales.groupby("session")
    for t in range(1, 4):
        new = exchange.floor_price("HOSE", last)
        price_loss += held * (new - last)
        if t in sales.groups:
            held -= int(sales.get_group(t)["shares"].sum())
        last = new
    costs = result.sales["value"].sum() * a.cost_rate
    assert math.isclose(result.equity_change, price_loss - costs, rel_tol=1e-12)


def test_path_scenario_uses_historical_volume():
    q = quotes(AAA=("HOSE", 10000, 1_000_000))
    returns = pd.DataFrame({"AAA": [-0.07, -0.07]})
    volume = pd.DataFrame({"AAA": [0.5, 0.0]})
    p = book(debt=600_000_000, AAA=100_000)
    result = simulate(p, q, liquidity(), Scenario.path(returns, volume, "thử"), Assumptions(participation=1.0))
    sold = result.sales.groupby("session")["shares"].sum().to_dict()
    assert sold.get(1, 0) <= 500_000 and sold.get(2, 0) == 0


def test_sessions_to_sell():
    q = quotes(AAA=("HOSE", 10000, 100_000))
    days = sessions_to_sell(book(AAA=25_000), q, liquidity(0.5))
    assert days == {"AAA": 5}                                # 10% × 100.000 × 0,5 = 5.000 cổ/phiên
    assert sessions_to_sell(book(AAA=1), q, liquidity(0.0)) == {"AAA": math.inf}


def test_portfolio_validation_and_round_trip(tmp_path):
    p = from_dict({"holdings": [{"symbol": " hpg ", "quantity": "1000", "cost_price": 25000}], "debt": 1e7})
    assert p.symbols == ["HPG"]
    save(p, tmp_path / "p.json")
    assert load(tmp_path / "p.json") == p
    for bad in ({"holdings": [{"symbol": "HPG", "quantity": 10.5}]},
                {"holdings": [{"symbol": "HPG", "quantity": 1}, {"symbol": "hpg", "quantity": 2}]},
                {"holdings": [{"symbol": "../x", "quantity": 1}]},
                {"holdings": [{"symbol": "HPG", "quantity": "nan"}]},
                {"debt": -1}, {"call_ratio": 0.2, "force_ratio": 0.25}):
        with pytest.raises(PortfolioError):
            from_dict(bad)


@pytest.mark.skipif(not (SAMPLE / "daily").exists(), reason="chưa có data_sample/lake")
def test_market_queries_on_sample():
    with store.Warehouse(SAMPLE, read_only=True) as wh:
        liq = market.floor_liquidity(wh)
        hose = liq.by_position[liq.by_position["board"] == "HOSE"].set_index("position")
        assert hose.loc[1, "sessions"] > 100 and hose.loc[1, "median"] > 0
        start, end = market.drawdown_window(wh, 2022)
        assert start < end and start.year == end.year == 2022
        returns, volume, replaced = market.historical_path(wh, ["HPG", "VCB"], start, end)
        assert len(returns) > 50 and not returns.isna().any().any() and replaced == []
        q = market.quotes(wh, ["HPG"])
        assert q.loc["HPG", "price"] > 0 and q.loc["HPG", "adv"] > 0


def test_account_already_below_call_counts_today_as_first_breach():
    q = quotes(AAA=("HOSE", 10000, 1e9))
    p = book(debt=700_000_000, AAA=100_000)                  # R = 0,30 ngay hiện tại
    result = simulate(p, q, liquidity(), Scenario.uniform(0.001, 3))
    statuses = list(result.sessions["status"])
    assert result.first_call == 0 and statuses[0] == CALL
    assert statuses[1:3] == [CALL, FORCE]                    # hôm nay + 2 phiên nữa = 3 phiên vi phạm liên tiếp


def test_uniform_with_per_symbol_drops():
    q = quotes(AAA=("HOSE", 10000, 1e7), BBB=("HOSE", 10000, 1e7))
    r = simulate(book(AAA=1000, BBB=1000), q, liquidity(), Scenario.uniform(0.01, 2, {"AAA": 0.05, "BBB": 0.0}))
    end = r.sessions.iloc[-1]["market_value"]
    assert math.isclose(end, 1000 * 10000 * 0.95 ** 2 + 1000 * 10000)    # BBB đứng giá, AAA giảm 5% mỗi phiên
