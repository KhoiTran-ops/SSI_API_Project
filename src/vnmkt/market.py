"""Truy vấn dữ liệu thị trường từ kho cho các phép tính rủi ro. Chỉ đọc, không gọi SSI.

Giá trong kho là giá đã điều chỉnh theo sự kiện quyền (SSI điều chỉnh lùi lịch sử), nên giá đóng cửa phiên
trước là giá tham chiếu của phiên sau.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from .store import Warehouse

ADV_SESSIONS = 20
LIQUIDITY_SINCE = date(2013, 1, 1)  # HOSE áp biên độ 7% từ 15/01/2013
MAX_CHAIN_POSITION = 4              # phiên sàn thứ 4 trở đi gộp chung một nhóm
SCALE_MIN_SESSIONS = 5              # số phiên sàn tối thiểu để dùng hệ số riêng của mã
SCALE_CLIP = (0.25, 4.0)

# Phiên giảm sàn: giá đóng cửa bằng giá sàn tính từ giá đóng cửa phiên trước (cùng công thức với
# exchange.floor_price). Bỏ phiên giảm quá biên độ thường (biên độ đặc biệt ngày đầu niêm yết hoặc giao dịch lại).
_FLOOR_SESSIONS = f"""
WITH d AS (
    SELECT d.symbol, upper(s.board) AS board, d.date, d.close, d.high, d.low, d.match_vol,
           -- Giá tham chiếu: UPCoM là giá bình quân phiên trước, HOSE/HNX là giá đóng cửa (đối chiếu masterdata).
           CASE WHEN upper(s.board) = 'UPCOM' THEN lag(d.avg) OVER w ELSE lag(d.close) OVER w END AS prev,
           avg(d.match_vol) OVER (w ROWS BETWEEN {ADV_SESSIONS} PRECEDING AND 1 PRECEDING) AS adv,
           count(d.match_vol) OVER (w ROWS BETWEEN {ADV_SESSIONS} PRECEDING AND 1 PRECEDING) AS n_adv
    FROM daily d JOIN securities s USING (symbol)
    WHERE upper(s.board) IN ('HOSE', 'HNX', 'UPCOM')
    WINDOW w AS (PARTITION BY d.symbol ORDER BY d.date)),
b AS (SELECT *, CASE board WHEN 'HOSE' THEN 0.07 WHEN 'HNX' THEN 0.10 ELSE 0.15 END AS band FROM d WHERE prev > 0),
r AS (SELECT *, prev * (1 - band) AS raw FROM b),
t AS (SELECT *, CASE WHEN board <> 'HOSE' THEN 100 WHEN raw < 10000 THEN 10 WHEN raw < 50000 THEN 50 ELSE 100 END
                AS step FROM r),
f AS (SELECT *, close <= ceil(round(raw / step, 6)) * step AND close >= prev * (1 - band - 0.01) AS is_floor FROM t),
g AS (SELECT *, sum(CASE WHEN is_floor THEN 0 ELSE 1 END) OVER (PARTITION BY symbol ORDER BY date) AS chain FROM f)
SELECT symbol, board, date, high = low AS locked, match_vol / adv AS ratio,
       least(row_number() OVER (PARTITION BY symbol, chain ORDER BY date), {MAX_CHAIN_POSITION}) AS position
