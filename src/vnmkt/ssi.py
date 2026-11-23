"""Đọc Market Data của SSI FastConnect V3 qua lớp truyền tải của ssi-sdk.

Dùng `rest_client` của SDK (giới hạn tần suất, tự chờ khi gặp HTTP 429, thử lại khi hết thời gian chờ)
nhưng giữ nguyên JSON gốc, vì bộ chuyển đổi của SDK biến ô thiếu thành 0. Chỉ dùng token Market Data
(không OTP), nên không có quyền giao dịch.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import httpx
from ssi_sdk import Auth, Config
from ssi_sdk.exceptions import AuthenticationError, RateLimitError, SSIError

from .config import RATE_LIMIT_PER_SECOND

log = logging.getLogger(__name__)

PAGE_SIZE = 1000
CORPORATE_ACTIONS_PAGE_SIZE = 100  # endpoint chỉ nhận 10, 20, 50 hoặc 100
MAX_PAGES = 50  # chặn vòng lặp vô hạn nếu máy chủ trả số trang bất thường

Hook = Callable[[str, dict, Any], None]

# Chỉ các endpoint ĐỌC được phép gọi. Mọi đường dẫn đặt/sửa/hủy lệnh đều bị chặn ngay trong code.
READ_ONLY_PATHS = (
    "/api/v3/data/",
    "/api/v3/account/info",
    "/api/v3/trading/position",
    "/api/v3/trading/accountBalance",
    "/api/v3/trading/ppmmrAccount",
    "/api/v3/trading/orderBook",
    "/api/v3/trading/maxBuySell",
)


def allowed(path: str) -> bool:
    return path.startswith(READ_ONLY_PATHS[0]) or path in READ_ONLY_PATHS[1:]


class SsiError(Exception):
    """Lỗi khi gọi SSI, thông điệp không chứa khóa hay token."""


def rows_of(payload: Any) -> list[dict]:
    """Bản ghi của một phản hồi: mảng trần, phong bì {"data": [...]}, hoặc {"code": 204} (rỗng)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if str(payload.get("code")) == "204":
            return []
        data = payload.get("data")
        if data is None:
            return []
        if isinstance(data, list):
            return data
    raise SsiError("Phản hồi SSI có cấu trúc khác tài liệu.")


def _pages(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 1
    value = payload.get("pagesCount", payload.get("pageCount"))
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return 1


class MarketData:
    """Phiên làm việc chỉ đọc. Dùng trong `with` để đóng kết nối."""

    def __init__(self, api_key: str, api_secret: str, on_response: Hook | None = None):
        self._config = Config(api_key=api_key, api_secret=api_secret,
                              rate_limit_per_second=RATE_LIMIT_PER_SECOND, log_level="WARNING")
        self._auth: Auth | None = None
        self._hook = on_response
        self.calls = 0  # số lượt gọi dữ liệu (không tính xác thực), ghi vào nhật ký mỗi lần cập nhật

    def __enter__(self) -> MarketData:
        self._auth = Auth(self._config)
        try:
            self._login()
        except SsiError:
            self._auth.close()
            raise
        return self

    def _login(self) -> None:
        """Xin token Market Data (không OTP). Không dùng refresh token: SSI coi refresh token bị dùng lại là bị
        lộ và thu hồi cả khóa, còn đăng nhập lại bằng khóa thì an toàn."""
        try:
            self._auth.authenticate()
        except AuthenticationError:
            raise SsiError("SSI từ chối xác thực. Kiểm tra cặp khóa FastConnect V3 và quyền Market Data.") from None
        except (SSIError, httpx.HTTPError, OSError) as exc:
            raise SsiError(f"Không kết nối hoặc xác thực được với SSI ({type(exc).__name__}).") from None

    def __exit__(self, *exc: object) -> None:
        if self._auth:
            self._auth.close()

    @property
    def rate_limit(self) -> dict:
        """Header X-RATELIMIT-* của lần gọi gần nhất."""
        return dict(self._auth.rest_client.rate_limit) if self._auth else {}

    def _get(self, path: str, params: dict) -> Any:
        """Một lượt gọi. Token hết hạn (~15 phút; SDK không tự làm mới cho Market Data) thì đăng nhập lại
        trước khi gọi; nếu vẫn bị 401 thì đăng nhập lại đúng một lần rồi thử lại."""
        if not allowed(path):
            raise SsiError(f"Chặn {path}: ứng dụng chỉ được gọi endpoint đọc dữ liệu.")
        self.calls += 1
        if getattr(self._auth, "is_token_expired", False):
            self._login()
        try:
            try:
                payload = self._auth.rest_client.get(path, params=params)
            except AuthenticationError:
                log.info("Token hết hạn khi gọi %s; đăng nhập lại", path)
                self._login()
                payload = self._auth.rest_client.get(path, params=params)
        except RateLimitError:
            raise SsiError("Vượt giới hạn tần suất SSI sau nhiều lần chờ. Hãy chạy lại sau.") from None
        except (SSIError, httpx.HTTPError, OSError) as exc:
            raise SsiError(f"Lỗi khi gọi {path} ({type(exc).__name__}).") from None
        if self._hook:
            self._hook(path, params, payload)
        return payload

    def _paged(self, path: str, params: dict, size: int = PAGE_SIZE) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            payload = self._get(path, {**params, "pageIndex": page, "pageSize": size})
            batch = rows_of(payload)
            rows.extend(batch)
            if not batch or page >= _pages(payload):
                return rows
        raise SsiError(f"{path} trả về hơn {MAX_PAGES} trang; hãy thu hẹp khoảng ngày.")

    @property
    def auth(self) -> Auth:
        """Phiên xác thực dùng chung cho WebSocket (một khóa chỉ có một phiên đăng nhập)."""
        return self._auth

    def relogin(self) -> None:
        self._login()

    def read(self, path: str, params: dict | None = None) -> Any:
        """Gọi một endpoint đọc (tài khoản, vị thế...) và trả JSON gốc."""
        return self._get(path, params or {})

    # Ngày truyền vào theo định dạng YYYY/MM/DD như tài liệu SSI.
    def securities(self, board: str) -> list[dict]:
        return rows_of(self._get("/api/v3/data/securitiesByBoard", {"board": board}))

    def summary(self, start: str, end: str, symbol: str | None = None, index: str | None = None) -> list[dict]:
        if (symbol is None) == (index is None):
            raise ValueError("Truyền đúng một trong symbol hoặc index.")
        key = {"symbol": symbol} if symbol else {"index": index}
        return self._paged("/api/v3/data/securitiesSummary", {**key, "from": start, "to": end})

    def board_summary(self, board: str, trading_date: str) -> list[dict]:
        return rows_of(self._get("/api/v3/data/indexSummary", {"board": board, "tradingDate": trading_date}))

    def corporate_actions(self, start: str, end: str) -> list[dict]:
        return self._paged("/api/v3/data/corporateActions", {"from": start, "to": end},
                           size=CORPORATE_ACTIONS_PAGE_SIZE)

    def master_data(self, start: str, end: str) -> list[dict]:
        return self._paged("/api/v3/data/masterdata", {"from": start, "to": end})
