"""Lưu trữ: phản hồi gốc (jsonl.gz) và kho Parquet đã chuẩn hóa, truy vấn bằng DuckDB.

Phản hồi gốc được giữ để tái lập kết quả, vì SSI điều chỉnh lại giá lịch sử sau mỗi sự kiện quyền.
Không ghi token hay khóa vào bất kỳ tệp nào.

Bố cục kho (`data/lake`):
  <bảng>/part-0.parquet            bảng nhỏ
  daily/year=YYYY/*.parquet        giá ngày, chia theo năm
  ticks/date=YYYY-MM-DD/topic=<t>/ dữ liệu thời gian thực, mỗi lần ghi một tệp; `compact_ticks` gộp lại
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import time
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from .config import VN_TZ

# Ánh xạ trường SSI -> cột trong kho. Thứ tự cột theo thứ tự khai báo.
DAILY_FIELDS = {
    "open": "open", "high": "high", "low": "low", "close": "close", "average": "avg",
    "priceChange": "change", "priceChangePercentage": "pct",
    "totalMatch": "match_vol", "totalMatchValue": "match_val",
    "totalBuy": "buy_orders", "totalTradeBuy": "buy_vol", "totalSell": "sell_orders", "totalTradeSell": "sell_vol",
    "totalForeignBuy": "f_buy_vol", "totalForeignBuyValue": "f_buy_val",
    "totalForeignSell": "f_sell_vol", "totalForeignSellValue": "f_sell_val",
    "remainForeignRoom": "room_remain", "totalForeignRoom": "room_total",
    "totalDeal": "deal_vol", "totalDealValue": "deal_val",
}
ORDER_COLS = ["buy_orders", "buy_vol", "sell_orders", "sell_vol"]
PRICE_COLS = ["open", "high", "low", "close", "avg"]
BOARD_FIELDS = {
    "indexValue": "index_value", "indexChangePercentage": "index_pct",
    "totalMatchValue": "match_val", "totalDealValue": "deal_val",
    "totalAdvanceStock": "advances", "totalDeclineStock": "declines", "totalNoChangeStock": "unchanged",
    "totalCeilingStock": "ceiling", "totalFloorStock": "floor",
    "totalPropBuyValue": "prop_buy_val", "totalPropSellValue": "prop_sell_val",
    # totalBuyForeignValue/totalSellForeignValue luôn là "0" (đã kiểm tra trên dữ liệu thật) nên không dùng;
    # giá trị ròng lấy từ netPurchasesForeignValue.
    "totalBuyForeign": "f_buy_vol", "totalSellForeign": "f_sell_vol", "netPurchasesForeignValue": "f_net_val",
}

# coverage: mã đã có đủ lịch sử từ khi niêm yết; checked: đã hỏi SSI giá của mã tới ngày nào (mã không giao dịch
# vẫn được tính là đã hỏi, nên chạy lại không gọi lại); action_windows: cửa sổ sự kiện quyền đã lấy;
# runs: nhật ký mỗi lần cập nhật (trạng thái kết nối hiển thị trên giao diện).
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS securities (symbol VARCHAR PRIMARY KEY, name VARCHAR, board VARCHAR,
    icb_code VARCHAR, icb_name VARCHAR, listed_share DOUBLE, stock_type VARCHAR, fetched_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS daily (symbol VARCHAR, date DATE,
    {", ".join(f"{c} DOUBLE" for c in DAILY_FIELDS.values())}, fetched_at TIMESTAMP, PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS index_daily (symbol VARCHAR, date DATE,
    {", ".join(f"{c} DOUBLE" for c in DAILY_FIELDS.values())}, fetched_at TIMESTAMP, PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS board_daily (board VARCHAR, date DATE,
    {", ".join(f"{c} DOUBLE" for c in BOARD_FIELDS.values())}, fetched_at TIMESTAMP, PRIMARY KEY (board, date));
CREATE TABLE IF NOT EXISTS corporate_actions (symbol VARCHAR, exright_date DATE, title VARCHAR, ratio DOUBLE,
    cash_dividend DOUBLE, exchange VARCHAR, fetched_at TIMESTAMP, PRIMARY KEY (symbol, exright_date, title));
CREATE TABLE IF NOT EXISTS master (symbol VARCHAR, date DATE, ceiling DOUBLE, floor DOUBLE, ref DOUBLE,
    fetched_at TIMESTAMP, PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS coverage (symbol VARCHAR PRIMARY KEY, complete_from DATE, fetched_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS checked (symbol VARCHAR PRIMARY KEY, through DATE, fetched_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS action_windows (start DATE PRIMARY KEY, stop DATE, fetched_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS runs (started_at TIMESTAMP PRIMARY KEY, finished_at TIMESTAMP, status VARCHAR,
    calls INTEGER, failures VARCHAR, session DATE);
"""


