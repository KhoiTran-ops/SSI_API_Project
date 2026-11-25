import math
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from vnmkt import performance as pf
from vnmkt import store
from vnmkt.performance import BUY, DEPOSIT, SELL, WITHDRAW, Journal, Transaction
from vnmkt.portfolio import PortfolioError

_ROOT = Path(__file__).resolve().parents[1]
# Kho thật nếu máy có, không thì bộ mẫu đi kèm repo (cùng dữ liệu SSI, HOSE từ 2022) để kiểm thử chạy được sau khi clone.
LAKE = next(p for p in (_ROOT / "data" / "lake", _ROOT / "data_sample" / "lake") if (p / "corporate_actions").exists())


def frame(prices: dict[str, list[float]], start=date(2026, 1, 5), actions=None, index=None):
    n = len(next(iter(prices.values())))
    days = [start + timedelta(days=i) for i in range(n)]
    px = pd.DataFrame(prices, index=days)
    idx = pd.Series(index or [1000.0] * n, index=days)
    acts = pd.DataFrame(actions or [], columns=["symbol", "exright_date", "ratio", "title"])
    return pf.MarketFrame(px, idx, acts), days


def test_twr_ignores_deposit_timing_and_irr_matches_simple_case():
    m, d = frame({"AAA": [10_000, 11_000, 11_000, 12_100]})
    base = [Transaction(d[0], DEPOSIT, amount=1_000_000), Transaction(d[0], BUY, "AAA", 100, 10_000)]
    r1 = pf.evaluate(Journal(tuple(base)), m)
    # Nạp thêm giữa kỳ rồi mua tiếp ở cùng giá đóng cửa: TWR không đổi
    more = base + [Transaction(d[2], DEPOSIT, amount=1_100_000), Transaction(d[2], BUY, "AAA", 100, 11_000)]
    r2 = pf.evaluate(Journal(tuple(more)), m)
    assert math.isclose(r1.twr, 0.21, rel_tol=1e-9) and math.isclose(r2.twr, r1.twr, rel_tol=1e-9)
    assert r1.costs == 0 and r1.pgr is None
    assert math.isclose(r1.hold_return, r1.twr, rel_tol=1e-9)   # không giao dịch thêm: bằng TWR


def test_irr_one_year():
    flows = [(date(2025, 1, 1), 100.0)]
    assert math.isclose(pf._irr(flows, date(2026, 1, 1), 110.0), 0.10, abs_tol=1e-6)


def test_bonus_shares_through_adjustment_factor():
    # Cổ phiếu thưởng 100% ngày d2 (ratio 0,5): giá thật 20.000 -> 10.000, giá điều chỉnh liền mạch 10.000
    m, d = frame({"AAA": [10_000, 10_000, 10_000]}, actions=[{"symbol": "AAA", "exright_date": date(2026, 1, 6),
                                                              "ratio": 0.5, "title": "AAA - Phát hành cổ phiếu thưởng tỷ lệ 100%"}])
    j = Journal((Transaction(d[0], DEPOSIT, amount=2_000_000), Transaction(d[0], BUY, "AAA", 100, 20_000)))
    r = pf.evaluate(j, m)
    assert math.isclose(r.daily["value"].iloc[-1], 2_000_000)   # 200 cổ × 10.000: không lãi, không lỗ
    assert pf.holdings(j, m.actions) == {"AAA": 200}


def test_costs_realized_gain_and_pgr_plr():
    m, d = frame({"AAA": [10_000, 12_000, 12_000], "BBB": [10_000, 8_000, 9_000], "CCC": [10_000, 11_000, 11_000]})
    j = Journal((
        Transaction(d[0], DEPOSIT, amount=3_000_000),
        Transaction(d[0], BUY, "AAA", 100, 10_000, fee=1_000),
        Transaction(d[0], BUY, "BBB", 100, 10_000), Transaction(d[0], BUY, "CCC", 100, 10_000),
        Transaction(d[1], SELL, "AAA", 100, 12_000, tax=1_200),
    ))
    r = pf.evaluate(j, m)
    assert r.costs == 2_200
    row = r.realized.iloc[0]
    assert math.isclose(row["gain"], 1_200_000 - 1_200 - 1_001_000)
    # Ngày bán: AAA lãi thực hiện; CCC lãi trên giấy; BBB lỗ trên giấy -> PGR = 1/2, PLR = 0/1
    assert r.pgr == 0.5 and r.plr == 0.0 and r.disposition == 0.5


