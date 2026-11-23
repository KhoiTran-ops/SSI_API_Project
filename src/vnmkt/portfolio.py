"""Danh mục của người dùng: nhập tay, mô phỏng, hoặc (khi tài khoản có danh mục) đọc từ SSI.

Tệp danh mục thật nằm trong `data/` (Git bỏ qua). Ngưỡng ký quỹ mặc định là dải SSI hiển thị trên trang
Margin Loan (an toàn > 0,35; bán giải chấp < 0,25); người dùng sửa theo hợp đồng của mình.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

DEFAULT_CALL_RATIO = 0.35
DEFAULT_FORCE_RATIO = 0.25
_SYMBOL = re.compile(r"^[A-Z0-9]{3,10}$")


class PortfolioError(ValueError):
    """Danh mục nhập vào không hợp lệ; thông điệp hiển thị được cho người dùng."""


@dataclass(frozen=True)
class Holding:
    symbol: str
    quantity: int
    cost_price: float | None = None  # đồng/cổ phiếu


@dataclass(frozen=True)
class Portfolio:
    holdings: tuple[Holding, ...] = ()
    cash: float = 0.0        # tiền mặt (đồng)
    debt: float = 0.0        # dư nợ ký quỹ gồm lãi (đồng)
    call_ratio: float = DEFAULT_CALL_RATIO
    force_ratio: float = DEFAULT_FORCE_RATIO
    simulated: bool = False
    label: str = ""
    notes: dict = field(default_factory=dict)

    @property
    def symbols(self) -> list[str]:
        return [h.symbol for h in self.holdings]

    def with_holdings(self, holdings) -> Portfolio:
        return replace(self, holdings=tuple(holdings))


def validate(p: Portfolio) -> Portfolio:
    """Chuẩn hóa mã (chữ in hoa) và kiểm tra ràng buộc; lỗi đầu tiên gặp được báo bằng PortfolioError."""
    seen, holdings = set(), []
    for h in p.holdings:
        symbol = str(h.symbol).strip().upper()
        if not _SYMBOL.match(symbol):
            raise PortfolioError(f"Mã {h.symbol!r} không hợp lệ.")
        if symbol in seen:
            raise PortfolioError(f"Mã {symbol} xuất hiện hai lần.")
        try:
            raw = float(h.quantity)
            cost = None if h.cost_price in (None, "") else float(h.cost_price)
        except (TypeError, ValueError):
            raise PortfolioError(f"Nhập khối lượng và giá vốn của {symbol} bằng số.") from None
        if not math.isfinite(raw) or raw <= 0 or raw != int(raw):
            raise PortfolioError(f"Khối lượng của {symbol} phải là số nguyên dương.")
        quantity = int(raw)
        if cost is not None and not (math.isfinite(cost) and cost > 0):
            raise PortfolioError(f"Giá vốn của {symbol} phải lớn hơn 0.")
        seen.add(symbol)
        holdings.append(Holding(symbol, quantity, cost))
    if not all(math.isfinite(v) and v >= 0 for v in (p.cash, p.debt)):
        raise PortfolioError("Tiền mặt và dư nợ phải là số từ 0 trở lên.")
    if not 0 < p.force_ratio < p.call_ratio < 1:
        raise PortfolioError("Nhập ngưỡng theo thứ tự 0 < ngưỡng bán giải chấp < ngưỡng gọi ký quỹ < 1.")
    return replace(p, holdings=tuple(holdings), cash=float(p.cash), debt=float(p.debt))


def from_dict(data: dict) -> Portfolio:
    holdings = tuple(Holding(h.get("symbol", ""), h.get("quantity"), h.get("cost_price"))
                     for h in data.get("holdings", []))
    return validate(Portfolio(
        holdings=holdings, cash=float(data.get("cash", 0) or 0), debt=float(data.get("debt", 0) or 0),
        call_ratio=float(data.get("call_ratio", DEFAULT_CALL_RATIO)),
        force_ratio=float(data.get("force_ratio", DEFAULT_FORCE_RATIO)),
        simulated=bool(data.get("simulated", False)), label=str(data.get("label", "")),
        notes=dict(data.get("notes", {}))))


def to_dict(p: Portfolio) -> dict:
    data = asdict(p)
    data["holdings"] = [asdict(h) for h in p.holdings]
    return data


def load(path: Path) -> Portfolio:
    try:
        return from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise PortfolioError(f"Lỗi định dạng JSON ở dòng {exc.lineno} của tệp danh mục.") from None


def save(p: Portfolio, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(to_dict(validate(p)), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
