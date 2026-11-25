"""Đọc tài khoản SSI của chính người dùng (chỉ đọc) và quy về `Portfolio` và nhật ký giao dịch.

Theo phản hồi thật ngày 25/11/2026 (ssi-sdk 3.3.2, mọi giá trị là chuỗi):
- `account/info`: danh sách {accountNo, accountType} với accountType "Cash", "Margin", "Derivative".
- `trading/position` → {"equity": [{symbol, quantity, costPrice, t1BuyQuantity, t2BuyQuantity, ...}]}.
- `trading/ppmmrAccount` → {"equity": {accountBalance, liability, equity, totalAssets, marginRatio, callLMV, forceLMV,
  debt, lmv, ...}}; equity = totalAssets − liability. Tài khoản không có dư nợ thì callLMV = forceLMV = 0.
- `trading/orderBook` (from/to dạng YYYY/MM/DD): khoảng dài hơn 365 ngày trả 0 lệnh mà không báo lỗi, nên đọc theo
  từng cửa sổ ORDER_WINDOW_DAYS ngày. Mỗi lệnh có side B/S, orderStatus (FF khớp hết, FFPC khớp một phần rồi hủy,
  EX hết hiệu lực, CL hủy), filledQty, avgPrice (đồng), inputTime, modifiedTime ("YYYY/MM/DD HH:MM:SS").
API không có lịch sử nạp/rút tiền và không trả phí của từng lệnh: hai phần này người dùng tự ghi.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from . import performance as pf
from .access import mask
from .portfolio import Holding, Portfolio, validate

ORDER_WINDOW_DAYS = 180          # nhỏ hơn giới hạn 365 ngày của orderBook
ORDER_HISTORY_START = date(2015, 1, 1)
ORDER_PAGE_SIZE = 100
SIDES = {"B": pf.BUY, "S": pf.SELL}


def _num(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if math.isfinite(x) else 0.0


def _day(text) -> date | None:
    try:
        return datetime.strptime(str(text)[:10], "%Y/%m/%d").date()
    except ValueError:
        return None


@dataclass
class AccountData:
    """Phản hồi gốc của một tiểu khoản (chỉ giữ trong bộ nhớ và trong data/, không đưa vào Git)."""
    account_no: str
    account_type: str
    positions: list[dict] = field(default_factory=list)
    ppmmr: dict = field(default_factory=dict)
    orders: list[dict] = field(default_factory=list)


def _equity(payload) -> object:
    return payload.get("equity") if isinstance(payload, dict) else None


def fetch(md, account_type: str = "Margin", today: date | None = None) -> AccountData:
    """Đọc danh mục, ký quỹ và toàn bộ lịch sử lệnh của tiểu khoản `account_type` (chỉ endpoint đọc)."""
    infos = md.read("/api/v3/account/info")
    infos = infos if isinstance(infos, list) else (infos or {}).get("data", [])
    match = [a for a in infos if str(a.get("accountType", "")).lower() == account_type.lower()]
    if not match:
        raise LookupError(f"Tài khoản không có tiểu khoản {account_type}.")
    no = match[0]["accountNo"]
    data = AccountData(no, match[0]["accountType"])
    data.positions = list(_equity(md.read("/api/v3/trading/position", {"accountNo": no})) or [])
    data.ppmmr = dict(_equity(md.read("/api/v3/trading/ppmmrAccount", {"accountNo": no})) or {})
    data.orders = fetch_orders(md, no, today)
    return data


def fetch_orders(md, account_no: str, today: date | None = None, start: date = ORDER_HISTORY_START) -> list[dict]:
    """Mọi lệnh từ `start` đến hôm nay, đọc theo cửa sổ ngắn và theo trang; bỏ trùng theo orderId."""
    end = today or date.today()
    seen, out = set(), []
    while end >= start:
        lo = max(end - timedelta(days=ORDER_WINDOW_DAYS - 1), start)
        page = 1
        while True:
            book = md.read("/api/v3/trading/orderBook", {"accountNo": account_no, "from": lo.strftime("%Y/%m/%d"),
                                                         "to": end.strftime("%Y/%m/%d"), "pageIndex": page,
                                                         "pageSize": ORDER_PAGE_SIZE})
            rows = (book.get("orderList") if isinstance(book, dict) else None) or []
            for r in rows:
                key = r.get("orderId") or (r.get("clientRequestId"), r.get("inputTime"))
                if key not in seen:
                    seen.add(key)
                    out.append(r)
            if len(rows) < ORDER_PAGE_SIZE:
                break
            page += 1
        end = lo - timedelta(days=1)
    return sorted(out, key=lambda r: str(r.get("inputTime", "")))


# ---------------------------------------------------------------- chuyển đổi
def trades(orders: list[dict], fee_rate: float = 0.0) -> list[pf.Transaction]:
    """Lệnh đã khớp (filledQty > 0, kể cả khớp một phần) -> giao dịch mua/bán theo giá khớp bình quân.
    Ngày giao dịch là ngày khớp (modifiedTime, thiếu thì inputTime). Thuế bán 0,1% tính theo quy định;
    phí = fee_rate × giá trị vì API không trả phí của từng lệnh."""
    out = []
    for o in orders:
        qty = int(_num(o.get("filledQty")))
        kind = SIDES.get(str(o.get("side", "")).upper())
        price = _num(o.get("avgPrice")) or _num(o.get("price"))
        day = _day(o.get("modifiedTime")) or _day(o.get("inputTime"))
        if qty <= 0 or kind is None or price <= 0 or day is None:
            continue
        value = qty * price
        out.append(pf.Transaction(day, kind, str(o.get("symbol", "")).upper(), qty, price, value * fee_rate,
                                  value * pf.SELL_TAX if kind == pf.SELL else 0.0))
    return out


def merge_journal(existing: pf.Journal, new_trades: list[pf.Transaction], label: str) -> pf.Journal:
    """Giữ các dòng nạp/rút người dùng tự ghi, thay toàn bộ lệnh mua/bán bằng lệnh đọc từ SSI."""
    cash = [t for t in existing.transactions if t.kind in (pf.DEPOSIT, pf.WITHDRAW)]
    return pf.validate(pf.Journal(tuple(cash + new_trades), simulated=False, label=label))


def position_quantity(row: dict) -> int:
    """Số cổ phiếu sở hữu của một dòng position: đã về tài khoản cộng phần mua chờ về T+1, T+2.
    (Chưa kiểm chứng được trên tài khoản có mã đang giữ; `cross_check` so với số dựng lại từ lệnh.)"""
    return int(_num(row.get("quantity")) + _num(row.get("t1BuyQuantity")) + _num(row.get("t2BuyQuantity")))


def to_portfolio(data: AccountData, base: Portfolio) -> Portfolio:
    """Danh mục từ position + ppmmrAccount. Tiền mặt = accountBalance, dư nợ = liability (gồm phí còn nợ).
    Ngưỡng: khi có dư nợ và SSI trả callLMV/forceLMV > 0 thì R = 1 − dư nợ / LMV ngưỡng; không thì giữ ngưỡng cũ."""
    holdings = []
    for row in data.positions:
        qty = position_quantity(row)
        if qty > 0:
            cost = _num(row.get("costPrice"))
            holdings.append(Holding(str(row.get("symbol", "")).upper(), qty, cost if cost > 0 else None))
    pp = data.ppmmr
    cash, debt = _num(pp.get("accountBalance")), _num(pp.get("liability"))
    call, force = base.call_ratio, base.force_ratio
    call_lmv, force_lmv = _num(pp.get("callLMV")), _num(pp.get("forceLMV"))
    derived = debt > 0 and call_lmv > 0 and force_lmv > 0 and 0 < 1 - debt / force_lmv < 1 - debt / call_lmv < 1
    if derived:
        call, force = 1 - debt / call_lmv, 1 - debt / force_lmv
    notes = {"source": "ssi", "account": mask(data.account_no), "account_type": data.account_type,
             "synced_at": datetime.now().isoformat(timespec="seconds"),
             "margin_ratio_ssi": _num(pp.get("marginRatio")), "equity_ssi": _num(pp.get("equity")),
             "thresholds_from_ssi": derived}
    return validate(replace(base, holdings=tuple(holdings), cash=cash, debt=debt, call_ratio=call, force_ratio=force,
                            simulated=False, label=f"SSI {data.account_type} {mask(data.account_no)}", notes=notes))


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def cross_check(p: Portfolio, data: AccountData, journal_holdings: dict[str, int]) -> list[Check]:
    """Đối chiếu số tự tính với số SSI trả về."""
    pp = data.ppmmr
    out = []
    equity_ssi, assets, liability = _num(pp.get("equity")), _num(pp.get("totalAssets")), _num(pp.get("liability"))
    out.append(Check("Tài sản ròng = tổng tài sản − nợ (SSI)", abs(assets - liability - equity_ssi) < 1,
                     f"{assets:,.0f} − {liability:,.0f} so với {equity_ssi:,.0f}"))
    lmv_ssi = _num(pp.get("lmv"))
    out.append(Check("Tiền mặt + giá trị chứng khoán ký quỹ = tổng tài sản (SSI)",
                     abs(_num(pp.get("accountBalance")) + lmv_ssi - assets) < 1,
                     f"{_num(pp.get('accountBalance')):,.0f} + {lmv_ssi:,.0f} so với {assets:,.0f}"))
    pos = {h.symbol: h.quantity for h in p.holdings}
    diff = {s: (pos.get(s, 0), journal_holdings.get(s, 0)) for s in set(pos) | set(journal_holdings)
            if pos.get(s, 0) != journal_holdings.get(s, 0)}
    out.append(Check("Số cổ phiếu theo position = số dựng lại từ lệnh khớp và sự kiện quyền", not diff,
                     "khớp" + (f" ({len(pos)} mã)" if pos else " (đều không còn mã nào)") if not diff else
                     "; ".join(f"{s}: position {a}, theo lệnh {b}" for s, (a, b) in sorted(diff.items()))))
    return out


@dataclass
class SyncResult:
    portfolio: Portfolio
    journal: pf.Journal
    checks: list[Check]
    orders: int
    filled: int
    trades: list[pf.Transaction]

    def summary(self) -> str:
        days = [t.date for t in self.trades]
        span = f" ({min(days):%d/%m/%Y}–{max(days):%d/%m/%Y})" if days else ""
        bad = [c.name for c in self.checks if not c.passed]
        return (f"Đã đồng bộ {self.portfolio.label}: {len(self.portfolio.holdings)} mã, {len(self.trades)} lệnh khớp{span}. "
                + ("Đối chiếu với SSI: khớp." if not bad else f"Đối chiếu lệch: {'; '.join(bad)}."))


def sync(md, account_type: str, base: Portfolio, old_journal: pf.Journal, lake_root, today: date,
         fee_rate: float = 0.0) -> SyncResult:
    """Đọc tiểu khoản, dựng danh mục và nhật ký, đối chiếu với số SSI (dùng chung cho dòng lệnh và giao diện)."""
    from . import store
    data = fetch(md, account_type, today)
    p = to_portfolio(data, base)
    new_trades = trades(data.orders, fee_rate)
    j = merge_journal(old_journal, new_trades, p.label)
    symbols = sorted({t.symbol for t in j.transactions if t.symbol} | set(p.symbols))
    with store.Warehouse(lake_root, read_only=True) as wh:
        actions = wh.query("SELECT symbol, exright_date, ratio, title FROM corporate_actions WHERE symbol IN ?",
                           [symbols or [""]])
    checks = cross_check(p, data, pf.holdings(j, actions))
    filled = sum(1 for o in data.orders if _num(o.get("filledQty")) > 0)
    return SyncResult(p, j, checks, len(data.orders), filled, new_trades)
