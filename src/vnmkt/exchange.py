"""Quy tắc giá của ba sàn cổ phiếu: biên độ, bước giá, giá trần, giá sàn, lô chẵn. Giá tính bằng đồng.

Đối chiếu với giá sàn SSI trả về (`masterdata`, phiên 13/11/2026): khớp 100% HNX, 99,9% UPCoM (trừ các mã có
biên độ đặc biệt ngày đầu niêm yết hoặc giao dịch lại), 96,8% HOSE; chỗ lệch ở HOSE tối đa 2 bước giá và chỉ
khi giá ngay dưới mốc đổi bước giá 10.000 hoặc 50.000 đồng. Xem tests/test_exchange.py.
"""

from __future__ import annotations

import math

BAND = {"HOSE": 0.07, "HNX": 0.10, "UPCOM": 0.15}
LOT = 100  # lô chẵn của cả ba sàn


def board_of(board: str) -> str:
    key = (board or "").strip().upper()
    if key not in BAND:
        raise ValueError(f"Sàn không hỗ trợ: {board!r}")
    return key


def tick(board: str, price: float) -> int:
    """Bước giá cổ phiếu: HOSE 10/50/100 đồng theo mức giá; HNX và UPCoM 100 đồng."""
    if board_of(board) != "HOSE":
        return 100
    return 10 if price < 10_000 else 50 if price < 50_000 else 100


def floor_price(board: str, ref: float) -> float:
    """Giá sàn của phiên kế tiếp từ giá tham chiếu: ref × (1 − biên độ), làm tròn lên tới bước giá.
    Nếu làm tròn mà không giảm được thì giảm một bước giá; giá không xuống dưới một bước giá."""
    board = board_of(board)
    raw = ref * (1 - BAND[board])
    step = tick(board, raw)
    price = math.ceil(round(raw / step, 9)) * step
    if price >= ref:
        price = ref - tick(board, ref - 1)
    return float(price) if price > 0 else float(ref)


def ceiling_price(board: str, ref: float) -> float:
    """Giá trần của phiên kế tiếp: ref × (1 + biên độ), làm tròn xuống tới bước giá; tăng ít nhất một bước giá."""
    board = board_of(board)
    raw = ref * (1 + BAND[board])
    step = tick(board, raw)
    price = math.floor(round(raw / step, 9)) * step
    return float(price if price > ref else ref + tick(board, ref))


def lots_down(shares: float) -> int:
    """Số cổ phiếu làm tròn xuống lô chẵn."""
    return int(shares // LOT * LOT) if shares > 0 else 0
