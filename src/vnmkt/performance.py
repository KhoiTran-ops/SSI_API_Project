"""Hiệu suất từ nhật ký giao dịch: TWR, IRR (MWR), so với VN-Index và với "không giao dịch gì", chi phí, PGR/PLR.

Định giá theo giá đã điều chỉnh của kho (lợi suất tổng). Một lệnh khớp q cổ phiếu giá thật P ngày d được quy thành
u = q / F(d) "đơn vị điều chỉnh", với F(d) = tích hệ số `ratio` của các sự kiện quyền có ngày giao dịch không hưởng quyền
sau d (đã đối chiếu: khớp hệ số quan sát từ cột thay đổi giá gốc của SSI). Giá trị về sau = u × giá điều chỉnh, nên cổ
tức tiền mặt và cổ phiếu thưởng đã nằm trong giá: nhật ký **không** ghi cổ tức để tránh cộng trùng.

- TWR: lợi suất theo ngày r = V_d / (V_{d−1} + dòng tiền ngày d) − 1, coi nạp/rút xảy ra đầu ngày; TWR = Π(1 + r) − 1.
- IRR năm: lãi suất làm giá trị hiện tại của các lần nạp, rút và giá trị cuối kỳ bằng 0 (năm 365 ngày).
- PGR/PLR (Odean 1998): mỗi ngày có lệnh bán, mỗi mã đang giữ được tính là lãi/lỗ đã thực hiện (nếu bán) hoặc lãi/lỗ
  trên giấy (nếu không bán), so giá với giá vốn bình quân. PGR = lãi thực hiện / (lãi thực hiện + lãi trên giấy).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from .portfolio import PortfolioError
from .store import Warehouse

BUY, SELL, DEPOSIT, WITHDRAW = "buy", "sell", "deposit", "withdraw"
KINDS = {BUY: "Mua", SELL: "Bán", DEPOSIT: "Nạp tiền", WITHDRAW: "Rút tiền"}
SELL_TAX = 0.001  # thuế thu nhập cá nhân khi bán: 0,1% giá trị bán


@dataclass(frozen=True)
class Transaction:
    date: date
    kind: str
    symbol: str | None = None
    quantity: int = 0
    price: float = 0.0     # giá khớp thật (đồng)
    fee: float = 0.0       # phí giao dịch (đồng)
    tax: float = 0.0       # thuế bán (đồng)
    amount: float = 0.0    # số tiền nạp/rút (đồng)

    @property
    def value(self) -> float:
        return self.quantity * self.price


@dataclass(frozen=True)
class Journal:
    transactions: tuple[Transaction, ...] = ()
    simulated: bool = False
    label: str = ""


# ---------------------------------------------------------------- nhật ký: kiểm tra, đọc, ghi
def validate(j: Journal) -> Journal:
    out = []
    for i, t in enumerate(sorted(j.transactions, key=lambda t: t.date), 1):
        if t.kind not in KINDS:
            raise PortfolioError(f"Loại giao dịch ở dòng {i} không hợp lệ.")
        if t.kind in (DEPOSIT, WITHDRAW):
            if not (math.isfinite(t.amount) and t.amount > 0):
                raise PortfolioError(f"Số tiền ở dòng {i} phải lớn hơn 0.")
            out.append(Transaction(t.date, t.kind, amount=float(t.amount)))
            continue
        symbol = str(t.symbol or "").strip().upper()
        if not symbol:
            raise PortfolioError(f"Thiếu mã ở dòng {i}.")
        if not (float(t.quantity) == int(t.quantity) and int(t.quantity) > 0):
            raise PortfolioError(f"Khối lượng ở dòng {i} phải là số nguyên dương.")
        if not (math.isfinite(t.price) and t.price > 0) or t.fee < 0 or t.tax < 0:
            raise PortfolioError(f"Ở dòng {i}, giá phải lớn hơn 0, phí và thuế phải từ 0 trở lên.")
        out.append(Transaction(t.date, t.kind, symbol, int(t.quantity), float(t.price), float(t.fee), float(t.tax)))
    return Journal(tuple(out), j.simulated, j.label)


def from_dict(data: dict) -> Journal:
    rows = []
    for r in data.get("transactions", []):
        try:
            d = date.fromisoformat(str(r["date"])[:10])
        except (KeyError, ValueError):
            raise PortfolioError("Nhập ngày giao dịch hợp lệ theo dạng YYYY-MM-DD.") from None
        kind = r.get("kind")
        price = float(r.get("price") or 0)
        qty = r.get("quantity") or 0
        tax = r.get("tax")
        if kind == SELL and tax in (None, ""):
            tax = SELL_TAX * float(qty) * price
        rows.append(Transaction(d, kind, r.get("symbol"), qty, price, float(r.get("fee") or 0), float(tax or 0),
                                float(r.get("amount") or 0)))
    return validate(Journal(tuple(rows), bool(data.get("simulated", False)), str(data.get("label", ""))))


def to_dict(j: Journal) -> dict:
    rows = []
    for t in j.transactions:
        d = asdict(t)
        d["date"] = t.date.isoformat()
        rows.append(d)
    return {"transactions": rows, "simulated": j.simulated, "label": j.label}


def load(path: Path) -> Journal:
    path = Path(path)
    if not path.exists():
        return Journal()
    try:
        return from_dict(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        raise PortfolioError("Tệp nhật ký có định dạng JSON không hợp lệ.") from None


def save(j: Journal, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(to_dict(validate(j)), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- dữ liệu thị trường
def adjustment_factors(actions: pd.DataFrame, symbol: str, dates) -> pd.Series:
    """F(d) = tích `ratio` của các sự kiện quyền của mã có ngày không hưởng quyền sau d.

    SSI ghi cùng một hệ số tổng cho mọi sự kiện trong cùng một ngày (đã kiểm tra: 907 ngày có nhiều sự kiện, không ngày
    nào khác hệ số), nên mỗi ngày chỉ lấy một hệ số."""
    ev = actions[(actions["symbol"] == symbol) & actions["ratio"].notna() & (actions["ratio"] > 0)]
    ev = ev.drop_duplicates(["exright_date"]).sort_values("exright_date")
    out = {}
    for d in dates:
        later = ev.loc[pd.to_datetime(ev["exright_date"]).dt.date > d, "ratio"]
        out[d] = float(later.prod()) if len(later) else 1.0
    return pd.Series(out)


@dataclass
class MarketFrame:
    prices: pd.DataFrame        # giá đóng cửa điều chỉnh, dòng = ngày giao dịch (VN-Index), cột = mã
    index: pd.Series            # VN-Index
    actions: pd.DataFrame       # sự kiện quyền (symbol, exright_date, ratio)


def market_frame(wh: Warehouse, symbols: list[str], start: date, end: date | None = None) -> MarketFrame:
    end = end or wh.scalar("SELECT max(date) FROM index_daily WHERE symbol = 'VNINDEX'")
    idx = wh.query("SELECT date, close FROM index_daily WHERE symbol = 'VNINDEX' AND date BETWEEN ? AND ? ORDER BY date",
                   [start, end])
    idx["date"] = pd.to_datetime(idx["date"]).dt.date
    px = wh.query("SELECT symbol, date, close FROM daily WHERE symbol IN ? AND date BETWEEN ? AND ?",
                  [list(symbols) or [""], start, end])
    px["date"] = pd.to_datetime(px["date"]).dt.date
    prices = px.pivot(index="date", columns="symbol", values="close").reindex(idx["date"]).ffill()
    actions = wh.query("SELECT symbol, exright_date, ratio, title FROM corporate_actions WHERE symbol IN ?",
                       [list(symbols) or [""]])
    return MarketFrame(prices, idx.set_index("date")["close"], actions)


# ---------------------------------------------------------------- tính toán
@dataclass
class PerformanceReport:
    daily: pd.DataFrame          # theo ngày: value, flow, ret, twr_index, index_level, hold_index
    twr: float
    irr: float | None            # năm hóa; None nếu không giải được
    index_return: float
    hold_return: float           # "không giao dịch gì" sau ngày giao dịch đầu tiên
    costs: float                 # tổng phí + thuế
    turnover: float              # tổng giá trị mua bán / giá trị bình quân
    pgr: float | None
    plr: float | None
    realized: pd.DataFrame       # các lần bán: lãi/lỗ đã thực hiện
    final_cash: float            # tiền mặt cuối kỳ theo nhật ký (âm: đang vay); chưa gồm cổ tức tiền mặt đã nhận
    start: date
    end: date
    notes: list[str] = field(default_factory=list)

    @property
    def disposition(self) -> float | None:
        return None if self.pgr is None or self.plr is None else self.pgr - self.plr


def _irr(flows: list[tuple[date, float]], final_date: date, final_value: float) -> float | None:
    """IRR năm: Σ c_t (1+i)^−(t−t0)/365 = 0 với c = −nạp, +rút, +giá trị cuối. Giải bằng chia đôi."""
    if not flows:
        return None
    t0 = flows[0][0]
    cash = [(d, -a) for d, a in flows] + [(final_date, final_value)]

    def npv(rate):
        return sum(c / (1 + rate) ** ((d - t0).days / 365) for d, c in cash)

    lo, hi = -0.99, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            break
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def evaluate(j: Journal, m: MarketFrame) -> PerformanceReport:
    if not j.transactions:
        raise PortfolioError("Chưa có giao dịch trong nhật ký.")
    days = list(m.prices.index)
    if not days:
        raise PortfolioError("Thiếu dữ liệu phiên giao dịch trong giai đoạn ghi nhật ký.")
    symbols = sorted({t.symbol for t in j.transactions if t.symbol})
    missing = [s for s in symbols if s not in m.prices.columns]
    if missing:
        raise PortfolioError(f"Thiếu dữ liệu giá của {', '.join(missing)}.")
    by_day: dict[date, list[Transaction]] = {}
    for t in j.transactions:
        trade_day = next((d for d in days if d >= t.date), None)  # giao dịch ngày nghỉ: tính vào phiên kế tiếp
        if trade_day is None:
            raise PortfolioError(f"Ngày giao dịch {t.date:%d/%m/%Y} sau phiên gần nhất có dữ liệu.")
        by_day.setdefault(trade_day, []).append(t)
    # Trong một phiên: nạp tiền, mua, bán, rút tiền (mua rồi bán trong ngày không bị báo bán quá số đang có).
    order = {DEPOSIT: 0, BUY: 1, SELL: 2, WITHDRAW: 3}
    for d in by_day:
        by_day[d].sort(key=lambda t: order[t.kind])
    # Hệ số điều chỉnh lấy theo phiên ghi nhận giao dịch, không theo ngày nhập (có thể là ngày nghỉ).
    factors = {s: adjustment_factors(m.actions, s, sorted({d for d, ts in by_day.items() for t in ts if t.symbol == s}))
               for s in symbols}

    # Không có dòng nạp/rút (ví dụ nhật ký đọc từ orderBook của SSI, API không có lịch sử tiền): coi tiền mua là nạp
    # vào và tiền bán là rút ra, lợi suất khi đó là của riêng phần cổ phiếu.
    implicit = not any(t.kind in (DEPOSIT, WITHDRAW) for t in j.transactions)
    units = {s: 0.0 for s in symbols}
    cost = {s: 0.0 for s in symbols}             # giá vốn (đồng) của số đơn vị đang giữ
    cash, costs, traded = 0.0, 0.0, 0.0
    rows, flows, realized = [], [], []
    rg = pg = rl = pl = 0
    hold_units, hold_cash = None, 0.0
    prev_value = None
    for d in days:
        px = m.prices.loc[d]
        flow = 0.0
        inflow = outflow = 0.0
        sold_today = set()
        for t in by_day.get(d, []):
            if t.kind == DEPOSIT:
                cash += t.amount
                flow += t.amount
                inflow += t.amount
                flows.append((d, t.amount))
            elif t.kind == WITHDRAW:
                cash -= t.amount
                flow -= t.amount
                outflow += t.amount
                flows.append((d, -t.amount))
            else:
                if pd.isna(px.get(t.symbol)):
                    raise PortfolioError(f"Không có giá của {t.symbol} phiên {d:%d/%m/%Y} do mã chưa niêm yết hoặc thiếu dữ liệu.")
                u = t.quantity / factors[t.symbol][d]
                if t.kind == BUY:
                    units[t.symbol] += u
                    cost[t.symbol] += t.value + t.fee
                    cash -= t.value + t.fee
                    costs += t.fee
                    if implicit:
                        cash += t.value + t.fee
                        flow += t.value + t.fee
                        inflow += t.value + t.fee
                        flows.append((d, t.value + t.fee))
                else:
                    if u > units[t.symbol] * (1 + 1e-9):
                        raise PortfolioError(f"Khối lượng bán {t.symbol} ngày {t.date:%d/%m/%Y} vượt số cổ phiếu nắm giữ.")
                    # Còn dưới một cổ phiếu sau lệnh này (phần lẻ do hệ số điều chỉnh gộp cả cổ tức tiền mặt):
                    # coi như bán hết, lệnh này gánh toàn bộ giá vốn còn lại.
                    closing = (units[t.symbol] - u) * factors[t.symbol][d] < 1 - 1e-6
                    sold_cost = cost[t.symbol] if closing else cost[t.symbol] / units[t.symbol] * u
                    proceeds = t.value - t.fee - t.tax
                    gain = proceeds - sold_cost
                    realized.append({"date": d, "symbol": t.symbol, "quantity": t.quantity, "price": t.price,
                                     "cost": sold_cost, "proceeds": proceeds, "gain": gain})
                    if gain > 0:
                        rg += 1
                    elif gain < 0:
                        rl += 1
                    sold_today.add(t.symbol)
                    cost[t.symbol] -= sold_cost
                    units[t.symbol] -= u
                    if closing:
                        units[t.symbol], cost[t.symbol] = 0.0, 0.0
                    cash += proceeds
                    costs += t.fee + t.tax
                    if implicit:
                        cash -= proceeds
                        flow -= proceeds
                        outflow += proceeds
                        flows.append((d, -proceeds))
                traded += t.value
        if sold_today:  # lãi/lỗ trên giấy của các mã đang giữ mà không bán trong ngày
            for s in symbols:
                if s in sold_today or units[s] <= 0 or pd.isna(px.get(s)):
                    continue
                paper = units[s] * float(px[s]) - cost[s]
                if paper > 0:
                    pg += 1
                elif paper < 0:
                    pl += 1
        value = cash + sum(units[s] * float(px[s]) for s in symbols if units[s] > 0 and not pd.isna(px[s]))
        if hold_units is None and by_day.get(d):
            hold_units, hold_cash = dict(units), cash      # danh mục sau ngày giao dịch đầu tiên
        elif hold_units is not None and not implicit:   # dòng tiền suy ra từ lệnh không thuộc kịch bản giữ nguyên
            hold_cash += flow
        hold_value = None if hold_units is None else hold_cash + sum(
            hold_units[s] * float(px[s]) for s in symbols if hold_units[s] > 0 and not pd.isna(px[s]))
        # Tiền vào tính từ đầu phiên, tiền ra tính cuối phiên (bán xong mới rút).
        base = (prev_value or 0.0) + inflow
        ret = (value + outflow) / base - 1 if prev_value is not None and base > 0 else 0.0
        rows.append({"date": d, "value": value, "flow": 0.0 if implicit else flow, "ret": ret, "hold_value": hold_value})
        prev_value = value

    daily = pd.DataFrame(rows).set_index("date")
    start = next(d for d in days if by_day.get(d))
    daily = daily.loc[start:]
    daily["twr_index"] = (1 + daily["ret"]).cumprod() * 100
    daily["index_level"] = m.index.loc[start:] / m.index.loc[start] * 100
    hold = daily["hold_value"].astype(float)
    hold_flow = daily["flow"].where(daily.index != start, 0.0)
    hold_ret = (hold / (hold.shift(1) + hold_flow) - 1).fillna(0.0)
    daily["hold_index"] = (1 + hold_ret).cumprod() * 100
    end = daily.index[-1]
    avg_value = float(daily["value"].clip(lower=0).mean()) or math.nan
    notes = ["Nhật ký không có nạp/rút tiền: coi tiền mua là nạp vào, tiền bán là rút ra; "
             "lợi suất là của phần cổ phiếu"] if implicit else []
    if (daily["value"] <= 0).any() and not implicit:
        notes.append("Có ngày tài sản ròng không dương: TWR các ngày đó không có ý nghĩa")
    held_left = {s: u for s, u in units.items() if u > 0}
    return PerformanceReport(
        daily=daily, twr=float(daily["twr_index"].iloc[-1] / 100 - 1),
        irr=_irr(flows, end, float(daily["value"].iloc[-1])),
        index_return=float(daily["index_level"].iloc[-1] / 100 - 1),
        hold_return=float(daily["hold_index"].iloc[-1] / 100 - 1),
        costs=costs, turnover=traded / avg_value if avg_value > 0 else math.nan,
        pgr=rg / (rg + pg) if rg + pg else None, plr=rl / (rl + pl) if rl + pl else None,
        realized=pd.DataFrame(realized, columns=["date", "symbol", "quantity", "price", "cost", "proceeds", "gain"]),
        final_cash=cash, start=start, end=end, notes=notes + ([f"Đang giữ {len(held_left)} mã"] if held_left else []))


_SHARE_EVENT = re.compile(r"(cổ phiếu thưởng|trả cổ tức|cổ tức bằng cổ phiếu)\D*?tỷ lệ\s*([\d.,]+)\s*%", re.IGNORECASE)


def share_multiplier(title: str) -> float:
    """Hệ số nhân số cổ phiếu của sự kiện: thưởng/cổ tức bằng cổ phiếu "tỷ lệ x%" -> 1 + x/100; sự kiện khác -> 1
    (cổ tức tiền mặt không đổi số cổ phiếu; quyền mua chỉ đổi khi người dùng thực hiện, phải ghi vào nhật ký)."""
    text = title or ""
    if "bằng tiền" in text.lower():
        return 1.0
    m = _SHARE_EVENT.search(text)
    return 1.0 + float(m.group(2).replace(",", ".")) / 100 if m else 1.0


def holdings(j: Journal, actions: pd.DataFrame) -> dict[str, int]:
    """Số cổ phiếu thật đang giữ sau toàn bộ nhật ký, cộng cổ phiếu thưởng và cổ tức bằng cổ phiếu về sau (làm tròn
    xuống như khi phân bổ cổ phiếu lẻ). `actions` cần cột symbol, exright_date, title."""
    shares: dict[str, float] = {}
    for t in j.transactions:
        if t.kind not in (BUY, SELL):
            continue
        ev = actions[(actions["symbol"] == t.symbol) & (pd.to_datetime(actions["exright_date"]).dt.date > t.date)]
        mult = math.prod(share_multiplier(x) for x in ev["title"])
        shares[t.symbol] = shares.get(t.symbol, 0.0) + (1 if t.kind == BUY else -1) * t.quantity * mult
    return {s: int(math.floor(v + 1e-6)) for s, v in shares.items() if v >= 1 - 1e-6}
