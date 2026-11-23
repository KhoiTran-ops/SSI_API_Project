"""Đo lường rủi ro và hiệu suất của danh mục trong quá khứ (không phải dự báo).

- Phân rã rủi ro theo Euler: σp = √(wᵀΣw); đóng góp RCᵢ = wᵢ·(Σw)ᵢ / σp, ΣRCᵢ = σp. Mỗi RCᵢ tách thành phần do mã
  tự biến động wᵢ²σᵢ²/σp và phần do tương quan với các mã còn lại (phần còn lại của RCᵢ).
- Beta với VN-Index và tách phương sai thành phần thị trường βp²σm² và phần riêng.
- Phân rã Brinson, Hood & Beebower (1986) theo nhóm ngành ICB trong một giai đoạn, giả định giữ nguyên danh mục hiện tại
  suốt giai đoạn: phân bổ (wpₛ − wbₛ)·Rbₛ + chọn mã wbₛ·(Rpₛ − Rbₛ) + tương tác (wpₛ − wbₛ)(Rpₛ − Rbₛ) = Rp − Rb.
  Mốc so sánh là "Chỉ số vốn hóa HOSE" do ứng dụng tự tính: mọi cổ phiếu HOSE, trọng số = số cổ phiếu niêm yết hiện
  tại × giá đầu kỳ. Đây không phải VN-Index và không dùng để thay VN-Index; chỉ là mốc cho phần phân rã này.

Giá trong kho đã điều chỉnh theo mọi sự kiện quyền, kể cả cổ tức tiền mặt, nên lợi suất là lợi suất tổng.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .store import Warehouse

SESSIONS_PER_YEAR = 250
MIN_SESSIONS = 60
INDEX = "VNINDEX"
REFERENCE_NAME = "Chỉ số vốn hóa HOSE"

# Nhóm ngành ICB cấp 2 (mã 4 chữ số của SSI, lấy 2 chữ số đầu).
ICB_SUPERSECTORS = {
    "05": "Dầu khí", "13": "Hóa chất", "17": "Tài nguyên cơ bản", "23": "Xây dựng và vật liệu",
    "27": "Hàng và dịch vụ công nghiệp", "33": "Ô tô và phụ tùng", "35": "Thực phẩm và đồ uống",
    "37": "Hàng cá nhân và gia dụng", "45": "Y tế", "53": "Bán lẻ", "55": "Truyền thông", "57": "Du lịch và giải trí",
    "65": "Viễn thông", "75": "Điện, nước, xăng dầu khí đốt", "83": "Ngân hàng", "85": "Bảo hiểm",
    "86": "Bất động sản", "87": "Dịch vụ tài chính", "95": "Công nghệ thông tin",
}


def supersector(icb_code) -> str:
    code = str(icb_code or "").strip()
    return ICB_SUPERSECTORS.get(code[:2], "Khác") if len(code) >= 2 else "Khác"


# ---------------------------------------------------------------- dữ liệu
def returns(wh: Warehouse, symbols: list[str], sessions: int) -> tuple[pd.DataFrame, pd.Series]:
    """Lợi suất ngày của các mã và của VN-Index trong `sessions` phiên gần nhất của VN-Index.

    SSI không trả dòng cho phiên mã không có giao dịch: giá được giữ nguyên giá gần nhất trước đó (lợi suất 0), lấy cả
    giá trước cửa sổ để phiên đầu có giá tham chiếu. Chỉ bỏ các phiên trước khi mã niêm yết (mã niêm yết sau thì cửa sổ
    ngắn lại)."""
    rows = wh.query("""
        WITH cal AS (SELECT date FROM index_daily WHERE symbol = ? ORDER BY date DESC LIMIT ?),
             px AS (SELECT symbol, date, close FROM daily WHERE symbol IN ?
                      AND date >= (SELECT min(date) FROM cal) - INTERVAL 400 DAY AND date <= (SELECT max(date) FROM cal)
                    UNION ALL
                    SELECT symbol, date, close FROM index_daily WHERE symbol = ? AND date >= (SELECT min(date) FROM cal))
        SELECT symbol, date, close FROM px
    """, [INDEX, sessions + 1, list(symbols), INDEX])
    cal = sorted(rows.loc[rows["symbol"] == INDEX, "date"])
    prices = rows.pivot(index="date", columns="symbol", values="close").sort_index().ffill().reindex(cal)
    rets = prices.pct_change(fill_method=None).iloc[1:]
    if INDEX not in rets:
        raise LookupError("Kho không có VN-Index trong giai đoạn này")
    market = rets.pop(INDEX)
    missing = [s for s in symbols if s not in rets]
    if missing:
        raise LookupError(f"Kho không có giá trong giai đoạn này: {', '.join(missing)}")
    rets = rets[list(symbols)].dropna()
    return rets, market.loc[rets.index]


# ---------------------------------------------------------------- rủi ro
@dataclass(frozen=True)
class RiskReport:
    table: pd.DataFrame       # theo mã: weight, vol, beta, rc, rc_share, own, corr (năm hóa)
    vol: float                # độ biến động năm của phần cổ phiếu
    beta: float
    market_vol: float
    systematic_share: float   # phần phương sai do thị trường
    sessions: int
    start: object
    end: object


def euler(weights: pd.Series, cov: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Đóng góp rủi ro Euler. Trả bảng (weight, vol, rc, rc_share, own, corr) và σp, cùng đơn vị với cov."""
    w = weights.reindex(cov.index).fillna(0.0).to_numpy()
    sigma = cov.to_numpy()
    variance = float(w @ sigma @ w)
    vol = math.sqrt(max(variance, 0.0))
    marginal = sigma @ w
    rc = w * marginal / vol if vol > 0 else np.zeros_like(w)
    own = w ** 2 * np.diag(sigma) / vol if vol > 0 else np.zeros_like(w)
    table = pd.DataFrame({"weight": w, "vol": np.sqrt(np.diag(sigma)), "rc": rc,
                          "rc_share": rc / vol if vol > 0 else 0.0, "own": own, "corr": rc - own}, index=cov.index)
    return table, vol


