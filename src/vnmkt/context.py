"""Bối cảnh thị trường cho danh mục: độ rộng thị trường, phân tách biến động trong phiên, ra-đa khối ngoại.

Phân tách biến động của danh mục trong một phiên (mô hình chỉ số đơn mở rộng thêm ngành):
  rᵢ = βᵢ·r_m + (r_ngành(i) − β_ngành·r_m) + phần riêng
  - thị trường: βᵢ·r_m, β ước lượng từ 250 phiên trước phiên đó so với VN-Index;
  - ngành: lợi suất trong phiên của nhóm ngành ICB cấp 2 (cổ phiếu HOSE theo vốn hóa) trừ phần do thị trường;
  - riêng: phần còn lại. Ba phần cộng đúng bằng lợi suất của mã; cộng theo tỷ trọng ra lợi suất danh mục.
Đây là phân tách mô tả sau phiên, không phải dự báo.

Ra-đa khối ngoại: giá trị mua ròng của khối ngoại (mua − bán) trong 5 và 20 phiên gần nhất, so với mức thường gặp của
chính mã đó (độ lệch chuẩn hóa z của tổng 5 phiên so với 120 phiên trước), và tỷ lệ room đã dùng. Phiên thiếu số liệu khối
ngoại được bỏ qua, không coi là 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .risk import supersector
from .store import Warehouse

BETA_SESSIONS = 250
Z_HISTORY = 120


def sessions(wh: Warehouse, n: int = 30) -> list[date]:
    rows = wh.rows("SELECT date FROM index_daily WHERE symbol = 'VNINDEX' ORDER BY date DESC LIMIT ?", [n])
    return [d for (d,) in rows]


@dataclass(frozen=True)
class Breadth:
    day: date
    index_value: float | None
    index_pct: float | None
    advances: float | None
    declines: float | None
    unchanged: float | None
    ceiling: float | None
    floor: float | None
    match_val: float | None
    foreign_net: float | None


def breadth(wh: Warehouse, day: date) -> Breadth:
    idx = wh.rows("SELECT close, pct FROM index_daily WHERE symbol = 'VNINDEX' AND date = ?", [day])
    b = wh.rows("""SELECT advances, declines, unchanged, ceiling, floor, match_val, f_net_val FROM board_daily
                   WHERE board = 'HOSE' AND date = ?""", [day])
    close, pct = idx[0] if idx else (None, None)
    vals = b[0] if b else (None,) * 7
    return Breadth(day, close, None if pct is None else pct / 100, *vals)


@dataclass
class DayAttribution:
    day: date
    table: pd.DataFrame        # theo mã: weight, ret, beta, sector, market, sector_part, specific
    market_return: float
    portfolio_return: float

    @property
    def parts(self) -> dict[str, float]:
        w = self.table["weight"]
        return {k: float((w * self.table[k]).sum()) for k in ("market", "sector_part", "specific")}


def attribute_day(wh: Warehouse, quantities: dict[str, float], day: date) -> DayAttribution:
    """Phân tách lợi suất phiên `day` của danh mục; tỷ trọng = khối lượng × giá đóng cửa phiên trước."""
    symbols = list(quantities)
    days = wh.rows("SELECT date FROM index_daily WHERE symbol = 'VNINDEX' AND date <= ? ORDER BY date DESC LIMIT ?",
                   [day, BETA_SESSIONS + 2])
    if len(days) < 60 or days[0][0] != day:
        raise LookupError("Kho không đủ phiên trước ngày này")
    start = days[-1][0]
    px = wh.query("SELECT symbol, date, close FROM daily WHERE symbol IN ? AND date BETWEEN ? AND ?",
                  [symbols, start, day]).pivot(index="date", columns="symbol", values="close").sort_index()
    idx = wh.query("SELECT date, close FROM index_daily WHERE symbol = 'VNINDEX' AND date BETWEEN ? AND ?",
                   [start, day]).set_index("date")["close"].sort_index()
    empty = pd.DataFrame(columns=["weight", "ret", "beta", "sector", "market", "sector_part", "specific"])
    day_ts = pd.Timestamp(day)
    px.index, idx.index = pd.to_datetime(px.index), pd.to_datetime(idx.index)
    if day_ts not in px.index or day_ts not in idx.index:  # không mã nào trong danh mục giao dịch phiên này
        return DayAttribution(day, empty, math.nan, math.nan)
    px = px.loc[:day_ts]
    rets = px.pct_change(fill_method=None)
    rm = idx.pct_change()
    prev_px = px.ffill().iloc[-2] if len(px) >= 2 else px.iloc[-1]
    r_m = float(rm.loc[day_ts])
    hist = rets.iloc[1:-1]
    hist_m = rm.loc[hist.index]
    secs = dict(wh.rows("SELECT symbol, icb_code FROM securities WHERE symbol IN ?", [symbols]))
    sector_ret = sector_returns(wh, day)
    sector_beta = sector_betas(wh, start, day)
    rows = []
    for s in symbols:
        if s not in rets or pd.isna(rets.loc[day_ts, s]):
            continue
        r = float(rets.loc[day_ts, s])
        h = pd.concat([hist[s], hist_m], axis=1).dropna()
        beta = float(np.cov(h.iloc[:, 0], h.iloc[:, 1])[0, 1] / h.iloc[:, 1].var()) if len(h) >= 60 else 1.0
        name = supersector(secs.get(s))
        sec_r = sector_ret.get(name)
        sec_part = 0.0 if sec_r is None else sec_r - sector_beta.get(name, 1.0) * r_m
        market = beta * r_m
        rows.append({"symbol": s, "weight": quantities[s] * float(prev_px[s]), "ret": r, "beta": beta, "sector": name,
                     "market": market, "sector_part": sec_part, "specific": r - market - sec_part})
    table = pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame(
        columns=["weight", "ret", "beta", "sector", "market", "sector_part", "specific"])
    if len(table):
        table["weight"] = table["weight"] / table["weight"].sum()
    return DayAttribution(day, table, r_m, float((table["weight"] * table["ret"]).sum()) if len(table) else math.nan)


def _sector_frame(wh: Warehouse, start: date, end: date) -> pd.DataFrame:
    df = wh.query("""
        SELECT d.symbol, d.date, d.close, s.icb_code, s.listed_share FROM daily d JOIN securities s USING (symbol)
        WHERE upper(s.board) = 'HOSE' AND s.listed_share > 0 AND d.date BETWEEN ? AND ?
    """, [start, end])
    df["sector"] = df["icb_code"].map(supersector)
    df = df.sort_values(["symbol", "date"])
    df["prev"] = df.groupby("symbol")["close"].shift(1)
    df["w"] = df["prev"] * df["listed_share"]
    df["ret"] = df["close"] / df["prev"] - 1
    return df.dropna(subset=["ret", "w"])


def sector_returns(wh: Warehouse, day: date) -> dict[str, float]:
    """Lợi suất trong phiên của mỗi nhóm ngành: cổ phiếu HOSE, trọng số vốn hóa đầu phiên."""
    prev = wh.scalar("SELECT max(date) FROM index_daily WHERE symbol = 'VNINDEX' AND date < ?", [day])
    df = _sector_frame(wh, prev, day)
    df = df[df["date"] == pd.Timestamp(day)]
    g = df.groupby("sector")
    return (g.apply(lambda x: float((x["w"] * x["ret"]).sum() / x["w"].sum()), include_groups=False)).to_dict()


def sector_betas(wh: Warehouse, start: date, end: date) -> dict[str, float]:
    """Beta của từng nhóm ngành với VN-Index trong [start, end) (không gồm phiên cuối)."""
    df = _sector_frame(wh, start, end)
    df = df[df["date"] < pd.Timestamp(end)]
    daily = df.groupby(["date", "sector"]).apply(lambda x: (x["w"] * x["ret"]).sum() / x["w"].sum(),
                                                include_groups=False).unstack()
    idx = wh.query("SELECT date, close FROM index_daily WHERE symbol = 'VNINDEX' AND date BETWEEN ? AND ?",
                   [start, end]).set_index("date")["close"].sort_index().pct_change()
    idx.index = pd.to_datetime(idx.index)
    daily.index = pd.to_datetime(daily.index)
    out = {}
    for name in daily.columns:
        h = pd.concat([daily[name], idx], axis=1).dropna()
        if len(h) >= 60:
            out[name] = float(np.cov(h.iloc[:, 0], h.iloc[:, 1])[0, 1] / h.iloc[:, 1].var())
    return out


def foreign_radar(wh: Warehouse, symbols: list[str], day: date) -> pd.DataFrame:
    """Theo mã: mua ròng 5 và 20 phiên, số phiên có dữ liệu, z của tổng 5 phiên, tỷ lệ room đã dùng."""
    rows = []
    for s in symbols:
        df = wh.query("""SELECT date, f_buy_val, f_sell_val, room_remain, room_total FROM daily
                         WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT ?""", [s, day, Z_HISTORY + 25])
        if df.empty:
            continue
        df = df.sort_values("date")
        net = df["f_buy_val"] - df["f_sell_val"]          # thiếu một vế thì cả phiên là thiếu
        last5, last20 = net.iloc[-5:], net.iloc[-20:]
        roll = net.rolling(5, min_periods=3).sum().iloc[:-5]
        z = None
        if roll.notna().sum() >= 30 and roll.std() > 0 and last5.notna().sum() >= 3:
            z = float((last5.sum() - roll.mean()) / roll.std())
        room = df.dropna(subset=["room_remain", "room_total"]).tail(1)
        used = None
        if len(room) and room["room_total"].iloc[0] > 0:
            used = float(1 - room["room_remain"].iloc[0] / room["room_total"].iloc[0])
        rows.append({"symbol": s, "net5": float(last5.sum()) if last5.notna().any() else None,
                     "net20": float(last20.sum()) if last20.notna().any() else None,
                     "days5": int(last5.notna().sum()), "days20": int(last20.notna().sum()), "z": z, "room_used": used})
    return pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame(
        columns=["net5", "net20", "days5", "days20", "z", "room_used"])
