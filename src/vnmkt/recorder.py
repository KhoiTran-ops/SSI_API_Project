"""Ghi dữ liệu thời gian thực (WebSocket) vào kho Parquet: khớp lệnh, ba bước giá, khối ngoại, chỉ số.

Dùng chung tiến trình và token với REST (mỗi khóa chỉ có một phiên đăng nhập). Token hết hạn thì đăng nhập lại bằng
khóa, không dùng refresh token. Chỉ đăng ký dữ liệu thị trường, không đăng ký kênh giao dịch.

Định dạng theo phản hồi thật ngày 23/11/2026 (ssi-sdk 3.3.2): TradeMessage và QuoteMessage có giá tính bằng đồng
(SDK đã đổi, khác tài liệu ghi nghìn đồng ở topic quote); IndexSummaryMessage giữ nguyên dict gốc với giá trị là chuỗi.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from ssi_sdk.client import Stream

from .config import VN_TZ
from .ssi import MarketData
from .store import TickWriter, compact_ticks, num

log = logging.getLogger(__name__)

QUOTE_LEVELS = 3   # HOSE/HNX hiển thị ba bước giá tốt nhất
FLUSH_SECONDS = 30


def _time(text) -> datetime | None:
    """'2026/11/23 14:12:45.991' hoặc '2026/11/23 14:12:45' (giờ Việt Nam) -> datetime không kèm múi giờ."""
    if not text:
        return None
    for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(str(text), fmt)
        except ValueError:
            continue
    return None


def normalize(msg) -> tuple[str, dict] | None:
    """Thông điệp của SDK -> (topic, bản ghi phẳng). Thông điệp xác nhận đăng ký hoặc không nhận ra -> None."""
    kind = type(msg).__name__
    if kind == "TradeMessage":
        return "trade", {"symbol": msg.symbol, "time": _time(msg.trading_time), "price": num(msg.price),
                         "quantity": num(msg.quantity), "side": getattr(msg, "side", None),
                         "total_volume": num(msg.total_volume), "open": num(msg.open), "high": num(msg.high),
                         "low": num(msg.low), "avg": num(msg.avg)}
    if kind == "QuoteMessage":
        rec = {"symbol": msg.symbol, "time": _time(msg.trading_time)}
        for side, prices, volumes in (("bid", msg.bid_prices, msg.bid_volumes), ("ask", msg.ask_prices, msg.ask_volumes)):
            for i in range(QUOTE_LEVELS):
                rec[f"{side}{i + 1}"] = num(prices[i]) if i < len(prices or []) else None
                rec[f"{side}{i + 1}_vol"] = num(volumes[i]) if i < len(volumes or []) else None
        return "quote", rec
    if kind == "IndexSummaryMessage":
        d = msg.data if isinstance(msg.data, dict) else {}
        return "index", {"index": d.get("s") or msg.index, "time": _time(d.get("t")), "value": num(d.get("v")),
                         "change": num(d.get("c")), "change_pct": num(d.get("cr")), "advances": num(d.get("adv")),
                         "declines": num(d.get("dec")), "unchanged": num(d.get("nc")), "ceiling": num(d.get("ce")),
                         "floor": num(d.get("fl")), "match_val": num(d.get("tmv")), "match_vol": num(d.get("tmq")),
                         "total_val": num(d.get("tv")), "total_vol": num(d.get("tq"))}
    if kind == "RoomMessage":  # chưa thấy phản hồi thật trong giờ giao dịch: giữ mọi trường đơn giản để xem sau
        rec = {k: v for k, v in vars(msg).items() if isinstance(v, (str, int, float, type(None)))}
        return "room", rec
    return None


class Recorder:
    """Đăng ký các mã và chỉ số, ghi theo lô; gọi `run(seconds)` để chạy, Ctrl+C để dừng sớm."""

    def __init__(self, md: MarketData, writer: TickWriter, symbols: list[str], indices: list[str]):
        self.md, self.writer = md, writer
        self.symbols, self.indices = sorted(set(symbols)), sorted(set(indices))
        self.counts: dict[str, int] = {}
        self.skipped = 0
        self._lock = threading.Lock()

    def _on_data(self, msg) -> None:
        try:
            item = normalize(msg)
        except (AttributeError, TypeError, ValueError):
            item = None
        with self._lock:
            if item is None:
                self.skipped += 1
                return
            topic, rec = item
            self.writer.add(topic, rec)
            self.counts[topic] = self.counts.get(topic, 0) + 1

    def run(self, seconds: float) -> dict[str, int]:
        stream = Stream(self.md.auth)
        svc = stream.streaming

        def fresh_token(force_refresh: bool) -> str:
            if force_refresh or self.md.auth.token_manager.is_token_expired:
                self.md.relogin()
            return self.md.auth.token_manager.access_token

        svc._ws._token_provider = fresh_token
        svc.connect()
        svc.on_data = self._on_data
        try:
            if self.symbols:
                svc.subscribe_symbol_trade(self.symbols)
                svc.subscribe_symbol_quote(self.symbols)
                svc.subscribe_symbol_room(self.symbols)
            if self.indices:
                svc.subscribe_index_summary(self.indices)
            stop = time.monotonic() + seconds
            while time.monotonic() < stop:
                time.sleep(min(FLUSH_SECONDS, max(stop - time.monotonic(), 0)))
                with self._lock:
                    self.writer.flush()
                log.info("Đã ghi: %s", self.counts)
        except KeyboardInterrupt:
            log.info("Dừng theo yêu cầu")
        finally:
            svc.disconnect()
            with self._lock:
                self.writer.flush()
        compact_ticks(self.writer.root.parent, datetime.now(VN_TZ).date())
        return dict(self.counts)