def risk_report(rets: pd.DataFrame, market: pd.Series, weights: pd.Series) -> RiskReport:
    if len(rets) < MIN_SESSIONS:
        raise LookupError(f"Chỉ có {len(rets)} phiên chung, cần ít nhất {MIN_SESSIONS}")
    cov = rets.cov() * SESSIONS_PER_YEAR
    table, vol = euler(weights, cov)
    var_m = float(market.var()) * SESSIONS_PER_YEAR
    betas = rets.apply(lambda col: float(np.cov(col, market)[0, 1]) * SESSIONS_PER_YEAR / var_m)
    table["beta"] = betas
    beta = float((table["weight"] * table["beta"]).sum())
    systematic = min(beta ** 2 * var_m / vol ** 2, 1.0) if vol > 0 else 0.0
    return RiskReport(table, vol, beta, math.sqrt(var_m), systematic, len(rets), rets.index[0], rets.index[-1])


# ---------------------------------------------------------------- Brinson
@dataclass(frozen=True)
class Attribution:
    table: pd.DataFrame       # theo nhóm ngành: wp, wb, rp, rb, allocation, selection, interaction
    portfolio_return: float
    benchmark_return: float   # Chỉ số vốn hóa HOSE (tự tính)
    start: object
    end: object

    @property
    def active(self) -> float:
        return self.portfolio_return - self.benchmark_return


def brinson(wp: pd.Series, rp: pd.Series, wb: pd.Series, rb: pd.Series) -> pd.DataFrame:
    """Brinson–Hood–Beebower theo nhóm. wp, wb tổng bằng 1; rp, rb là lợi suất của nhóm (nhóm không có thì 0)."""
    groups = wp.index.union(wb.index)
    wp, wb = wp.reindex(groups).fillna(0.0), wb.reindex(groups).fillna(0.0)
    rp, rb = rp.reindex(groups).fillna(0.0), rb.reindex(groups).fillna(0.0)
    rp = rp.where(wp > 0, rb)  # nhóm danh mục không nắm giữ: không có phần chọn mã
    return pd.DataFrame({"wp": wp, "wb": wb, "rp": rp, "rb": rb, "allocation": (wp - wb) * rb,
                         "selection": wb * (rp - rb), "interaction": (wp - wb) * (rp - rb)})


def attribution(wh: Warehouse, quantities: dict[str, int], sessions: int) -> Attribution:
    """Brinson trong `sessions` phiên gần nhất, giả định giữ nguyên khối lượng hiện tại suốt giai đoạn."""
    dates = wh.rows("SELECT date FROM index_daily WHERE symbol = ? ORDER BY date DESC LIMIT ?", [INDEX, sessions + 1])
    if len(dates) < 2:
        raise LookupError("Kho không đủ phiên VN-Index")
    start, end = dates[-1][0], dates[0][0]
    # Giá gần nhất tại hoặc trước ngày đầu/cuối (mã không giao dịch đúng ngày đó vẫn có giá); mã chưa niêm yết trước
    # ngày đầu thì không có p0.
    px = wh.query("""
        SELECT d.symbol, s.icb_code, upper(s.board) AS board, s.listed_share,
               arg_max(d.close, d.date) FILTER (WHERE d.date <= ?) AS p0,
               arg_max(d.close, d.date) FILTER (WHERE d.date <= ?) AS p1
        FROM daily d JOIN securities s USING (symbol)
        WHERE d.date <= ? AND d.date >= ? - INTERVAL 400 DAY AND (upper(s.board) = 'HOSE' OR d.symbol IN ?)
        GROUP BY d.symbol, s.icb_code, s.board, s.listed_share
    """, [start, end, end, start, list(quantities)])
    px = px.dropna(subset=["p0", "p1"])
    px["sector"] = px["icb_code"].map(supersector)
    px["ret"] = px["p1"] / px["p0"] - 1
    missing = [s for s in quantities if s not in set(px["symbol"])]
    if missing:
        raise LookupError(f"Không có giá đầu và cuối giai đoạn cho: {', '.join(missing)}")
    held = px[px["symbol"].isin(list(quantities))].copy()
    held["v0"] = held["symbol"].map(quantities) * held["p0"]
    bench = px[(px["board"] == "HOSE") & (px["listed_share"] > 0)].copy()
    bench["v0"] = bench["listed_share"] * bench["p0"]

    def by_sector(frame):
        total = frame["v0"].sum()
        g = frame.groupby("sector")
        return g["v0"].sum() / total, g.apply(lambda x: float((x["v0"] * x["ret"]).sum() / x["v0"].sum()),
                                               include_groups=False)

    wp, rp = by_sector(held)
    wb, rb = by_sector(bench)
    table = brinson(wp, rp, wb, rb).sort_values("wp", ascending=False)
    return Attribution(table, float((table["wp"] * table["rp"]).sum()), float((table["wb"] * table["rb"]).sum()),
                       start, end)
