from datetime import date, datetime

import pandas as pd

from vnmkt import store


def _daily(symbol, day, close):
    frame = store.daily_frame([{"symbol": symbol, "tradingDate": day, "close": str(close)}])
    return frame


def test_lake_round_trip_partitions_and_upsert(tmp_path):
    with store.Warehouse(tmp_path) as wh:
        wh.upsert("daily", pd.concat([_daily("AAA", "2021/12/31", 10), _daily("AAA", "2022/01/03", 11)]))
        wh.upsert("securities", store.securities_frame([{"symbol": "AAA", "board": "HOSE"}]))
    assert sorted(p.name for p in (tmp_path / "daily").iterdir()) == ["year=2021", "year=2022"]
    assert not list(tmp_path.glob(".*.tmp")) + list(tmp_path.glob(".*.old"))  # không còn thư mục tạm

    with store.Warehouse(tmp_path) as wh:  # mở lại: dữ liệu còn, upsert ghi đè đúng khóa
        wh.upsert("daily", _daily("AAA", "2022/01/03", 12))
    with store.Warehouse(tmp_path, read_only=True) as ro:
        assert ro.rows("SELECT date, close FROM daily ORDER BY date") == [(date(2021, 12, 31), 10.0),
                                                                          (date(2022, 1, 3), 12.0)]
        assert "year" not in ro.query("SELECT * FROM daily").columns
        assert ro.scalar("SELECT count(*) FROM master") == 0  # bảng chưa có tệp vẫn truy vấn được


def test_unchanged_tables_are_not_rewritten(tmp_path):
    with store.Warehouse(tmp_path) as wh:
        wh.upsert("securities", store.securities_frame([{"symbol": "AAA", "board": "HOSE"}]))
    stamp = (tmp_path / "securities" / "part-0.parquet").stat().st_mtime_ns
    with store.Warehouse(tmp_path) as wh:
        wh.upsert("daily", _daily("AAA", "2022/01/03", 11))
    assert (tmp_path / "securities" / "part-0.parquet").stat().st_mtime_ns == stamp


def test_replace_symbol_removes_old_history(tmp_path):
    with store.Warehouse(tmp_path) as wh:
        wh.upsert("daily", pd.concat([_daily("AAA", "2021/12/31", 10), _daily("BBB", "2021/12/31", 5)]))
    with store.Warehouse(tmp_path) as wh:
        wh.replace_symbol("daily", "AAA", _daily("AAA", "2022/01/03", 9))
    with store.Warehouse(tmp_path, read_only=True) as ro:
        assert ro.rows("SELECT symbol, date FROM daily ORDER BY symbol") == [("AAA", date(2022, 1, 3)),
                                                                             ("BBB", date(2021, 12, 31))]


def test_ticks_are_batched_and_compacted(tmp_path):
    writer = store.TickWriter(tmp_path, max_rows=2)
    at = datetime(2026, 11, 23, 9, 30)
    for i in range(5):
        writer.add("trade", {"symbol": "AAA", "price": 10_000 + i}, at)
    writer.add("quote", {"symbol": "AAA", "bid1": 9.9}, at)
    writer.flush()
    folder = tmp_path / "ticks" / "date=2026-11-23"
    assert len(list((folder / "topic=trade").glob("*.parquet"))) == 3  # 2 + 2 + 1 (lần flush cuối)
    assert store.compact_ticks(tmp_path, date(2026, 11, 23)) == 1      # quote chỉ có một tệp, không cần gộp
    files = list((folder / "topic=trade").glob("*.parquet"))
    assert len(files) == 1
    prices = pd.read_parquet(files[0])["price"].tolist()
    assert prices == [10_000, 10_001, 10_002, 10_003, 10_004]


def test_tick_topic_name_cannot_escape_folder(tmp_path):
    writer = store.TickWriter(tmp_path)
    try:
        writer.add("../x", {"a": 1})
    except ValueError:
        return
    raise AssertionError("topic chứa đường dẫn phải bị từ chối")


def test_second_writer_is_refused_while_first_is_open(tmp_path):
    with store.Warehouse(tmp_path):
        try:
            store.Warehouse(tmp_path)
        except store.WarehouseBusy:
            pass
        else:
            raise AssertionError("tiến trình ghi thứ hai phải bị từ chối")
        with store.Warehouse(tmp_path, read_only=True):  # đọc vẫn được
            pass
    with store.Warehouse(tmp_path):  # đóng xong thì ghi lại được
        pass


def test_interrupted_swap_is_recovered(tmp_path):
    with store.Warehouse(tmp_path) as wh:
        wh.upsert("securities", store.securities_frame([{"symbol": "AAA", "board": "HOSE"}]))
    # Giả lập dừng đột ngột giữa lúc thay thư mục: bảng cũ đã đổi tên, bảng mới chưa vào chỗ.
    (tmp_path / "securities").rename(tmp_path / ".securities.old")
    (tmp_path / ".securities.tmp").mkdir()
    with store.Warehouse(tmp_path) as wh:
        assert wh.scalar("SELECT count(*) FROM securities") == 1
    assert not (tmp_path / ".securities.old").exists() and not (tmp_path / ".securities.tmp").exists()
