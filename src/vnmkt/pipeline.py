"""Tải dữ liệu từ SSI vào kho: nạp lịch sử từ khi niêm yết và cập nhật sau mỗi phiên.

securitiesSummary phân trang sai khi khoảng ngày dài (trùng dòng, mất cả năm: đã kiểm tra với VNINDEX
2000–2026), nên mỗi lượt gọi chỉ hỏi một đoạn ≤ CHUNK_DAYS ngày lịch (< 1000 phiên, vừa một trang). Lịch sử
được lấy lùi từng đoạn tới khi gặp đoạn không có giá (trước ngày niêm yết); mã đã đủ được ghi vào bảng
`coverage`, nên chạy lại sẽ tiếp tục từ chỗ dở. Cập nhật lấy lại từ ngày cuối đã lưu; nếu giá ngày trùng đó
khác bản đã lưu thì SSI đã điều chỉnh lùi lịch sử (cổ tức, thưởng cổ phiếu...) và mã đó được tải lại toàn bộ.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

import pandas as pd

from . import store
from .config import BOARD, BOARDS, VN_TZ
from .ssi import MarketData, SsiError, rows_of

log = logging.getLogger(__name__)

HISTORY_START = date(2000, 7, 28)   # phiên đầu tiên của HOSE
CHUNK_DAYS = 1000                   # ~690 phiên: một trang của securitiesSummary
INDICES = ("VNINDEX", "VN30", "VN100")
BOARD_HISTORY_SESSIONS = 252        # indexSummary chỉ lấy theo từng ngày, chỉ dùng để đối chiếu
ACTIONS_START = date(2017, 12, 1)   # sự kiện quyền cần cho lớp phân tích (từ 2018) để tách cổ tức tiền mặt
ACTION_WINDOW_DAYS = 5              # corporateActions giới hạn khoảng ngày tối đa 5 ngày
ACTION_REFRESH_DAYS = 30            # cửa sổ gần đây được lấy lại vì sự kiện mới công bố sau
ADJUST_TOLERANCE = 1e-4             # sai lệch tương đối của giá đóng cửa coi là đã điều chỉnh
MAX_CONSECUTIVE_FAILURES = 3        # dừng thay vì gọi dồn khi SSI đang lỗi
SESSION_DATA_READY = time(15, 30)   # HOSE đóng cửa 14:45; trước mốc này không lấy phiên hôm nay


def fmt(day: date) -> str:
    return day.strftime("%Y/%m/%d")


def stocks(rows: list[dict]) -> pd.DataFrame:
    """Chỉ giữ cổ phiếu của ba sàn (bỏ ETF, chứng quyền, trái phiếu, phái sinh)."""
    frame = store.securities_frame(rows)
    return frame[(frame["stock_type"] == "Stock") & frame["board"].isin(BOARDS)]


def last_close(at: datetime | None = None) -> date:
    """Ngày lịch muộn nhất có thể đã có dữ liệu cuối phiên; trước giờ chốt thì phiên hôm nay còn dở."""
    at = at or datetime.now(VN_TZ)
    return at.date() if at.time() >= SESSION_DATA_READY else at.date() - timedelta(days=1)


def action_windows(start: date, end: date):
    """Lưới cửa sổ 5 ngày cố định tính từ ACTIONS_START, để biết cửa sổ nào đã lấy."""
    day = ACTIONS_START + timedelta(days=max((start - ACTIONS_START).days, 0) // ACTION_WINDOW_DAYS * ACTION_WINDOW_DAYS)
    while day <= end:
        yield day, min(day + timedelta(days=ACTION_WINDOW_DAYS - 1), end)
        day += timedelta(days=ACTION_WINDOW_DAYS)


class Pipeline:
    def __init__(self, md: MarketData, wh: store.Warehouse, end: date | None = None):
        self.md, self.wh = md, wh
        self.end = end or last_close()
        self.failures: list[str] = []
        self._streak = 0

    def _guard(self, label: str, call):
        """Gọi một bước; ghi nhận lỗi và dừng hẳn khi lỗi liên tiếp."""
        try:
            result = call()
        except SsiError as exc:
            self.failures.append(f"{label}: {exc}")
            log.warning("%s: %s", label, exc)
            self._streak += 1
            if self._streak >= MAX_CONSECUTIVE_FAILURES:
                raise SsiError(f"Dừng sau {MAX_CONSECUTIVE_FAILURES} lỗi liên tiếp; lần cuối: {exc}") from None
            return None
        self._streak = 0
        return result

    # ---- danh mục mã ----
    def universe(self) -> list[str]:
        frame = stocks([r for board in BOARDS for r in self.md.securities(board)]).drop_duplicates("symbol")
        self.wh.upsert("securities", frame)
        log.info("Danh mục: %s", frame["board"].value_counts().to_dict())
        return sorted(frame["symbol"])

    # ---- giá ngày ----
    def _history(self, symbol: str, is_index: bool, until: date, since: date | None = None) -> pd.DataFrame | None:
        """Lấy lùi từng đoạn từ `until`. Không có `since`: dừng ở đoạn không có giá (trước niêm yết) hoặc
        HISTORY_START. Lỗi ở bất kỳ đoạn nào -> None (không ghi dở dang)."""
        key = {"index": symbol} if is_index else {"symbol": symbol}
        floor = since or HISTORY_START
        collected, stop = [], until
        while stop >= floor:
            start = max(stop - timedelta(days=CHUNK_DAYS - 1), floor)
            rows = self._guard(f"{symbol} {start}", lambda: self.md.summary(fmt(start), fmt(stop), **key))
            if rows is None:
                return None
            rows = [r for r in rows if str(r.get("symbol", "")).upper() == symbol]
            if since is None and store.daily_frame(rows).empty:
                break
            collected.extend(rows)
            stop = start - timedelta(days=1)
        return store.daily_frame(collected)

    def _complete(self, symbol: str, first: date | None) -> None:
        self.wh.upsert("coverage", pd.DataFrame({"symbol": [symbol], "complete_from": [first]}))

    def _full(self, symbol: str, table: str, is_index: bool) -> int | None:
        frame = self._history(symbol, is_index, self.end)
        if frame is None:
            return None
        n = self.wh.replace_symbol(table, symbol, frame)
        self._complete(symbol, frame["date"].min() if n else None)
        return n

    def _backfill(self, symbol: str, first: date, table: str, is_index: bool) -> int | None:
        """Bổ sung phần lịch sử trước ngày đầu tiên đã lưu (kho cũ chỉ có 3 năm)."""
        frame = self._history(symbol, is_index, first - timedelta(days=1))
        if frame is None:
            return None
        n = self.wh.upsert(table, frame)
        self._complete(symbol, frame["date"].min() if n else first)
        return n

    def _incremental(self, symbol: str, last: date, table: str, is_index: bool) -> int | None:
        frame = self._history(symbol, is_index, self.end, since=last)
        if frame is None:
            return None
        stored = self.wh.scalar(f"SELECT close FROM {table} WHERE symbol = ? AND date = ?", [symbol, last])
        overlap = frame.loc[frame["date"] == last, "close"]
        if stored and not overlap.empty and abs(overlap.iloc[0] / stored - 1) > ADJUST_TOLERANCE:
            log.info("%s: giá lịch sử đã được điều chỉnh, tải lại toàn bộ", symbol)
            return self._full(symbol, table, is_index)
        return self.wh.upsert(table, frame)

    def prices(self, symbols: list[str], table: str = "daily", is_index: bool = False, full: bool = False,
               upto: date | None = None) -> int:
        """`upto`: phiên mới nhất đã biết; mã đã có phiên này và đủ lịch sử thì không gọi."""
        spans = {} if full else {s: (first, last) for s, first, last in self.wh.rows(
            f"SELECT symbol, min(date), max(date) FROM {table} GROUP BY symbol")}
        complete = set() if full else {s for (s,) in self.wh.rows("SELECT symbol FROM coverage")}
        checked = {} if full else dict(self.wh.rows("SELECT symbol, through FROM checked"))
        upto = upto or self.end
        total, done = 0, []
        try:
            for i, symbol in enumerate(symbols, 1):
                if symbol in complete and checked.get(symbol, date.min) >= upto:
                    continue  # đã hỏi tới phiên mới nhất (kể cả khi mã không có giao dịch)
                if symbol not in spans:
                    n = self._full(symbol, table, is_index)
                else:
                    first, last = spans[symbol]
                    n = 0 if symbol in complete else self._backfill(symbol, first, table, is_index)
                    if n is not None and last < upto:
                        n = self._incremental(symbol, last, table, is_index)
                if n is not None:
                    done.append(symbol)
                total += n or 0
                if i % 100 == 0:
                    log.info("%s: %d/%d mã", table, i, len(symbols))
        finally:  # vẫn ghi phần đã hỏi xong nếu lần chạy dừng giữa chừng
            # Chỉ ghi "đã hỏi tới" đến phiên VN-Index đã có trong kho: nếu SSI chưa công bố phiên mới (chạy ngay sau
            # 15:30), lần chạy sau phải hỏi lại, không được coi là "mã không có giao dịch" (lỗi gặp 23/11/2026).
            published = self.wh.scalar("SELECT max(date) FROM index_daily WHERE symbol = 'VNINDEX'")
            through = min(self.end, published) if published else self.end
            self.wh.upsert("checked", pd.DataFrame({"symbol": done, "through": through}))
        return total

    # ---- tổng hợp sàn (độ rộng chính thức, tự doanh, khối ngoại) ----
    def board(self, sessions: int = BOARD_HISTORY_SESSIONS) -> int:
        calendar = [d for (d,) in self.wh.rows(
            "SELECT date FROM index_daily WHERE symbol = 'VNINDEX' ORDER BY date DESC LIMIT ?", [sessions])]
        have = {d for (d,) in self.wh.rows("SELECT date FROM board_daily WHERE board = ?", [BOARD])}
        total = 0
        for day in sorted(d for d in calendar if d not in have):
            rows = self._guard(f"indexSummary {day}", lambda: self.md.board_summary(BOARD, fmt(day)))
            if rows:
                total += self.wh.upsert("board_daily", store.board_frame(BOARD, rows))
        return total

    # ---- sự kiện quyền và giá trần/sàn ----
    def actions(self) -> int:
        """Lấy các cửa sổ 5 ngày còn thiếu từ ACTIONS_START, và lấy lại các cửa sổ gần đây."""
        have = {d for (d,) in self.wh.rows("SELECT start FROM action_windows")}
        recent = self.end - timedelta(days=ACTION_REFRESH_DAYS)
        total = 0
        for start, stop in action_windows(ACTIONS_START, self.end):
            if start in have and stop < recent:
                continue
            rows = self._guard(f"corporateActions {start}", lambda: self.md.corporate_actions(fmt(start), fmt(stop)))
            if rows is None:
                continue
            total += self.wh.upsert("corporate_actions", store.actions_frame(rows))
            self.wh.upsert("action_windows", pd.DataFrame({"start": [start], "stop": [stop]}))
        return total

    def master(self, session: date, symbols: list[str]) -> int:
        """Trần/sàn/tham chiếu của một phiên. masterdata trả cả chứng quyền, trái phiếu nên lọc theo danh mục."""
        if self.wh.scalar("SELECT count(*) FROM master WHERE date = ?", [session]):
            return 0
        rows = self._guard("masterdata", lambda: self.md.master_data(fmt(session), fmt(session)))
        if not rows:
            return 0
        frame = store.master_frame(rows)
        return self.wh.upsert("master", frame[frame["symbol"].isin(symbols)])

    def session(self) -> date:
        """Phiên giao dịch mới nhất trong kho theo VNINDEX (ngày nghỉ không có dòng)."""
        return self.wh.scalar("SELECT max(date) FROM index_daily WHERE symbol = 'VNINDEX'") or self.end

    # ---- quy trình ----
    def run(self, full: bool = False) -> dict:
        result = {
            "securities": len(symbols := self.universe()),
            "index_daily": self.prices(list(INDICES), "index_daily", is_index=True, full=full),
        }
        session = self.session()
        # Không lấy phiên mà SSI chưa công bố đủ (chưa có VN-Index): mã bị tải lại toàn bộ cũng chỉ lấy đến phiên này,
        # tránh kho lẫn một phiên có dữ liệu cho một số mã (đã gặp 23/11/2026: chạy lúc 15:31, 132 mã có phiên mới).
        self.end = min(self.end, session)
        result["daily"] = self.prices(symbols, full=full, upto=session)
        result["board_daily"] = self.board()
        result["corporate_actions"] = self.actions()
        result["master"] = self.master(session, symbols)
        return result


def record_run(wh: store.Warehouse, started: datetime, status: str, calls: int, failures: list[str]) -> None:
    """Nhật ký mỗi lần cập nhật, để giao diện hiển thị trạng thái kết nối và lần cập nhật cuối."""
    session = wh.scalar("SELECT max(date) FROM index_daily WHERE symbol = 'VNINDEX'")
    wh.upsert("runs", pd.DataFrame({
        "started_at": [started.replace(tzinfo=None)], "finished_at": [store.now().replace(tzinfo=None)],
        "status": [status], "calls": [calls], "failures": ["\n".join(failures[:50]) or None], "session": [session]}))


def rebuild(raw: store.RawLog, wh: store.Warehouse) -> dict:
    """Dựng lại toàn bộ kho từ phản hồi gốc, không gọi SSI (dùng khi đổi quy tắc chuẩn hóa).

    Phát lại đúng thứ tự đã lấy và ghi đè theo khóa, nên lần tải lại sau điều chỉnh giá ghi đè bản cũ như lúc
    chạy thật. Trạng thái thu thập (coverage, action_windows) cũng được dựng lại để lần cập nhật sau không tải
    lại. Giữ nguyên thời điểm lấy gốc. Nhật ký `runs` không có trong phản hồi gốc nên được giữ nguyên.
    """
    runs = wh.query("SELECT * FROM runs")
    wh.reset()
    wh.upsert("runs", runs)
    symbols: set[str] = set()
    counts: dict[str, int] = {}
    checked: dict[str, tuple] = {}  # mã -> (ngày "to" lớn nhất đã hỏi, thời điểm lấy)
    for rec in raw.replay(order=("securitiesByBoard",)):
        endpoint, params, at = rec["path"].rsplit("/", 1)[-1], rec["params"], rec["fetched_at"]
        try:
            rows = rows_of(rec["payload"])
        except SsiError:
            continue
        covered = None
        if endpoint == "securitiesByBoard":
            frame = stocks(rows)
            symbols.update(frame["symbol"])
            table = "securities"
        elif endpoint == "securitiesSummary":
            table = "index_daily" if "index" in params else "daily"
            key = str(params.get("index") or params.get("symbol")).upper()
            frame = store.daily_frame(rows)
            frame = frame[frame["symbol"] == key]
            start, stop = store.parse_day(params["from"]), store.parse_day(params["to"])
            if stop > checked.get(key, (date.min,))[0]:
                checked[key] = (stop, at)
            # Đoạn lấy lùi đủ CHUNK_DAYS mà rỗng = đã tới trước ngày niêm yết (lượt cập nhật tăng dần ngắn hơn).
            if start <= HISTORY_START or (frame.empty and (stop - start).days == CHUNK_DAYS - 1):
                covered = key
        elif endpoint == "indexSummary":
            table, frame = "board_daily", store.board_frame(params["board"], rows)
        elif endpoint == "corporateActions":
            table, frame = "corporate_actions", store.actions_frame(rows)
            if params.get("pageIndex", 1) == 1:
                wh.upsert("action_windows", pd.DataFrame({"start": [store.parse_day(params["from"])],
                                                          "stop": [store.parse_day(params["to"])]}), at)
        elif endpoint == "masterdata":
            frame = store.master_frame(rows)
            table, frame = "master", frame[frame["symbol"].isin(symbols)]
        else:
            continue
        counts[table] = counts.get(table, 0) + wh.upsert(table, frame, at)
        if covered:
            first = wh.scalar(f"SELECT min(date) FROM {table} WHERE symbol = ?", [covered])
            wh.upsert("coverage", pd.DataFrame({"symbol": [covered], "complete_from": [first]}), at)
    for symbol, (through, at) in checked.items():
        wh.upsert("checked", pd.DataFrame({"symbol": [symbol], "through": [through]}), at)
    return counts


def quality(wh: store.Warehouse) -> pd.DataFrame:
    """Báo cáo chất lượng theo mã: số dòng, khoảng ngày, phiên thiếu, ô thiếu.

    Lịch giao dịch = mọi ngày có ít nhất một mã giao dịch (SSI không có VNINDEX 2003–2008 nên không dùng VNINDEX).
    """
    return wh.query("""
        WITH cal AS (SELECT DISTINCT date FROM daily),
             span AS (SELECT symbol, min(date) AS first, max(date) AS last, count(*) AS rows,
                             count(*) FILTER (WHERE buy_vol IS NULL AND match_vol > 0) AS missing_orders
                      FROM daily GROUP BY symbol)
        SELECT s.symbol, sec.board, s.first, s.last, s.rows, s.missing_orders,
               (SELECT count(*) FROM cal WHERE cal.date BETWEEN s.first AND s.last) - s.rows AS missing_sessions,
               c.symbol IS NOT NULL AS history_complete
        FROM span s LEFT JOIN securities sec USING (symbol) LEFT JOIN coverage c USING (symbol)
        ORDER BY missing_sessions DESC, s.symbol
    """)
