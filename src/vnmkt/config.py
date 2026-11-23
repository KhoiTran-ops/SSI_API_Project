"""Cấu hình dùng chung: đường dẫn, múi giờ và khóa SSI đọc từ biến môi trường hoặc tệp .env."""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Việt Nam không có giờ mùa hè; dùng múi giờ cố định để không phụ thuộc gói tzdata trên Windows.
VN_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")
BOARD = "HOSE"                    # sàn của lớp phân tích (VN-Index chỉ gồm cổ phiếu HOSE)
BOARDS = ("HOSE", "HNX", "UPCOM")  # sàn được thu thập để tra cứu từng cổ phiếu
# Thấp hơn mặc định 10 lượt/giây của ssi-sdk; SDK vẫn tự chờ khi nhận HTTP 429.
RATE_LIMIT_PER_SECOND = 2


class ConfigError(Exception):
    """Thiếu hoặc sai cấu hình, thông điệp an toàn để hiển thị."""


def _env_file() -> dict[str, str]:
    path = ROOT / ".env"
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name, sep, value = line.partition("=")
        if sep and not name.lstrip().startswith("#"):
            values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def setting(name: str, default: str | None = None) -> str | None:
    """Biến môi trường được ưu tiên hơn giá trị trong .env."""
    return os.environ.get(name) or _env_file().get(name) or default


def credentials() -> tuple[str, str]:
    key, secret = setting("SSI_API_KEY"), setting("SSI_API_SECRET")
    if not key or not secret:
        raise ConfigError("Thiếu SSI_API_KEY hoặc SSI_API_SECRET. Sao chép .env.example thành .env và điền khóa.")
    return key, secret


def data_dir() -> Path:
    """Thư mục dữ liệu; đặt DATA_DIR=data_sample để chạy bằng dữ liệu mẫu."""
    path = Path(setting("DATA_DIR") or ROOT / "data")
    return path if path.is_absolute() else ROOT / path
