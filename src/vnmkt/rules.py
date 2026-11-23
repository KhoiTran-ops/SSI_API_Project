"""Bộ luật rủi ro do người dùng tự đặt; kiểm tra trên danh mục hiện tại và trên lệnh đang thử.

Mỗi luật để trống (None) là tắt. Các con số mặc định chỉ là điểm bắt đầu để người dùng sửa theo khẩu vị của mình.
Tệp luật lưu ở data/rules.json (Git bỏ qua).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pandas as pd

from . import stress
from .market import FloorLiquidity
from .portfolio import Portfolio, PortfolioError
from .risk import supersector


@dataclass(frozen=True)
class Rules:
    max_symbol_weight: float | None = 0.30    # tỷ trọng tối đa một mã
    max_sector_weight: float | None = 0.50    # tỷ trọng tối đa một nhóm ngành ICB cấp 2
    min_margin_ratio: float | None = 0.45     # tỷ lệ ký quỹ tối thiểu (chỉ xét khi có dư nợ)
    max_sessions_to_sell: int | None = 5      # số phiên giảm sàn tối đa để bán hết một mã


@dataclass(frozen=True)
class Check:
    rule: str          # khóa của luật trong Rules
    subject: str       # mã, nhóm ngành, hoặc "Danh mục"
    value: float
    limit: float
    passed: bool


LABELS = {
    "max_symbol_weight": "Tỷ trọng một mã",
    "max_sector_weight": "Tỷ trọng một nhóm ngành",
    "min_margin_ratio": "Tỷ lệ ký quỹ",
    "max_sessions_to_sell": "Số phiên bán hết khi giảm sàn",
}


def validate(r: Rules) -> Rules:
    for key in ("max_symbol_weight", "max_sector_weight", "min_margin_ratio"):
        v = getattr(r, key)
        if v is not None and not (math.isfinite(v) and 0 < v <= 1):
            raise PortfolioError(f"Nhập giới hạn cho “{LABELS[key]}” từ 1 đến 100%.")
    s = r.max_sessions_to_sell
    if s is not None and not (float(s) == int(s) and 1 <= int(s) <= 60):
        raise PortfolioError("Số phiên bán hết phải là số nguyên từ 1 đến 60.")
    return replace(r, max_sessions_to_sell=None if s is None else int(s))


def from_dict(data: dict) -> Rules:
    known = {k: data.get(k) for k in asdict(Rules()) if k in data}
    return validate(Rules(**{k: (None if v in ("", None) else float(v)) for k, v in known.items()}))


def to_dict(r: Rules) -> dict:
    return asdict(r)


def load(path: Path) -> Rules:
    path = Path(path)
    if not path.exists():
        return Rules()
    try:
        return from_dict(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        raise PortfolioError("Tệp luật có định dạng JSON không hợp lệ.") from None


def save(r: Rules, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(to_dict(validate(r)), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def check(p: Portfolio, quotes: pd.DataFrame, liquidity: FloorLiquidity, r: Rules) -> list[Check]:
    """Kết quả từng luật đang bật. Luật theo mã/ngành trả một dòng cho mỗi mã/ngành."""
    held = [h for h in p.holdings if h.symbol in quotes.index]
    values = {h.symbol: h.quantity * float(quotes.loc[h.symbol, "price"]) for h in held}
    lmv = sum(values.values())
    out: list[Check] = []
    if r.max_symbol_weight is not None and lmv > 0:
        for s, v in values.items():
            w = v / lmv
            out.append(Check("max_symbol_weight", s, w, r.max_symbol_weight, w <= r.max_symbol_weight + 1e-12))
    if r.max_sector_weight is not None and lmv > 0:
        sectors: dict[str, float] = {}
        for s, v in values.items():
            code = quotes.loc[s, "icb_code"] if "icb_code" in quotes.columns else None
            sectors[supersector(code)] = sectors.get(supersector(code), 0.0) + v
        for name, v in sectors.items():
            w = v / lmv
            out.append(Check("max_sector_weight", name, w, r.max_sector_weight, w <= r.max_sector_weight + 1e-12))
    net = p.debt - p.cash
    if r.min_margin_ratio is not None and net > 0 and lmv > 0:
        ratio = (lmv - net) / lmv
        out.append(Check("min_margin_ratio", "Danh mục", ratio, r.min_margin_ratio, ratio >= r.min_margin_ratio))
    if r.max_sessions_to_sell is not None and held:
        days = stress.sessions_to_sell(p.with_holdings(held), quotes, liquidity)
        for s, d in days.items():
            out.append(Check("max_sessions_to_sell", s, d, r.max_sessions_to_sell, d <= r.max_sessions_to_sell))
    return out


def failures(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.passed]