FROM g WHERE is_floor AND n_adv = {ADV_SESSIONS} AND adv > 0 AND date >= ?
"""


@dataclass(frozen=True)
class FloorLiquidity:
    """Khối lượng khớp phiên giảm sàn so với bình quân 20 phiên trước, theo lịch sử.

    `by_position`: trung vị theo sàn và vị trí phiên trong chuỗi sàn (1, 2, 3, 4+), kèm số phiên và tỷ lệ khóa sàn.
    `scale`: hệ số riêng của mã = trung vị của mã / trung vị của sàn (mã có ≥ SCALE_MIN_SESSIONS phiên sàn).
    """
    by_position: pd.DataFrame
    scale: pd.Series
    since: date

    def ratio(self, symbol: str, board: str, position: int) -> float:
        pos = min(max(position, 1), MAX_CHAIN_POSITION)
        rows = self.by_position[(self.by_position["board"] == board) & (self.by_position["position"] == pos)]
        base = float(rows["median"].iloc[0]) if len(rows) else 0.0
        return base * float(self.scale.get(symbol, 1.0))


def floor_liquidity(wh: Warehouse, since: date = LIQUIDITY_SINCE) -> FloorLiquidity:
    sessions = wh.query(_FLOOR_SESSIONS, [since])
    by_position = (sessions.groupby(["board", "position"])
                   .agg(sessions=("ratio", "size"), locked_share=("locked", "mean"), median=("ratio", "median"))
                   .reset_index())
    board_median = sessions.groupby("board")["ratio"].median()
    per_symbol = sessions.groupby(["symbol", "board"])["ratio"].agg(["median", "size"]).reset_index()
    per_symbol = per_symbol[per_symbol["size"] >= SCALE_MIN_SESSIONS]
    base = per_symbol["board"].map(board_median)
    scale = (per_symbol["median"] / base.where(base > 0)).clip(*SCALE_CLIP).fillna(1.0)
    return FloorLiquidity(by_position, pd.Series(scale.values, index=per_symbol["symbol"]), since)


def quotes(wh: Warehouse, symbols: list[str]) -> pd.DataFrame:
    """Giá đóng cửa phiên cuối, sàn, ngành và khối lượng khớp bình quân 20 phiên của từng mã (theo mã)."""
    if not symbols:
        return pd.DataFrame(columns=["symbol", "name", "board", "icb_code", "icb_name", "date", "price", "ref", "adv"]
                            ).set_index("symbol")
    # Khối lượng bình quân theo 20 phiên của thị trường: SSI không trả dòng cho phiên mã không có giao dịch, nên phiên
    # thiếu được tính là 0 (lấy bình quân theo số dòng sẽ thổi phồng thanh khoản của mã ít giao dịch).
    frame = wh.query(f"""
        WITH cal AS (SELECT date FROM index_daily WHERE symbol = 'VNINDEX' ORDER BY date DESC LIMIT {ADV_SESSIONS}),
             last AS (
            -- Chỉ đến phiên cuối của VN-Index: nếu kho có một phiên SSI mới công bố cho một phần mã, mọi mã vẫn được
            -- định giá cùng một phiên.
            SELECT symbol, date, close, avg, row_number() OVER (PARTITION BY symbol ORDER BY date DESC) AS k
            FROM daily WHERE symbol IN ? AND date <= (SELECT max(date) FROM cal)),
             vol AS (SELECT symbol, sum(coalesce(match_vol, 0)) / {ADV_SESSIONS} AS adv FROM daily
                     WHERE symbol IN ? AND date IN (SELECT date FROM cal) GROUP BY symbol)
        SELECT l.symbol, s.name, upper(s.board) AS board, s.icb_code, s.icb_name, l.date, l.close AS price,
               l.avg AS last_avg, coalesce(v.adv, 0) AS adv
        FROM last l JOIN securities s USING (symbol) LEFT JOIN vol v USING (symbol)
        WHERE l.k = 1
    """, [list(symbols), list(symbols)])
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    # Giá tham chiếu phiên kế tiếp: UPCoM = giá bình quân phiên cuối làm tròn bước giá 100 đồng (đối chiếu masterdata
    # 13/11/2026: khớp 99,7%), HOSE và HNX = giá đóng cửa.
    upcom = (frame["board"] == "UPCOM") & frame["last_avg"].notna()
    frame["ref"] = frame["price"].where(~upcom, (frame["last_avg"] / 100).round() * 100)
    return frame.drop(columns="last_avg").set_index("symbol")


def known_symbols(wh: Warehouse) -> pd.DataFrame:
    """Danh sách mã cho ô chọn mã: mã, tên, sàn, ngành."""
    return wh.query("SELECT symbol, name, upper(board) AS board, icb_name FROM securities ORDER BY symbol")


def drawdown_window(wh: Warehouse, year: int = 2022, index: str = "VNINDEX") -> tuple[date, date]:
    """Từ đỉnh tới đáy của chỉ số trong năm: đáy là phiên đóng cửa thấp nhất, đỉnh là phiên cao nhất trước đáy."""
    rows = wh.rows("""
        WITH y AS (SELECT date, close FROM index_daily WHERE symbol = ? AND year(date) = ?),
             low AS (SELECT date FROM y ORDER BY close, date LIMIT 1)
        SELECT (SELECT date FROM y WHERE date <= (SELECT date FROM low) ORDER BY close DESC, date LIMIT 1),
               (SELECT date FROM low)
    """, [index, year])
    if not rows or rows[0][0] is None:
        raise LookupError(f"Kho không có {index} năm {year}")
    return rows[0][0], rows[0][1]


def historical_path(wh: Warehouse, symbols: list[str], start: date, end: date,
                    index: str = "VNINDEX") -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Lợi suất ngày và khối lượng khớp so với bình quân 20 phiên trước của từng mã trong giai đoạn (start, end].

    Lịch là các phiên của chỉ số. SSI không trả dòng cho phiên mã không giao dịch: phiên đó giá đứng yên (lợi suất 0)
    và khối lượng 0, bình quân 20 phiên cũng tính các phiên 0 đó. Chỉ mã chưa niêm yết ở ngày đầu giai đoạn mới thay
    bằng lợi suất của chỉ số (tỷ lệ khối lượng 1) và được báo lại.
    """
    idx = wh.query("""
        SELECT date, close FROM index_daily WHERE symbol = ? AND date BETWEEN ? - INTERVAL 60 DAY AND ? ORDER BY date
    """, [index, start, end]).set_index("date")["close"]
    cal = idx.index
    data = wh.query("""
        SELECT symbol, date, close, coalesce(match_vol, 0) AS vol FROM daily
        WHERE symbol IN ? AND date BETWEEN ? - INTERVAL 400 DAY AND ?
    """, [list(symbols), start, end])
    close = data.pivot(index="date", columns="symbol", values="close").sort_index()
    close = close.reindex(close.index.union(cal)).ffill().reindex(cal)
    vol = data.pivot(index="date", columns="symbol", values="vol").reindex(cal).fillna(0.0)
    adv = vol.rolling(ADV_SESSIONS, min_periods=ADV_SESSIONS).mean().shift(1)
    ratio = (vol / adv.where(adv > 0)).fillna(0.0)
    window = pd.to_datetime(pd.Series(cal)).dt.date.gt(start).to_numpy()
    returns = close.pct_change(fill_method=None)[window].copy()
    volume = ratio[window].copy()
    idx_ret = idx.pct_change()[window]
    start_px = close[pd.to_datetime(pd.Series(cal)).dt.date.le(start).to_numpy()].iloc[-1:] if len(close) else close
    replaced = []
    for symbol in symbols:
        listed = symbol in close and len(start_px) and pd.notna(start_px[symbol].iloc[0])
        if not listed:
            replaced.append(symbol)
            returns[symbol] = idx_ret
            volume[symbol] = 1.0
    return returns[list(symbols)].fillna(0.0), volume[list(symbols)], replaced
