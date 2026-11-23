from pathlib import Path

import pytest

from vnmkt import exchange, store

SAMPLE = Path(__file__).resolve().parents[1] / "data_sample" / "lake"


def test_floor_price_rules():
    assert exchange.floor_price("HOSE", 6900) == 6420      # 6417 làm tròn lên bước 10
    assert exchange.floor_price("HOSE", 15100) == 14050    # bước 50
    assert exchange.floor_price("HOSE", 100000) == 93000   # bước 100
    assert exchange.floor_price("HNX", 25000) == 22500
    assert exchange.floor_price("UPCOM", 25000) == 21300   # 21250 làm tròn lên bước 100
    assert exchange.floor_price("UPCOM", 400) == 300       # làm tròn lên bằng giá tham chiếu: giảm một bước
    assert exchange.floor_price("UPCOM", 100) == 100       # không xuống dưới một bước giá
    with pytest.raises(ValueError):
        exchange.floor_price("OTC", 1000)


def test_lots_down():
    assert [exchange.lots_down(x) for x in (0, 99, 100, 1999.9, -5)] == [0, 0, 100, 1900, 0]


@pytest.mark.skipif(not (SAMPLE / "master").exists(), reason="chưa có data_sample/lake")
def test_floor_price_matches_ssi_master():
    """So với giá sàn SSI trả về. Bỏ mã có biên độ đặc biệt (lệch biên độ thường hơn 2 điểm %)."""
    with store.Warehouse(SAMPLE, read_only=True) as wh:
        rows = wh.rows("SELECT upper(s.board), m.ref, m.floor FROM master m JOIN securities s USING (symbol)")
    by_board: dict[str, list[tuple[float, float]]] = {}
    for board, ref, floor in rows:
        if abs((1 - floor / ref) - exchange.BAND[board]) > 0.02 + 100 / ref:
            continue
        by_board.setdefault(board, []).append((exchange.floor_price(board, ref) - floor) / exchange.tick(board, floor))
    hose = by_board["HOSE"]
    assert len(hose) > 300
    assert sum(d == 0 for d in hose) / len(hose) > 0.95
    assert max(abs(d) for d in hose) <= 2
    for board in ("HNX", "UPCOM"):
        assert all(abs(d) <= 1 for d in by_board.get(board, []))


def test_ceiling_price_rules():
    assert exchange.ceiling_price("HOSE", 6900) == 7380      # 7383 làm tròn xuống bước 10
    assert exchange.ceiling_price("HOSE", 9690) == 10350     # 10368 vượt mốc 10.000: bước 50
    assert exchange.ceiling_price("UPCOM", 25000) == 28700   # 28750 làm tròn xuống bước 100
    assert exchange.ceiling_price("UPCOM", 400) == 500       # làm tròn xuống bằng giá tham chiếu: tăng một bước


@pytest.mark.skipif(not (SAMPLE / "master").exists(), reason="chưa có data_sample/lake")
def test_ceiling_price_matches_ssi_master():
    with store.Warehouse(SAMPLE, read_only=True) as wh:
        rows = wh.rows("SELECT upper(s.board), m.ref, m.ceiling FROM master m JOIN securities s USING (symbol)")
    diffs = [(exchange.ceiling_price(b, ref) - c) / exchange.tick(b, c) for b, ref, c in rows
             if abs((c / ref - 1) - exchange.BAND[b]) <= 0.02 + 100 / ref]
    assert len(diffs) > 300 and sum(d == 0 for d in diffs) / len(diffs) > 0.98
