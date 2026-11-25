from datetime import date, datetime

from vnmkt import store, tick_quality

DAY = date(2026, 11, 23)


def trade(sym, hms, price, qty, total, delay=0.2):
    t = datetime(2026, 11, 23, *hms)
    return {"symbol": sym, "time": t, "price": price, "quantity": qty, "total_volume": total}, \
        datetime.fromtimestamp(t.timestamp() + delay)


def write(tmp_path, trades, quotes=()):
    w = store.TickWriter(tmp_path)
    for rec, at in trades:
        w.add("trade", rec, at)
    for rec, at in quotes:
        w.add("quote", rec, at)
    w.flush()


def test_clean_data_passes(tmp_path):
    write(tmp_path, [trade("SSI", (10, 0, 0), 19_850, 100, 1_000), trade("SSI", (10, 0, 30), 19_900, 200, 1_200)])
    r = tick_quality.check(tmp_path, DAY, {"SSI": ("HOSE", 20_000)})
    assert r.ok and r.counts["trade"] == 2 and r.symbols == 1


def test_detects_each_problem(tmp_path):
    write(tmp_path, [
        trade("SSI", (10, 0, 0), 19_850, 100, 1_000),
        trade("SSI", (10, 0, 0), 19_850, 100, 1_000),             # gửi lại: trùng
        trade("SSI", (10, 0, 5), 19_800, 100, 900),               # tổng khối lượng lùi: sai thứ tự
        trade("SSI", (10, 0, 10), 19_800, 100, 1_100, delay=12),  # nhận sau 12 giây: đến muộn
        trade("SSI", (10, 5, 0), 25_000, 100, 1_200),             # trên giá trần 21.400; cách 5 phút: mất tín hiệu
        trade("SSI", (10, 5, 1), 0, 100, 1_300),                  # giá không dương
    ], quotes=[({"symbol": "SSI", "time": datetime(2026, 11, 23, 10, 0, 1), "bid1": 19_900, "ask1": 19_850},
                datetime(2026, 11, 23, 10, 0, 1))])
    r = tick_quality.check(tmp_path, DAY, {"SSI": ("HOSE", 20_000)})
    assert (r.duplicates, r.out_of_order, r.late, r.abnormal, r.crossed_quotes) == (1, 1, 1, 2, 1)
    assert len(r.gaps) == 1 and not r.ok
    clean = tick_quality.clean_trades(tmp_path, DAY)
    assert len(clean) == 4                                        # bỏ 1 dòng trùng và 1 dòng giá 0


def test_no_data(tmp_path):
    r = tick_quality.check(tmp_path, DAY)
    assert r.counts == {"trade": 0, "quote": 0, "index": 0} and r.ok


def test_late_count_ignores_local_clock_offset(tmp_path):
    # Đồng hồ máy chậm 1,6 giây: mọi thông điệp "nhận trước giờ khớp"; chỉ thông điệp trễ thật mới bị tính muộn.
    write(tmp_path, [trade("SSI", (10, 0, s), 19_850, 100, 1_000 + s, delay=-1.6) for s in range(0, 50, 5)]
          + [trade("SSI", (10, 0, 55), 19_850, 100, 1_100, delay=8)])
    r = tick_quality.check(tmp_path, DAY, {"SSI": ("HOSE", 20_000)})
    assert r.late == 1 and abs(r.clock_offset + 1.6) < 0.01
