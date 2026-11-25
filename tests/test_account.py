import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from vnmkt import account, performance as pf
from vnmkt.portfolio import Portfolio

FIX = json.loads((Path(__file__).parent / "fixtures" / "ssi_account.json").read_text(encoding="utf-8"))


class FakeSSI:
    """Giả lập các endpoint đọc theo hành vi thật: orderBook trả 0 lệnh khi khoảng ngày > 365 ngày, chia trang."""

    def __init__(self, orders=None, page_size_cap=100):
        self.orders = FIX["orders"] if orders is None else orders
        self.cap = page_size_cap
        self.calls = []

    def read(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path == "/api/v3/account/info":
            return FIX["account_info"]
        if path == "/api/v3/trading/position":
            return FIX["position"]
        if path == "/api/v3/trading/ppmmrAccount":
            return FIX["ppmmr"]
        if path == "/api/v3/trading/orderBook":
            lo = datetime.strptime(params["from"], "%Y/%m/%d").date()
            hi = datetime.strptime(params["to"], "%Y/%m/%d").date()
            if (hi - lo).days > 365:
                return {"accountNo": params["accountNo"], "orderList": [], "totalRecord": 0}
            rows = [o for o in self.orders if lo <= datetime.strptime(o["inputTime"][:10], "%Y/%m/%d").date() <= hi]
            size = min(params["pageSize"], self.cap)
            page = rows[(params["pageIndex"] - 1) * size: params["pageIndex"] * size]
            return {"accountNo": params["accountNo"], "orderList": page, "totalRecord": len(rows)}
        raise AssertionError(f"endpoint ngoài danh sách đọc: {path}")


def test_fetch_reads_all_orders_in_short_windows():
    ssi = FakeSSI()
    data = account.fetch(ssi, "Margin", date(2026, 11, 25))
    assert data.account_type == "Margin" and data.account_no.endswith("6")
    assert len(data.orders) == len(FIX["orders"])                      # đủ lệnh dù trải từ 2025 đến 2026
    windows = [(p["from"], p["to"]) for path, p in ssi.calls if path.endswith("orderBook")]
    spans = [(datetime.strptime(b, "%Y/%m/%d") - datetime.strptime(a, "%Y/%m/%d")).days for a, b in windows]
    assert max(spans) < 365 and windows[-1][0] == "2015/01/01"         # không cửa sổ nào chạm giới hạn 365 ngày


def test_fetch_pages_and_dedups():
    many = [dict(FIX["orders"][0], orderId=str(i), clientRequestId=f"c{i}") for i in range(250)]
    data = account.fetch(FakeSSI(orders=many + many[:5]), "Margin", date(2026, 11, 25))
    assert len(data.orders) == 250                                      # 3 trang, bỏ 5 lệnh trùng orderId


def test_missing_subaccount():
    with pytest.raises(LookupError):
        account.fetch(FakeSSI(), "Futures", date(2026, 11, 25))


def test_trades_keep_only_filled_quantity_at_average_price():
    t = account.trades(FIX["orders"], fee_rate=0.0015)
    assert [(x.symbol, x.kind, x.quantity, x.price) for x in t] == [
        ("HPG", pf.BUY, 1000, 26000), ("HPG", pf.BUY, 500, 26040),      # FFPC: chỉ phần đã khớp
        ("VND", pf.BUY, 30, 21550), ("VND", pf.SELL, 30, 23250)]         # EX, CL không khớp: bỏ; lô lẻ giữ nguyên
    assert t[1].date == date(2026, 11, 21)                              # ngày khớp (modifiedTime)
    sell = t[3]
    assert sell.tax == pytest.approx(30 * 23250 * 0.001) and sell.fee == pytest.approx(30 * 23250 * 0.0015)


def test_portfolio_from_position_and_margin():
    data = account.fetch(FakeSSI(), "Margin", date(2026, 11, 25))
    p = account.to_portfolio(data, Portfolio())
    assert [(h.symbol, h.quantity, h.cost_price) for h in p.holdings] == [("HPG", 1500, 26000)]  # gồm 500 chờ về T+2
    assert (p.cash, p.debt) == (1_000_000, 20_000_000) and not p.simulated
    assert p.call_ratio == pytest.approx(0.35, abs=1e-6) and p.force_ratio == pytest.approx(0.25, abs=1e-6)
    assert p.notes["thresholds_from_ssi"] and p.notes["account"].endswith("6") and "000C" not in p.label


def test_thresholds_kept_without_debt():
    data = account.AccountData("000C000006", "Margin", [], {"accountBalance": "9970", "liability": "6",
                                                             "callLMV": "0", "forceLMV": "0"})
    p = account.to_portfolio(data, Portfolio(call_ratio=0.4, force_ratio=0.3))
    assert (p.call_ratio, p.force_ratio, p.cash, p.debt) == (0.4, 0.3, 9970, 6) and not p.notes["thresholds_from_ssi"]


def test_cross_check_matches_position_with_order_history():
    data = account.fetch(FakeSSI(), "Margin", date(2026, 11, 25))
    p = account.to_portfolio(data, Portfolio())
    j = pf.validate(pf.Journal(tuple(account.trades(data.orders))))
    no_actions = pd.DataFrame(columns=["symbol", "exright_date", "ratio", "title"])
    checks = account.cross_check(p, data, pf.holdings(j, no_actions))
    assert all(c.passed for c in checks), [c for c in checks if not c.passed]
    bad = account.cross_check(p, data, {"HPG": 1000})
    assert not bad[-1].passed and "HPG" in bad[-1].detail


def test_merge_keeps_manual_cash_rows():
    old = pf.from_dict({"transactions": [
        {"date": "2025-07-01", "kind": "deposit", "amount": 5_000_000},
        {"date": "2025-07-02", "kind": "buy", "symbol": "AAA", "quantity": 100, "price": 10_000}]})
    j = account.merge_journal(old, account.trades(FIX["orders"]), "SSI Margin")
    kinds = [t.kind for t in j.transactions]
    assert kinds.count(pf.DEPOSIT) == 1 and "AAA" not in {t.symbol for t in j.transactions}
    assert len(j.transactions) == 5 and not j.simulated and j.label == "SSI Margin"