def test_withdraw_and_invalid_journal():
    m, d = frame({"AAA": [10_000, 10_000]})
    j = Journal((Transaction(d[0], DEPOSIT, amount=1_000_000), Transaction(d[1], WITHDRAW, amount=400_000)))
    r = pf.evaluate(j, m)
    assert math.isclose(r.twr, 0.0, abs_tol=1e-12) and math.isclose(r.daily["value"].iloc[-1], 600_000)
    with pytest.raises(PortfolioError):
        pf.evaluate(Journal((Transaction(d[0], SELL, "AAA", 100, 10_000),)), m)
    with pytest.raises(PortfolioError):
        pf.from_dict({"transactions": [{"date": "2026-13-01", "kind": "deposit", "amount": 1}]})


def test_round_trip_fills_sell_tax(tmp_path):
    j = pf.from_dict({"transactions": [{"date": "2026-01-05", "kind": "sell", "symbol": "aaa", "quantity": 100,
                                        "price": 10_000}]})
    assert j.transactions[0].symbol == "AAA" and j.transactions[0].tax == pytest.approx(1_000)
    pf.save(j, tmp_path / "j.json")
    assert pf.load(tmp_path / "j.json") == j


def test_adjustment_factor_matches_ssi_price_changes():
    """F(d) tính từ sự kiện quyền phải bằng (thay đổi giá điều chỉnh) / (thay đổi giá gốc) mà SSI trả về."""
    with store.Warehouse(LAKE, read_only=True) as wh:
        actions = wh.query("SELECT symbol, exright_date, ratio FROM corporate_actions WHERE symbol = 'FPT'")
        obs = wh.query("""
            WITH d AS (SELECT date, change, close - lag(close) OVER (ORDER BY date) AS adj FROM daily WHERE symbol='FPT')
            SELECT date, adj / change AS f FROM d WHERE abs(change) >= 1000 AND date BETWEEN '2025-01-01' AND '2026-11-13'
        """)
    obs["date"] = pd.to_datetime(obs["date"]).dt.date
    # Phiên giao dịch không hưởng quyền: giá gốc so với tham chiếu đã điều chỉnh nên không dùng để đối chiếu
    ex = set(pd.to_datetime(actions["exright_date"]).dt.date)
    obs = obs[~obs["date"].isin(ex)]
    f = pf.adjustment_factors(actions, "FPT", list(obs["date"]))
    err = (obs.set_index("date")["f"] - f).abs()
    assert (err < 0.01).mean() > 0.95


def test_share_multiplier_and_same_day_events():
    assert pf.share_multiplier("VCB - Phát hành cổ phiếu trả cổ tức tỷ lệ 49,5%") == pytest.approx(1.495)
    assert pf.share_multiplier("FPT - Phát hành cổ phiếu thưởng tỷ lệ 15%") == pytest.approx(1.15)
    assert pf.share_multiplier("FPT - Trả cổ tức Đợt 2 năm 2025 bằng tiền 1000 đồng/cổ phiếu") == 1.0
    assert pf.share_multiplier("SSI - Phát hành cổ phiếu cho cổ đông hiện hữu tỷ lệ 5 : 1 giá 15.000 đồng/cp") == 1.0
    # Hai sự kiện cùng ngày, SSI ghi cùng hệ số tổng cho cả hai dòng: chỉ tính một lần
    acts = pd.DataFrame([{"symbol": "SSI", "exright_date": date(2026, 8, 17), "ratio": 0.7993, "title": "thưởng"},
                         {"symbol": "SSI", "exright_date": date(2026, 8, 17), "ratio": 0.7993, "title": "tiền"}])
    assert pf.adjustment_factors(acts, "SSI", [date(2026, 1, 1)]).iloc[0] == pytest.approx(0.7993)


