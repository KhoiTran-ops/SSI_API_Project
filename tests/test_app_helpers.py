"""Kiểm thử các hàm hiển thị của giao diện (không cần chạy server Dash)."""

import math
from datetime import date

import pandas as pd
import pytest

from vnmkt import market, performance, rules, stress
from vnmkt.app import page_performance, page_rules, page_stress, page_whatif, ui
from vnmkt.app.theme import base_figure, fit_range, money, number, pannable, pct
from vnmkt.portfolio import Holding, Portfolio, PortfolioError
from vnmkt.whatif import BUY, Order, evaluate


def text(component) -> str:
    """Gộp toàn bộ chữ trong một cây thành phần Dash."""
    if component is None:
        return ""
    if isinstance(component, (str, int, float)):
        return str(component)
    if isinstance(component, (list, tuple)):
        return "".join(text(c) for c in component)
    return text(getattr(component, "children", None))


def liquidity():
    pos = pd.DataFrame([{"board": b, "position": p, "sessions": 10, "locked_share": 0.2, "median": 1.0}
                        for b in ("HOSE", "HNX", "UPCOM") for p in range(1, 5)])
    return market.FloorLiquidity(pos, pd.Series(dtype=float), since=None)


QUOTES = pd.DataFrame([{"symbol": "AAA", "board": "HOSE", "icb_code": "8355", "price": 20000.0, "adv": 1e7},
                       {"symbol": "BBB", "board": "HOSE", "icb_code": "8633", "price": 10000.0, "adv": 1e7}]
                      ).set_index("symbol")


def test_vietnamese_number_formats():
    assert number(1234567.891, 2) == "1.234.567,89"
    assert money(1_834_700_000) == "1,83 tỷ" and money(826_000_000) == "826 tr" and money(12_300) == "12.300 đ"
    assert money(-5_485_500, signed=True) == "−5,5 tr" and money(42_670_490, signed=True) == "+42,7 tr"
    assert pct(-0.476) == "−47,6%" and pct(0.1, signed=True) == "+10,0%"
    assert pct(math.inf) == "–" and pct(None) == "–" and money(float("nan")) == "–"


def test_charts_bars_fixed_lines_bounded_to_data():
    bar = base_figure(None)
    assert bar.layout.xaxis.fixedrange and bar.layout.yaxis.fixedrange and bar.layout.dragmode is False
    line = pannable(base_figure(None), None, 0, 9, [0.40, 0.31, math.nan], include=(0.35, 0.25))
    x = line.layout.xaxis
    assert not x.fixedrange and line.layout.dragmode == "pan"
    assert tuple(x.range) == (-0.5, 9.5) and (x.minallowed, x.maxallowed) == (-0.5, 9.5)
    lo, hi = line.layout.yaxis.range
    assert lo < 0.25 and hi > 0.40                      # ngưỡng và dữ liệu luôn trong khung
    assert fit_range([5, 5]) == pytest.approx([5 - 0.04, 5 + 0.04]) and fit_range([math.nan]) is None


def test_ratio_status_bands():
    assert ui.ratio_status(math.inf, 0.35, 0.25) == "no_debt"
    assert ui.ratio_status(0.20, 0.35, 0.25) == "force"
    assert ui.ratio_status(0.30, 0.35, 0.25) == "call"
    assert ui.ratio_status(0.37, 0.35, 0.25) == "near"
    assert ui.ratio_status(0.50, 0.35, 0.25) == "safe"


def test_stress_lede_and_tiles_for_current_breach():
    p = Portfolio((Holding("AAA", 100_000),), debt=1_500_000_000)          # R = 25% ngay hiện tại
    r = stress.simulate(p, QUOTES, liquidity(), stress.Scenario.floor(3))
    labels = [f"Phiên {i}" for i in range(4)]
    assert "hiện tại đã dưới ngưỡng gọi ký quỹ" in text(page_stress.lede(p, r, labels, "floor3"))
    assert text(page_stress.tiles(r, labels, "floor3")[0]).startswith("Gọi ký quỹHiện tại")


