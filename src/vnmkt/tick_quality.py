"""Kiểm tra chất lượng dữ liệu thời gian thực đã ghi (một ngày): trùng, sai thứ tự, đến muộn, bất thường, mất tín hiệu.

Không sửa dữ liệu gốc. `clean_trades` trả bản đã bỏ dòng trùng và dòng bất thường để dùng cho phân tích.
- Trùng: cùng mã, cùng thời điểm khớp, cùng giá, khối lượng và tổng khối lượng (thông điệp gửi lại).
- Sai thứ tự: tổng khối lượng khớp lũy kế của mã giảm so với thông điệp trước (theo thứ tự nhận).
- Đến muộn: nhận sau thời điểm khớp quá LATE_SECONDS giây, sau khi trừ độ lệch đồng hồ của máy (trung vị độ trễ).
- Bất thường: giá không dương, ngoài biên độ trần–sàn tính từ giá tham chiếu, hoặc giá mua tốt nhất cao hơn giá bán tốt nhất.
- Mất tín hiệu: khoảng trống giữa hai thông điệp liên tiếp (mọi mã) quá GAP_SECONDS trong giờ khớp lệnh liên tục.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

import duckdb
import pandas as pd

from . import exchange

LATE_SECONDS = 5
GAP_SECONDS = 60
CONTINUOUS = ((time(9, 15), time(11, 30)), (time(13, 0), time(14, 30)))  # khớp lệnh liên tục của HOSE


def read_topic(root: Path, day: date, topic: str) -> pd.DataFrame:
    folder = Path(root) / "ticks" / f"date={day.isoformat()}" / f"topic={topic}"
    files = sorted(folder.glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    con = duckdb.connect(":memory:")
    try:
        paths = ", ".join("'" + f.as_posix().replace("'", "''") + "'" for f in files)
        # Giữ thứ tự nhận: theo tên tệp (tăng dần theo lần ghi) rồi theo số dòng trong tệp.
        return con.execute(f"SELECT * EXCLUDE (filename, file_row_number) FROM read_parquet([{paths}], "
                           "union_by_name = true, filename = true, file_row_number = true) "
                           "ORDER BY filename, file_row_number").df()
    finally:
        con.close()


@dataclass
class TickReport:
    day: date
    counts: dict[str, int]
    duplicates: int
    out_of_order: int
    late: int
    abnormal: int
    crossed_quotes: int
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    symbols: int = 0
    clock_offset: float = 0.0   # trung vị (giờ nhận − giờ khớp), giây; âm là đồng hồ máy chậm hơn sàn

    @property
    def ok(self) -> bool:
        return not (self.duplicates or self.out_of_order or self.abnormal or self.crossed_quotes or self.gaps)


def _in_session(t: datetime) -> bool:
    return any(a <= t.time() <= b for a, b in CONTINUOUS)


def check(root: Path, day: date, refs: dict[str, tuple[str, float]] | None = None) -> TickReport:
    """`refs`: mã -> (sàn, giá tham chiếu) để kiểm tra biên độ; thiếu thì bỏ qua phần biên độ."""
    trade = read_topic(root, day, "trade")
    quote = read_topic(root, day, "quote")
    index = read_topic(root, day, "index")
    counts = {"trade": len(trade), "quote": len(quote), "index": len(index)}
    dup = ooo = late = abnormal = crossed = 0
    offset = 0.0
    if len(trade):
        key = ["symbol", "time", "price", "quantity", "total_volume"]
        dup = int(trade.duplicated(key).sum())
        t = trade.drop_duplicates(key)
        ooo = int(t.groupby("symbol")["total_volume"].apply(lambda s: (s.diff() < 0).sum()).sum())
        delay = (pd.to_datetime(t["received_at"]) - pd.to_datetime(t["time"])).dt.total_seconds()
        offset = float(delay.median()) if len(delay) else 0.0
        late = int((delay - offset > LATE_SECONDS).sum())
        bad = t["price"].isna() | (t["price"] <= 0)
        if refs:
            def outside(row):
                if row["symbol"] not in refs:
                    return False
                board, ref = refs[row["symbol"]]
                return not (exchange.floor_price(board, ref) <= row["price"] <= exchange.ceiling_price(board, ref))
            bad |= t.apply(outside, axis=1)
        abnormal = int(bad.sum())
    if len(quote):
        q = quote.dropna(subset=["bid1", "ask1"])
        crossed = int(((q["bid1"] > 0) & (q["ask1"] > 0) & (q["bid1"] > q["ask1"])).sum())
    stamps = sorted(pd.to_datetime(pd.concat([df["received_at"] for df in (trade, quote, index) if len(df)]))
                    ) if any(len(df) for df in (trade, quote, index)) else []
    gaps = []
    for a, b in zip(stamps, stamps[1:]):
        if (b - a).total_seconds() > GAP_SECONDS and _in_session(a) and _in_session(b):
            gaps.append((a.to_pydatetime(), b.to_pydatetime()))
    return TickReport(day, counts, dup, ooo, late, abnormal, crossed, gaps,
                      symbols=int(trade["symbol"].nunique()) if len(trade) else 0, clock_offset=offset)


def clean_trades(root: Path, day: date) -> pd.DataFrame:
    """Khớp lệnh đã bỏ trùng và dòng giá không dương, theo thứ tự nhận."""
    t = read_topic(root, day, "trade")
    if t.empty:
        return t
    t = t.drop_duplicates(["symbol", "time", "price", "quantity", "total_volume"])
    return t[t["price"] > 0].reset_index(drop=True)


def references(wh, symbols: list[str], day: date) -> dict[str, tuple[str, float]]:
    """Giá tham chiếu của `day` cho từng mã: phiên liền trước trong kho (UPCoM dùng giá bình quân, làm tròn 100 đồng).
    Mã chưa có phiên trước `day` thì bỏ qua."""
    rows = wh.rows("""
        SELECT d.symbol, upper(s.board), d.close, d.avg FROM daily d JOIN securities s USING (symbol)
        WHERE d.symbol IN ? AND d.date = (SELECT max(date) FROM daily x WHERE x.symbol = d.symbol AND x.date < ?)
    """, [list(symbols) or [""], day])
    out = {}
    for symbol, board, close, avg in rows:
        ref = round(avg / 100) * 100 if board == "UPCOM" and avg else close
        out[symbol] = (board, float(ref))
    return out
