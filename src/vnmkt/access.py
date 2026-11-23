"""Kiểm tra quyền của khóa SSI cho các tính năng tài khoản và thời gian thực.

Chỉ đọc. Không in số tài khoản, số dư, giá vốn hay token: chỉ in loại tiểu khoản, số bản ghi,
tên các trường có dữ liệu và thời hạn token.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from ssi_sdk.client import Stream

from .config import VN_TZ
from .ssi import MarketData, SsiError, rows_of

ACCOUNT_READS = ("/api/v3/trading/position", "/api/v3/trading/accountBalance", "/api/v3/trading/ppmmrAccount")
ORDER_WINDOWS_DAYS = (7, 30, 90, 365, 730, 1095)
WS_SYMBOLS = ["SSI", "HPG", "VCB"]


def mask(account_no: str) -> str:
    """'003C123456' -> '******3456'... chỉ giữ ký tự cuối để phân biệt tiểu khoản."""
    text = str(account_no or "")
    return "*" * max(len(text) - 1, 0) + text[-1:]


def filled_fields(payload) -> list[str]:
    """Tên các trường có giá trị khác rỗng (đệ quy một cấp), không lấy giá trị."""
    names: set[str] = set()
    def walk(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    walk(v, f"{prefix}{k}.")
                elif v not in (None, "", "0", 0):
                    names.add(prefix + k)
        elif isinstance(obj, list):
            for item in obj[:5]:
                walk(item, prefix)
    walk(payload)
    return sorted(names)


def _day(dt: datetime) -> str:
    # Phản hồi thật 22/11/2026: dạng ISO bị 400; orderBook nhận YYYY/MM/DD theo giờ Việt Nam.
    return dt.astimezone(VN_TZ).strftime("%Y/%m/%d")


def _token_minutes(md: MarketData) -> str:
    token = getattr(md.auth.token_manager, "_token", None)
    if not token:
        return "không rõ"
    now = time.time()
    parts = [f"access {max(token.expires_at - now, 0) / 60:.0f} phút" if token.expires_at else "access ?"]
    if token.refresh_token_expires_at:
        parts.append(f"refresh {max(token.refresh_token_expires_at - now, 0) / 3600:.1f} giờ")
    return ", ".join(parts)


def accounts(md: MarketData) -> list[dict]:
    payload = md.read("/api/v3/account/info")
    return payload if isinstance(payload, list) else rows_of(payload)


def check_rest(md: MarketData) -> None:
    print(f"Xác thực không OTP: OK — thời hạn token: {_token_minutes(md)}")
    try:
        accts = accounts(md)
    except SsiError as exc:
        print(f"  account/info: LỖI — {exc} (khóa có thể chưa có quyền truy vấn tài khoản)")
        return
    print(f"  account/info: {len(accts)} tiểu khoản — " +
          ", ".join(f"{a.get('accountType')} {mask(a.get('accountNo'))}" for a in accts))
    now = datetime.now(timezone.utc)
    for a in accts:
        no, kind = a.get("accountNo"), a.get("accountType")
        print(f"  [{kind} {mask(no)}]")
        for path in ACCOUNT_READS:
            try:
                payload = md.read(path, {"accountNo": no})
            except SsiError as exc:
                print(f"    {path.rsplit('/', 1)[-1]:<15} LỖI — {exc}")
                continue
            fields = filled_fields(payload)
            equity = payload.get("data", payload).get("equity") if isinstance(payload, dict) else None
            n = f"{len(equity)} mã, " if isinstance(equity, list) else ""
            print(f"    {path.rsplit('/', 1)[-1]:<15} OK — {n}{len(fields)} trường có dữ liệu: {', '.join(fields[:12])}"
                  + (" ..." if len(fields) > 12 else ""))
        if kind == "DERIVATIVE":
            continue
        for days in ORDER_WINDOWS_DAYS:
            params = {"accountNo": no, "from": _day(now - timedelta(days=days)), "to": _day(now),
                      "pageIndex": 1, "pageSize": 10}
            try:
                payload = md.read("/api/v3/trading/orderBook", params)
            except SsiError as exc:
                print(f"    orderBook {days:>4} ngày  LỖI — {exc}")
                break
            # rest_client của SDK đã bóc lớp "data"; {"code": 204} nghĩa là không có lệnh.
            book = payload if isinstance(payload, dict) else {}
            if str(book.get("code")) == "204":
                print(f"    orderBook {days:>4} ngày  0 lệnh (204)")
                continue
            orders = book.get("orderList") or []
            print(f"    orderBook {days:>4} ngày  totalRecord={book.get('totalRecord', '?')}, trang đầu {len(orders)} lệnh"
                  f" — khóa: {', '.join(sorted(book)) or 'rỗng'}")


def check_stream(md: MarketData, seconds: int) -> None:
    counts: Counter = Counter()
    acks: list[str] = []
    lock = threading.Lock()

    def on_data(msg) -> None:
        # Phản hồi thật (23/11/2026): SDK trả TradeMessage, QuoteMessage, IndexSummaryMessage... hoặc dict xác nhận.
        kind = type(msg).__name__.removesuffix("Message") if not isinstance(msg, dict) else "xác nhận"
        with lock:
            counts[kind] += 1

    stream = Stream(md.auth)
    svc = stream.streaming

    def fresh_token(force_refresh: bool) -> str:
        # Đăng nhập lại bằng khóa (không OTP) thay vì dùng refresh token: SSI coi refresh token bị dùng lại là lộ.
        if force_refresh or md.auth.token_manager.is_token_expired:
            md.relogin()
        return md.auth.token_manager.access_token

    svc._ws._token_provider = fresh_token
    try:
        svc.connect()
    except Exception as exc:  # thư viện websocket có nhiều loại lỗi; chỉ in tên lỗi
        print(f"WebSocket: KHÔNG kết nối được ({type(exc).__name__})")
        return
    try:
        print("WebSocket: kết nối OK")
        svc.on_data = on_data
        # SDK giữ một callback phản hồi chung cho mọi lần đăng ký, nên chỉ đếm trạng thái, không gắn tên topic.
        def record(resp):
            acks.append(str(resp.get("code", resp.get("status", "ok"))) if isinstance(resp, dict) else "ok")
        svc.subscribe_symbol_trade(WS_SYMBOLS, on_response=record)
        svc.subscribe_symbol_quote(WS_SYMBOLS, on_response=record)
        svc.subscribe_symbol_room(WS_SYMBOLS, on_response=record)
        svc.subscribe_index_summary(["VNINDEX"], on_response=record)
        time.sleep(2)
        try:
            print(f"  Đang đăng ký: {svc.list_subscription()}")
        except Exception as exc:
            print(f"  LIST_SUBSCRIPTION lỗi ({type(exc).__name__})")
        print(f"  Phản hồi đăng ký: {dict(Counter(acks)) if acks else 'chưa nhận'}")
        print(f"  Nghe {seconds} giây ...")
        time.sleep(seconds)
        print(f"  Số thông điệp theo topic: {dict(counts) or 'không có (ngoài giờ giao dịch thì bình thường)'}")
    finally:
        svc.disconnect()