def test_trade_without_price_is_rejected():
    m, d = frame({"AAA": [float("nan"), 10_000]})
    j = Journal((Transaction(d[0], DEPOSIT, amount=1_000_000), Transaction(d[0], BUY, "AAA", 10, 10_000)))
    with pytest.raises(PortfolioError):
        pf.evaluate(j, m)


def test_same_day_buy_then_sell_in_any_order_and_weekend_trade_uses_session_factor():
    m, d = frame({"AAA": [10_000, 10_000, 10_000]}, actions=[{"symbol": "AAA", "exright_date": date(2026, 1, 6),
                                                              "ratio": 0.5, "title": "thưởng tỷ lệ 100%"}])
    j = Journal((Transaction(d[1], DEPOSIT, amount=1_000_000), Transaction(d[1], SELL, "AAA", 100, 10_000),
                 Transaction(d[1], BUY, "AAA", 100, 10_000)))
    r = pf.evaluate(j, m)                                   # bán được dù dòng bán nhập trước dòng mua
    assert len(r.realized) == 1
    # Mua nhập ngày 05/01 nhưng kho bắt đầu từ 06/01 (phiên không hưởng quyền): dùng hệ số của phiên 06/01
    m2 = pf.MarketFrame(m.prices.iloc[1:], m.index.iloc[1:], m.actions)
    j2 = Journal((Transaction(d[0], DEPOSIT, amount=1_000_000), Transaction(d[0], BUY, "AAA", 100, 10_000)))
    r2 = pf.evaluate(j2, m2)
    assert math.isclose(r2.daily["value"].iloc[0], 1_000_000)  # 100 cổ × 10.000, không bị nhân đôi


def test_trades_only_journal_measures_stock_sleeve():
    # Nhật ký đọc từ orderBook không có nạp/rút: tiền mua coi là nạp vào, tiền bán coi là rút ra.
    m, d = frame({"AAA": [10_000, 11_000, 11_000, 12_100], "BBB": [20_000, 20_000, 18_000, 18_000]})
    j = Journal((Transaction(d[0], BUY, "AAA", 100, 10_000), Transaction(d[1], SELL, "AAA", 100, 11_000, tax=0),
                 Transaction(d[2], BUY, "BBB", 50, 18_000), Transaction(d[3], SELL, "BBB", 50, 18_000, tax=0)))
    r = pf.evaluate(j, m)
    assert r.final_cash == 0 and any("không có nạp/rút" in n for n in r.notes)
    assert r.twr == pytest.approx(0.10)                     # +10% ở AAA, BBB mua và bán cùng giá: hòa vốn
    assert r.realized["gain"].sum() == pytest.approx(100 * 1_000)


def test_closing_sale_carries_fractional_residual_cost():
    # Cổ tức tiền mặt và cổ phiếu cùng ngày: hệ số điều chỉnh gộp cả hai nên số đơn vị còn lẻ sau khi bán hết.
    acts = [{"symbol": "AAA", "exright_date": date(2026, 1, 6), "ratio": 0.877,
             "title": "AAA - Phát hành cổ phiếu trả cổ tức tỷ lệ 14%"}]
    m, d = frame({"AAA": [10_000, 8_770, 8_600]}, actions=acts)
    j = Journal((Transaction(d[0], DEPOSIT, amount=2_000_000), Transaction(d[0], BUY, "AAA", 100, 10_000),
                 Transaction(d[2], SELL, "AAA", 114, 8_600, tax=0)))
    r = pf.evaluate(j, m)
    assert not any("Đang giữ" in n for n in r.notes)
    assert r.realized["cost"].sum() == pytest.approx(1_000_000)   # toàn bộ giá vốn được tính khi bán hết
