"""Kiểm tra sức chịu đựng của tài khoản ký quỹ khi giá giảm liên tiếp, có ràng buộc thanh khoản.

Đây là kiểm tra theo kịch bản, không phải dự báo. Mỗi phiên:
  1. Cập nhật giá theo kịch bản (giảm sàn, đường đi giá lịch sử, hoặc mức giảm người dùng nhập).
  2. Tính L = Σ khối lượng × giá, dư nợ ròng D = dư nợ − tiền mặt, tài sản ròng E = L − D, tỷ lệ ký quỹ R = E / L.
  3. R < ngưỡng bán giải chấp, hoặc R < ngưỡng gọi ký quỹ đủ `call_grace` phiên liên tiếp (SSI: vi phạm
     TLKQ duy trì ≥ 3 ngày làm việc liên tục), thì bán để đưa R về ngưỡng gọi ký quỹ R*. Bán giá trị S (trước
     thuế phí, tỷ lệ c) thì L giảm S, D giảm S(1 − c), nên cần S ≥ (R*·L − E) / (R* − c).
  4. Mỗi mã chỉ bán được tối đa `participation` × (khối lượng khớp dự kiến) cổ phiếu, làm tròn lô chẵn. Khối lượng
     khớp dự kiến ở phiên giảm sàn = bình quân 20 phiên × tỷ lệ lịch sử theo vị trí phiên trong chuỗi sàn.
     Phần chưa bán được chuyển sang phiên sau.
Giả định đơn giản hóa: tiền bán trừ ngay vào nợ (không chờ T+2); không nộp thêm tiền; không tính lãi vay phát sinh.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from . import exchange
from .market import FloorLiquidity
from .portfolio import Portfolio

SAFE, CALL, FORCE, NO_DEBT = "safe", "call", "force", "no_debt"


@dataclass(frozen=True)
class Assumptions:
    participation: float = 0.10   # phần khối lượng khớp của mã mà lệnh bán giải chấp chiếm được
    call_grace: int = 3           # số phiên liên tiếp dưới ngưỡng gọi ký quỹ thì bị bán
    tax_rate: float = 0.001       # thuế thu nhập cá nhân khi bán chứng khoán: 0,1% giá trị bán
    fee_rate: float = 0.0         # phí giao dịch theo biểu phí của người dùng

    @property
    def cost_rate(self) -> float:
        return self.tax_rate + self.fee_rate


@dataclass(frozen=True)
class Scenario:
    """`kind`: "floor" (giảm sàn `sessions` phiên), "uniform" (giảm `drop` mỗi phiên, không quá giá sàn),
    "path" (lợi suất và tỷ lệ khối lượng lịch sử, mỗi dòng một phiên, mỗi cột một mã)."""
    kind: str
    sessions: int = 1
    drop: float = 0.0
    drops: dict | None = None   # mức giảm mỗi phiên riêng cho từng mã (kịch bản tự nhập); mã không có thì dùng `drop`
    returns: pd.DataFrame | None = None
    volume: pd.DataFrame | None = None
    label: str = ""
    replaced: tuple[str, ...] = ()

    @classmethod
    def floor(cls, sessions: int) -> Scenario:
        return cls("floor", sessions=sessions, label=f"{sessions} phiên giảm sàn")

    @classmethod
    def uniform(cls, drop: float, sessions: int, drops: dict | None = None) -> Scenario:
        return cls("uniform", sessions=sessions, drop=drop, drops=drops or None,
                   label=f"Giảm {drop:.1%} mỗi phiên, {sessions} phiên")

    @classmethod
    def path(cls, returns: pd.DataFrame, volume: pd.DataFrame, label: str, replaced=()) -> Scenario:
        return cls("path", sessions=len(returns), returns=returns, volume=volume, label=label,
                   replaced=tuple(replaced))


@dataclass
class StressResult:
    sessions: pd.DataFrame          # mỗi phiên: giá trị, tài sản ròng, R, trạng thái, bán được, thiếu thanh khoản
    sales: pd.DataFrame             # mỗi lần bán: phiên, mã, khối lượng, giá, giá trị
    first_call: int | None
    first_force: int | None
    start_equity: float
    end_equity: float
    unsold_value: float             # giá trị cần bán nhưng không bán được ở phiên cuối
    scenario: Scenario
    assumptions: Assumptions
    notes: list[str] = field(default_factory=list)

    @property
    def equity_change(self) -> float:
        return self.end_equity - self.start_equity


def _ratio(market_value: float, net_debt: float) -> float:
    if net_debt <= 0:
        return math.inf
    return (market_value - net_debt) / market_value if market_value > 0 else -math.inf


def simulate(portfolio: Portfolio, quotes: pd.DataFrame, liquidity: FloorLiquidity, scenario: Scenario,
             assumptions: Assumptions = Assumptions()) -> StressResult:
    """Chạy kịch bản trên danh mục. `quotes` là kết quả market.quotes (giá, sàn, khối lượng bình quân theo mã)."""
    missing = [s for s in portfolio.symbols if s not in quotes.index or pd.isna(quotes.loc[s, "price"])]
    if missing:
        raise ValueError(f"Không có giá trong kho cho: {', '.join(missing)}")
    qty = {h.symbol: h.quantity for h in portfolio.holdings}
    price = {s: float(quotes.loc[s, "price"]) for s in qty}
    # Giá tham chiếu để tính giá sàn (UPCoM: giá bình quân; không có cột ref thì dùng giá đóng cửa).
    ref = {s: float(quotes.loc[s, "ref"]) if "ref" in quotes.columns and pd.notna(quotes.loc[s, "ref"]) else price[s]
           for s in qty}
    board = {s: exchange.board_of(quotes.loc[s, "board"]) for s in qty}
    adv = {s: float(quotes.loc[s, "adv"] or 0.0) for s in qty}
    cash, debt = portfolio.cash, portfolio.debt
    target, cost = portfolio.call_ratio, assumptions.cost_rate
    chain = {s: 0 for s in qty}  # số phiên sàn liên tiếp của từng mã
    start_equity = sum(qty[s] * price[s] for s in qty) + cash - debt

    def row(t, status, sold, short):
        lmv = sum(qty[s] * price[s] for s in qty)
        net = debt - cash
        return {"session": t, "market_value": lmv, "net_debt": max(net, 0.0), "equity": lmv - net,
                "ratio": _ratio(lmv, net), "status": status, "sold_value": sold, "shortfall_value": short}

    sales, breach, first_call, first_force, short = [], 0, None, None, 0.0
    r0 = _ratio(sum(qty[s] * price[s] for s in qty), debt - cash)
    status0 = NO_DEBT if debt <= cash else SAFE
    if debt > cash and r0 < portfolio.call_ratio:  # đang vi phạm ngay hiện tại: tính là ngày vi phạm thứ nhất
        status0, breach, first_call = CALL, 1, 0
    rows = [row(0, status0, 0.0, 0.0)]
    for t in range(1, scenario.sessions + 1):
        capacity = {}
        for s in qty:
            prev = price[s]
            floor = exchange.floor_price(board[s], ref[s])
            if scenario.kind == "floor":
                new, expected = floor, None
            elif scenario.kind == "uniform":
                d = (scenario.drops or {}).get(s, scenario.drop)
                new, expected = max(prev * (1 - d), floor), None
            else:
                new = max(prev * (1 + float(scenario.returns[s].iloc[t - 1])), 0.0)
                expected = float(scenario.volume[s].iloc[t - 1]) * adv[s]
            at_floor = new <= floor
            chain[s] = chain[s] + 1 if at_floor else 0
            if expected is None:
                expected = adv[s] * (liquidity.ratio(s, board[s], chain[s]) if at_floor else 1.0)
            price[s] = ref[s] = new  # phiên sau: giá tham chiếu xấp xỉ giá phiên này (phiên khóa sàn: bình quân = sàn)
            capacity[s] = exchange.lots_down(assumptions.participation * max(expected, 0.0))

        lmv = sum(qty[s] * price[s] for s in qty)
        net = debt - cash
        r = _ratio(lmv, net)
        status = NO_DEBT if net <= 0 else SAFE
        if net > 0 and r < portfolio.call_ratio:
            breach += 1
            status = CALL
            first_call = t if first_call is None else first_call
        else:
            breach = 0
        sold, short = 0.0, 0.0
        if net > 0 and (r < portfolio.force_ratio or breach >= assumptions.call_grace):
            status = FORCE
            first_force = t if first_force is None else first_force
            equity = lmv - net
            need = lmv if equity <= 0 else (target * lmv - equity) / max(target - cost, 1e-6)
            for s in sorted(qty, key=lambda k: capacity[k] * price[k], reverse=True):
                if need <= 0:
                    break
                lots = math.ceil(need / price[s] / exchange.LOT) * exchange.LOT
                shares = min(qty[s], capacity[s], lots)
                if shares <= 0:
                    continue
                value = shares * price[s]
                qty[s] -= shares
                cash += value * (1 - cost)
                sold += value
                need -= value
                sales.append({"session": t, "symbol": s, "shares": shares, "price": price[s], "value": value})
            short = max(need, 0.0)
            after = sum(qty[s] * price[s] for s in qty)
            if cash >= debt or _ratio(after, debt - cash) >= portfolio.call_ratio:
                breach = 0  # đã xử lý xong vi phạm (về lại ngưỡng gọi ký quỹ hoặc hết nợ)
        rows.append(row(t, status, sold, short))

    sessions = pd.DataFrame(rows)
    notes = []
    if scenario.replaced:
        notes.append("Thay bằng biến động VN-Index (thiếu dữ liệu trong giai đoạn): " + ", ".join(scenario.replaced))
    return StressResult(
        sessions=sessions,
        sales=pd.DataFrame(sales, columns=["session", "symbol", "shares", "price", "value"]),
        first_call=first_call, first_force=first_force, start_equity=start_equity,
        end_equity=float(sessions["equity"].iloc[-1]), unsold_value=short,
        scenario=scenario, assumptions=assumptions, notes=notes)


def sessions_to_sell(portfolio: Portfolio, quotes: pd.DataFrame, liquidity: FloorLiquidity,
                     assumptions: Assumptions = Assumptions(), at_floor: bool = True) -> dict[str, float]:
    """Số phiên cần để bán hết từng mã nếu mỗi phiên chỉ chiếm `participation` khối lượng khớp
    (ở giá sàn: theo tỷ lệ lịch sử của phiên sàn đầu tiên). Không bán được thì vô cực."""
    result = {}
    for h in portfolio.holdings:
        b = exchange.board_of(quotes.loc[h.symbol, "board"])
        ratio = liquidity.ratio(h.symbol, b, 1) if at_floor else 1.0
        per_session = assumptions.participation * float(quotes.loc[h.symbol, "adv"] or 0.0) * ratio
        result[h.symbol] = math.ceil(h.quantity / per_session) if per_session > 0 else math.inf
    return result