def test_build_scenario_custom_drops_and_bounds():
    p = Portfolio((Holding("AAA", 100), Holding("BBB", 100)))
    sc, labels = page_stress.build_scenario("custom", 50, 100, p, [{"symbol": "AAA", "drop": 3}, {"symbol": "ZZZ", "drop": 9}])
    assert sc.sessions == 60 and len(labels) == 61          # tối đa 60 phiên
    assert sc.drop == 0.15                                    # mức chung bị chặn ở biên độ 15%
    assert sc.drops == {"AAA": 0.03}                          # bỏ mã không có trong danh mục
    sc, _ = page_stress.build_scenario("custom", None, None, p, [])
    assert sc.drop == 0.05 and sc.sessions == 5 and sc.drops is None   # ô trống: giá trị mặc định


def test_whatif_order_validation_and_comparison():
    with pytest.raises(PortfolioError):
        page_whatif._order(BUY, "AAA", 10.5, 20000)
    with pytest.raises(PortfolioError):
        page_whatif._order(BUY, None, 100, 20000)
    p = Portfolio((Holding("AAA", 50_000),), debt=400_000_000)
    r = evaluate(p, Order(BUY, "BBB", 30_000, 10_000.0), QUOTES, liquidity())
    table = text(page_whatif.comparison(r, p, (0.2, 0.18)))
    assert "Độ biến động năm" in table and "−2,0 điểm %" in table
    lede = text(page_whatif.lede(r, p))
    assert "Mua 30.000 BBB" in lede and "→" in lede


@pytest.mark.parametrize("cash, added_debt", [(300_000_000, 0), (200_000_000, 100_000_000)])
def test_whatif_explains_margin_for_cash_account(cash, added_debt):
    p = Portfolio((Holding("AAA", 50_000),), cash=cash)
    r = evaluate(p, Order(BUY, "BBB", 30_000, 10_000.0), QUOTES, liquidity())
    lede = text(page_whatif.lede(r, p))
    comparison = text(page_whatif.comparison(r, p))
    if added_debt:
        assert f"Mô phỏng tính phần thiếu {money(added_debt)} vào dư nợ." in lede
        assert "Danh mục chạm ngưỡng gọi ký quỹ nếu giá trị giảm" in lede
        assert comparison.count("Không áp dụng") == 2
    else:
        assert "Sau lệnh, tài khoản không có dư nợ ròng." in lede
        assert "gọi ký quỹ" not in lede
        assert comparison.count("Không áp dụng") == 4


def test_rules_status_view():
    r = rules.Rules(max_symbol_weight=0.2, max_sector_weight=None, min_margin_ratio=0.5, max_sessions_to_sell=None)
    p = Portfolio((Holding("AAA", 50_000), Holding("BBB", 10_000)), debt=600_000_000)
    view = text(page_rules.status_view(rules.check(p, QUOTES, liquidity(), r), r))
    assert "Vi phạm (1)" in view and "AAA" in view and "Tắt" in view


def test_journal_row_ids_are_stable_and_unique():
    t = performance.Transaction(date(2026, 1, 5), performance.BUY, "AAA", 100, 10_000.0)
    j = performance.Journal((t, t, performance.Transaction(date(2026, 1, 6), performance.DEPOSIT, amount=1e6)))
    ids = page_performance.row_ids(j)
    assert len(set(ids)) == 3 and ids[0].endswith("#1") and ids[1].endswith("#2")
    assert page_performance.row_ids(performance.Journal(j.transactions[1:]))[0] == ids[0]  # xóa dòng sau không đổi id


def test_live_prices_only_when_newer_than_warehouse(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from vnmkt import store
    from vnmkt.app import data
    from vnmkt.config import VN_TZ
    now = datetime.now(VN_TZ).replace(tzinfo=None)
    w = store.TickWriter(tmp_path)
    for i, (price, total) in enumerate([(19_850, 100), (19_900, 300), (19_900, 300)]):  # dòng cuối: gửi lại (trùng)
        t = now.replace(microsecond=0) - timedelta(seconds=10 - min(i, 1))
        w.add("trade", {"symbol": "SSI", "time": t, "price": price, "quantity": 100, "total_volume": total}, now)
    w.flush()
    monkeypatch.setattr(data, "lake", lambda: (tmp_path, False))
    monkeypatch.setattr(data, "last_session", lambda: now.date() - timedelta(days=3))
    live = data.live_prices(["SSI", "HPG"])
    assert set(live) == {"SSI"} and live["SSI"][0] == 19_900
    monkeypatch.setattr(data, "last_session", lambda: now.date())       # kho đã có phiên hôm nay: dùng giá đóng cửa
    assert data.live_prices(["SSI"]) == {}