def now() -> datetime:
    return datetime.now(VN_TZ).replace(microsecond=0)


def num(value) -> float | None:
    """Chuỗi số của SSI -> float; rỗng, null hoặc không phải số -> None (không đoán thành 0)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_day(value) -> date | None:
    """'2026/11/06' hoặc '2026/11/06 14:45:00' -> date."""
    try:
        return datetime.strptime(str(value)[:10], "%Y/%m/%d").date()
    except ValueError:
        return None


class RawLog:
    """Ghi nguyên văn phản hồi API vào data/raw/<ngày lấy>/<endpoint>.jsonl.gz."""

    def __init__(self, root: Path):
        self.root = root

    def __call__(self, path: str, params: dict, payload) -> None:
        stamp = now()
        folder = self.root / stamp.date().isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        record = {"fetched_at": stamp.isoformat(), "path": path, "params": params, "payload": payload}
        with gzip.open(folder / f"{path.rsplit('/', 1)[-1]}.jsonl.gz", "at", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    def replay(self, order: tuple[str, ...] = ()):
        """Đọc tuần tự các bản ghi gốc: theo ngày lấy, trong ngày theo `order` của endpoint rồi theo tên;
        mỗi tệp vốn đã theo thứ tự ghi. fetched_at trả về dạng datetime."""
        rank = {name: i for i, name in enumerate(order)}
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            files = sorted(folder.glob("*.jsonl.gz"), key=lambda f: (rank.get(f.name.split(".")[0], len(rank)), f.name))
            for file in files:
                with gzip.open(file, "rt", encoding="utf-8") as src:
                    for line in src:
                        if line.strip():
                            rec = json.loads(line)
                            yield {**rec, "fetched_at": datetime.fromisoformat(rec["fetched_at"])}


def daily_frame(rows: list[dict], key: str = "symbol") -> pd.DataFrame:
    """Chuẩn hóa securitiesSummary; bỏ dòng thiếu mã/ngày, giữ dòng cuối nếu trùng (mã, ngày)."""
    records = []
    for r in rows:
        day = parse_day(r.get("tradingDate"))
        if not r.get(key) or day is None:
            continue
        rec = {"symbol": str(r[key]).upper(), "date": day}
        rec.update({col: num(r.get(field)) for field, col in DAILY_FIELDS.items()})
        # Phiên trước ngày niêm yết có close = 0 (ví dụ VPL): giá không dương là thiếu, không phải giá.
        for col in PRICE_COLS:
            if rec[col] is not None and rec[col] <= 0:
                rec[col] = None
        if rec["close"] is None:  # không có giá đóng cửa thì dòng không mang thông tin; bản gốc vẫn ở data/raw
            continue
        # SSI trả 0 cho dữ liệu đặt lệnh bị thiếu dù có khớp lệnh: coi là thiếu.
        if (rec["match_vol"] or 0) > 0:
            for col in ORDER_COLS:
                if rec[col] == 0:
                    rec[col] = None
        records.append(rec)
    cols = ["symbol", "date", *DAILY_FIELDS.values()]
    return pd.DataFrame(records, columns=cols).drop_duplicates(["symbol", "date"], keep="last")


def board_frame(board: str, rows: list[dict]) -> pd.DataFrame:
    records = []
    for r in rows:
        day = parse_day(r.get("tradingDate"))
        if day is not None:
            rec = {"board": board, "date": day, **{c: num(r.get(f)) for f, c in BOARD_FIELDS.items()}}
            if rec["f_buy_vol"] == 0 and rec["f_sell_vol"] == 0:  # cả sàn không thể không có giao dịch khối ngoại
                rec.update(f_buy_vol=None, f_sell_vol=None, f_net_val=None)
            records.append(rec)
    return pd.DataFrame(records, columns=["board", "date", *BOARD_FIELDS.values()]).drop_duplicates(["board", "date"])


def securities_frame(rows: list[dict]) -> pd.DataFrame:
    records = [{"symbol": str(r["symbol"]).upper(), "name": r.get("symbolNameVi"), "board": r.get("board"),
                "icb_code": r.get("icbCode") or None, "icb_name": r.get("icbName") or None,
                "listed_share": num(r.get("listedShare")), "stock_type": r.get("stockType")}
               for r in rows if r.get("symbol")]
    return pd.DataFrame(records, columns=["symbol", "name", "board", "icb_code", "icb_name", "listed_share", "stock_type"])


_CASH = re.compile(r"bằng tiền\D{0,10}?(\d[\d.,]*)\s*(%|đồng|đ\b|vnd)", re.IGNORECASE)
PAR_VALUE = 10_000  # mệnh giá cổ phiếu Việt Nam; cổ tức "x%" nghĩa là x% mệnh giá


def cash_dividend(title: str) -> float | None:
    """Cổ tức tiền mặt (đồng/cổ phiếu) đọc từ tiêu đề sự kiện, ví dụ "... bằng tiền 1000 đồng/cổ phiếu",
    "... bằng tiền 6000 VND/CP", "... bằng tiền 12%". Không đọc được thì None."""
    m = _CASH.search(title or "")
    if not m:
        return None
    value, unit = m.group(1), m.group(2).lower()
    if unit == "%":
        return float(value.replace(",", ".")) / 100 * PAR_VALUE
    return float(value.replace(".", "").replace(",", ""))  # dấu chấm/phẩy là phân cách hàng nghìn


def actions_frame(rows: list[dict]) -> pd.DataFrame:
    records = [{"symbol": str(r["symbol"]).upper(), "exright_date": parse_day(r.get("exrightDate")),
                "title": r.get("eventTitle") or "", "ratio": num(r.get("ratio")),
                "cash_dividend": cash_dividend(r.get("eventTitle")), "exchange": r.get("exchange") or None}
               for r in rows if r.get("symbol") and parse_day(r.get("exrightDate"))]
    return pd.DataFrame(records, columns=["symbol", "exright_date", "title", "ratio", "cash_dividend", "exchange"]
                        ).drop_duplicates(["symbol", "exright_date", "title"], keep="last")


def master_frame(rows: list[dict]) -> pd.DataFrame:
    records = [{"symbol": str(r["symbol"]).upper(), "date": parse_day(r.get("tradingDate")), "ceiling": num(r.get("ceiling")),
                "floor": num(r.get("floor")), "ref": num(r.get("refPrice"))}
               for r in rows if r.get("symbol") and parse_day(r.get("tradingDate"))]
    return pd.DataFrame(records, columns=["symbol", "date", "ceiling", "floor", "ref"]).drop_duplicates(["symbol", "date"])


TABLES = tuple(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA))
PARTITIONED = {"daily": "year"}  # bảng -> cột chia thư mục (tính từ cột date)
SORT_KEYS = {"daily": "symbol, date", "index_daily": "symbol, date", "board_daily": "board, date",
             "corporate_actions": "symbol, exright_date", "master": "symbol, date"}


def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _rename(src: Path, dst: Path, attempts: int = 10) -> None:
    """Đổi tên, thử lại khi Windows còn giữ tệp (tiến trình khác đang đọc đúng lúc đó)."""
    for i in range(attempts):
        try:
            src.rename(dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.2)


class WarehouseBusy(RuntimeError):
    """Một tiến trình khác đang ghi kho."""


class _WriteLock:
    """Khóa ghi theo tệp; hệ điều hành tự nhả khi tiến trình kết thúc, kể cả khi bị dừng đột ngột."""

    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self._file = open(root / ".write.lock", "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._file.close()
            raise WarehouseBusy(f"Kho {root} đang được một tiến trình khác ghi; chờ tiến trình đó xong.") from None

    def release(self) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            self._file.close()


def scan(root: Path, table: str) -> str | None:
    """Biểu thức FROM đọc một bảng từ kho Parquet; None nếu bảng chưa có tệp."""
    folder = root / table
    if table in PARTITIONED:
        if not any(folder.glob("*=*/*.parquet")):
            return None
        return (f"(SELECT * EXCLUDE ({PARTITIONED[table]}) FROM read_parquet("
                f"'{_sql_path(folder)}/*=*/*.parquet', hive_partitioning = true))")
    if not any(folder.glob("*.parquet")):
        return None
    return f"read_parquet('{_sql_path(folder)}/*.parquet')"


class Warehouse:
    """Kho Parquet trong thư mục `root`, truy vấn bằng DuckDB trong bộ nhớ.

    Chế độ ghi: nạp các bảng vào DuckDB (có khóa chính, nên mọi ghi là upsert và chạy lại không sinh trùng),
    và khi đóng thì ghi lại các bảng đã đổi. Mỗi bảng được ghi vào thư mục tạm rồi mới thay thư mục cũ, nên
    tiến trình đọc không bao giờ thấy bảng ghi dở. Chế độ chỉ đọc: chỉ tạo view lên tệp Parquet, không nạp gì.
    """

    def __init__(self, root: Path, read_only: bool = False):
        self.root, self.read_only = Path(root), read_only
        self._dirty: set[str] = set()
        self._lock = None if read_only else _WriteLock(self.root)
        if not read_only:
            self._recover()
        self.con = duckdb.connect(":memory:")
        for statement in filter(str.strip, SCHEMA.split(";")):
            self.con.execute(statement).fetchall()
        for table in TABLES:
            source = scan(self.root, table)
            if source is None:
                continue
            if read_only:
                self.con.execute(f"DROP TABLE {table}").fetchall()
                self.con.execute(f"CREATE VIEW {table} AS SELECT * FROM {source}").fetchall()
            else:
                self.con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM {source}").fetchall()

    def _recover(self) -> None:
        """Lần ghi trước dừng giữa lúc thay thư mục: khôi phục bảng cũ, bỏ bản ghi dở."""
        for table in TABLES:
            final, tmp, old = (self.root / table, self.root / f".{table}.tmp", self.root / f".{table}.old")
            if old.exists() and not final.exists():
                _rename(old, final)
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(old, ignore_errors=True)

    def close(self) -> None:
        try:
            if not self.read_only:
                self.flush()
        finally:
            self.con.close()
            if self._lock:
                self._lock.release()

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def flush(self) -> None:
        """Ghi các bảng đã đổi ra Parquet (zstd, sắp theo khóa để nén tốt và lọc nhanh)."""
        self.root.mkdir(parents=True, exist_ok=True)
        for table in sorted(self._dirty):
            final, tmp, old = (self.root / table, self.root / f".{table}.tmp", self.root / f".{table}.old")
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(old, ignore_errors=True)
            tmp.mkdir()
            order = f" ORDER BY {SORT_KEYS[table]}" if table in SORT_KEYS else ""
            options = "FORMAT parquet, COMPRESSION zstd"
            empty = not self.con.execute(f"SELECT count(*) FROM {table}").fetchall()[0][0]
            if table in PARTITIONED and not empty:
                part = PARTITIONED[table]
                self.con.execute(f"COPY (SELECT *, year(date) AS {part} FROM {table}{order}) "
                                 f"TO '{_sql_path(tmp)}' ({options}, PARTITION_BY ({part}))").fetchall()
            elif not (table in PARTITIONED and empty):
                self.con.execute(f"COPY (SELECT * FROM {table}{order}) TO '{_sql_path(tmp / 'part-0.parquet')}' "
                                 f"({options})").fetchall()
            if final.exists():
                _rename(final, old)
            try:
                _rename(tmp, final)
            except OSError:
                if old.exists() and not final.exists():
                    _rename(old, final)
                raise
            shutil.rmtree(old, ignore_errors=True)
        self._dirty.clear()

    def insert_sql(self, table: str, select_sql: str, params: list | None = None) -> int:
        """Chèn kết quả một câu SELECT (ví dụ đọc từ kho khác) vào bảng, đánh dấu bảng cần ghi lại."""
        self.con.execute(f"INSERT OR REPLACE INTO {table} BY NAME {select_sql}", params or []).fetchall()
        self._dirty.add(table)
        return self.scalar(f"SELECT count(*) FROM {table}")

    def reset(self) -> None:
        """Xóa dữ liệu mọi bảng (dùng khi dựng lại kho từ phản hồi gốc)."""
        for table in TABLES:
            self.con.execute(f"DELETE FROM {table}").fetchall()
        self._dirty.update(TABLES)

    def upsert(self, table: str, frame: pd.DataFrame, fetched_at: datetime | None = None) -> int:
        """Ghi đè theo khóa chính; `fetched_at` mặc định là bây giờ (giờ Việt Nam, lưu không kèm múi giờ)."""
        if frame.empty:
            return 0
        if table != "runs":
            frame = frame.assign(fetched_at=(fetched_at or now()).replace(tzinfo=None))
        cols = ", ".join(frame.columns)
        self.con.register("incoming", frame)
        try:
            self.con.execute(f"INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM incoming").fetchall()
        finally:
            self.con.unregister("incoming")
        self._dirty.add(table)
        return len(frame)

    def replace_symbol(self, table: str, symbol: str, frame: pd.DataFrame, fetched_at: datetime | None = None) -> int:
        """Thay toàn bộ lịch sử một mã (dùng khi giá quá khứ đã được điều chỉnh lại)."""
        self.con.execute(f"DELETE FROM {table} WHERE symbol = ?", [symbol]).fetchall()
        self._dirty.add(table)
        return self.upsert(table, frame, fetched_at)

    def scalar(self, sql: str, params: list | None = None):
        rows = self.rows(sql, params)  # đọc hết để giải phóng kết quả
        return rows[0][0] if rows else None

    def rows(self, sql: str, params: list | None = None) -> list[tuple]:
        """Kết quả dạng kiểu Python (date, float...), dùng khi so sánh trạng thái; .df() trả Timestamp."""
        return self.con.execute(sql, params or []).fetchall()

    def query(self, sql: str, params: list | None = None) -> pd.DataFrame:
        return self.con.execute(sql, params or []).df()


_TOPIC = re.compile(r"^[A-Za-z0-9_-]+$")


class TickWriter:
    """Ghi dữ liệu thời gian thực theo lô ra `ticks/date=.../topic=.../<giờ>.parquet`.

    Luồng WebSocket có thể tới hàng trăm thông điệp mỗi giây, nên gom trong bộ nhớ rồi ghi một tệp mỗi
    `max_rows` dòng hoặc khi gọi `flush` (bộ ghi luồng gọi theo chu kỳ và khi dừng). Không ghi token.
    """

    def __init__(self, root: Path, max_rows: int = 5_000):
        self.root, self.max_rows = Path(root) / "ticks", max_rows
        self._buffer: dict[tuple[date, str], list[dict]] = {}

    def add(self, topic: str, record: dict, at: datetime | None = None) -> None:
        if not _TOPIC.match(topic):
            raise ValueError(f"Tên topic không hợp lệ: {topic!r}")
        at = at or now()
        rows = self._buffer.setdefault((at.date(), topic), [])
        rows.append({"received_at": at.replace(tzinfo=None), **record})
        if len(rows) >= self.max_rows:
            self._write(at.date(), topic)

    def _write(self, day: date, topic: str) -> None:
        rows = self._buffer.pop((day, topic), None)
        if not rows:
            return
        folder = self.root / f"date={day.isoformat()}" / f"topic={topic}"
        folder.mkdir(parents=True, exist_ok=True)
        # Số thứ tự tăng dần ở đầu tên tệp: gộp theo tên là đúng thứ tự ghi (kể cả khi ghi qua nửa đêm).
        seqs = [int(f.name[:6]) for f in folder.glob("*.parquet") if f.name[:6].isdigit()]
        name = f"{max(seqs, default=-1) + 1:06d}-{now().strftime('%H%M%S')}.parquet"
        # Ghi ra tệp tạm rồi đổi tên: giao diện đọc song song không bao giờ gặp tệp ghi dở.
        tmp = folder / f"{name}.tmp"
        pd.DataFrame(rows).to_parquet(tmp, index=False, compression="zstd")
        os.replace(tmp, folder / name)

    def flush(self) -> None:
        for day, topic in list(self._buffer):
            self._write(day, topic)


def compact_ticks(root: Path, day: date) -> int:
    """Gộp các tệp nhỏ của một ngày thành một tệp mỗi topic; trả số topic đã gộp."""
    merged = 0
    for folder in sorted((Path(root) / "ticks" / f"date={day.isoformat()}").glob("topic=*")):
        files = sorted(folder.glob("*.parquet"))
        if len(files) < 2:
            continue
        tmp = folder / "compact.tmp"
        con = duckdb.connect(":memory:")
        try:
            # Tên tệp tăng dần theo lần ghi; giữ đúng thứ tự đến kể cả khi nhiều dòng cùng received_at.
            con.execute(f"COPY (SELECT * EXCLUDE (filename, file_row_number) FROM read_parquet("
                        f"'{_sql_path(folder)}/*.parquet', union_by_name = true, filename = true, "
                        f"file_row_number = true) ORDER BY filename, file_row_number) "
                        f"TO '{_sql_path(tmp)}' (FORMAT parquet, COMPRESSION zstd)").fetchall()
        finally:
            con.close()
        for file in files:
            file.unlink()
        tmp.rename(folder / "000000-day.parquet")
        merged += 1
    return merged
