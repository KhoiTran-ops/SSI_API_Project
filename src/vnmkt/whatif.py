"""Thử một lệnh trước khi đặt: lệnh đó làm danh mục thay đổi thế nào. Không gửi lệnh, không theo dõi tiếp.

Lệnh được áp lên bản sao danh mục:
  - Mua: thêm cổ phiếu; tiền trả = giá trị × (1 + phí), lấy từ tiền mặt trước, thiếu thì thành dư nợ ký quỹ.
  - Bán: bớt cổ phiếu; tiền nhận = giá trị × (1 − phí − thuế), về tài khoản ngày T+2 nhưng được tính ngay vào tài
    sản khi xét tỷ lệ ký quỹ (như cách SSI tính tiền bán chờ về), nên ở đây trừ thẳng vào dư nợ / cộng vào tiền mặt.
Kết quả so sánh trước và sau: giá trị, dư nợ ròng, tỷ lệ ký quỹ, khoảng giảm giá còn chịu được trước khi bị gọi
ký quỹ, số phiên giảm sàn liên tiếp trước khi bị gọi ký quỹ, tỷ trọng.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from . import exchange, stress
from .market import FloorLiquidity
from .portfolio import Holding, Portfolio, PortfolioError, validate

BUY, SELL = "buy", "sell"
FLOOR_HORIZON = 10  # đếm số phiên giảm sàn tới khi bị gọi ký quỹ, tối đa bấy nhiêu phiên


@dataclass(frozen=True)
class Order:
    side: str
    symbol: str
    quantity: int
    price: float           # đồng/cổ phiếu


@dataclass(frozen=True)
class Snapshot:
    market_value: float
    net_debt: float
    equity: float
    ratio: float                 # vô cực khi không có dư nợ
    call_gap: float | None       # giá trị danh mục còn giảm được trước khi bị gọi ký quỹ (đồng); None nếu không vay
    floor_sessions: int | None   # số phiên giảm sàn liên tiếp tới khi bị gọi ký quỹ; None nếu > FLOOR_HORIZON
    weights: dict[str, float]

    @property
    def call_gap_pct(self) -> float | None:
        return None if self.call_gap is None or self.market_value <= 0 else self.call_gap / self.market_value


@dataclass
class WhatIfResult:
    order: Order
    value: float                 # giá trị lệnh (đồng)
    costs: float                 # phí và thuế (đồng)
    cash_change_t2: float        # tiền thay đổi khi thanh toán xong (âm: trả ra, dương: nhận về)
    before: Snapshot
    after: Snapshot
    portfolio_after: Portfolio
    warnings: list[str] = field(default_factory=list)


def apply(p: Portfolio, order: Order, fee_rate: float = 0.0, tax_rate: float = 0.001) -> tuple[Portfolio, float, float]:
    """Danh mục sau lệnh, giá trị lệnh, phí thuế. Báo PortfolioError nếu lệnh không hợp lệ."""
    if order.side not in (BUY, SELL):
        raise PortfolioError("Chọn lệnh mua hoặc bán.")
    if not (isinstance(order.quantity, int) and order.quantity > 0):
        raise PortfolioError("Khối lượng phải là số nguyên dương.")
    if not (math.isfinite(order.price) and order.price > 0):
        raise PortfolioError("Giá phải lớn hơn 0.")
    held = {h.symbol: h for h in p.holdings}
    value = order.quantity * order.price
    if order.side == BUY:
        costs = value * fee_rate
        old = held.get(order.symbol)
        qty = (old.quantity if old else 0) + order.quantity
        cost_price = None
        if old and old.cost_price:  # giá vốn bình quân gia quyền, gồm phí mua
            cost_price = (old.quantity * old.cost_price + value + costs) / qty
        elif not old:
            cost_price = (value + costs) / order.quantity
        held[order.symbol] = Holding(order.symbol, qty, cost_price)
        pay = value + costs
        from_cash = min(p.cash, pay)
        cash, debt = p.cash - from_cash, p.debt + (pay - from_cash)
    else:
        old = held.get(order.symbol)
        if old is None:
            raise PortfolioError(f"Danh mục không nắm giữ {order.symbol}.")
        if order.quantity > old.quantity:
            raise PortfolioError(f"Danh mục chỉ có {old.quantity:,} cổ phiếu {order.symbol}.".replace(",", "."))
        costs = value * (fee_rate + tax_rate)
        rest = old.quantity - order.quantity
        if rest:
            held[order.symbol] = Holding(order.symbol, rest, old.cost_price)
        else:
            del held[order.symbol]
        receive = value - costs
        repay = min(p.debt, receive)
        cash, debt = p.cash + (receive - repay), p.debt - repay
    after = validate(Portfolio(tuple(held.values()), cash=cash, debt=debt, call_ratio=p.call_ratio,
                               force_ratio=p.force_ratio, simulated=p.simulated, label=p.label, notes=p.notes))
    return after, value, costs


def snapshot(p: Portfolio, quotes: pd.DataFrame, liquidity: FloorLiquidity) -> Snapshot:
    values = {h.symbol: h.quantity * float(quotes.loc[h.symbol, "price"]) for h in p.holdings}
    lmv = sum(values.values())
    net = p.debt - p.cash
    ratio = math.inf if net <= 0 else ((lmv - net) / lmv if lmv > 0 else -math.inf)
    gap = None if net <= 0 else lmv - net / (1 - p.call_ratio)
    sessions = None
    if net > 0 and p.holdings:
        sessions = stress.simulate(p, quotes, liquidity, stress.Scenario.floor(FLOOR_HORIZON)).first_call
    return Snapshot(lmv, max(net, 0.0), lmv - net, ratio, gap, sessions,
                    {s: v / lmv for s, v in values.items()} if lmv > 0 else {})


def evaluate(p: Portfolio, order: Order, quotes: pd.DataFrame, liquidity: FloorLiquidity,
             fee_rate: float = 0.0, tax_rate: float = 0.001) -> WhatIfResult:
    """So sánh danh mục trước và sau lệnh. `quotes` phải có giá của mọi mã trong danh mục và mã của lệnh."""
    if order.symbol not in quotes.index:
        raise PortfolioError(f"Thiếu dữ liệu giá của {order.symbol}.")
    after, value, costs = apply(p, order, fee_rate, tax_rate)
    warnings = []
    board = exchange.board_of(quotes.loc[order.symbol, "board"])
    last = float(quotes.loc[order.symbol, "ref"]) if "ref" in quotes.columns else float(quotes.loc[order.symbol, "price"])
    low, high = exchange.floor_price(board, last), exchange.ceiling_price(board, last)
    if not low <= order.price <= high:
        warnings.append(f"Giá nằm ngoài biên độ phiên kế tiếp ({low:,.0f}–{high:,.0f} đồng)".replace(",", "."))
    if order.quantity % exchange.LOT:
        warnings.append("Khối lượng không chẵn lô 100: phần lẻ phải đặt ở bảng giao dịch lô lẻ")
    before_snap, after_snap = snapshot(p, quotes, liquidity), snapshot(after, quotes, liquidity)
    if after_snap.ratio < p.call_ratio <= before_snap.ratio:
        warnings.append("Sau lệnh, tỷ lệ ký quỹ xuống dưới ngưỡng gọi ký quỹ")
    if order.side == BUY:
        warnings.append("Cổ phiếu mua về tài khoản ngày T+2, trước đó chưa bán được")
    cash_change = -(value + costs) if order.side == BUY else value - costs
    return WhatIfResult(order, value, costs, cash_change, before_snap, after_snap, after, warnings)
