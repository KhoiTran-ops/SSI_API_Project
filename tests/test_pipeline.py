import gzip
import json
from datetime import date, datetime

import pandas as pd
import pytest

from vnmkt import pipeline, store
from vnmkt.config import VN_TZ

END = date(2026, 11, 13)


def row(symbol, day, close, match="100", buy="50"):
    return {"symbol": symbol, "tradingDate": day, "close": close, "open": close, "high": close, "low": close,
            "totalMatch": match, "totalMatchValue": "1000", "totalTradeBuy": buy, "totalForeignBuy": None}


class FakeMD:
    """Máy chủ giả: chỉ trả các dòng trong khoảng ngày được hỏi, ghi lại từng lượt gọi."""

    def __init__(self):
        self.calls = []
        self.history = {
            # BBB có lịch sử dài hơn một đoạn (2022 và 2026) để kiểm tra lấy lùi nhiều đoạn
            "AAA": [row("AAA", "2026/11/11", "10"), row("AAA", "2026/11/12", "11"), row("AAA", "2026/11/13", "12")],
            "BBB": [row("BBB", "2022/03/01", "5"), row("BBB", "2025/01/02", "5.5"), row("BBB", "2026/11/13", "6")],
            "CCC": [row("CCC", "2026/11/11", "3")],  # kém thanh khoản: không có giao dịch ở phiên cuối
            "VNINDEX": [row("VNINDEX", "2026/11/12", "1200"), row("VNINDEX", "2026/11/13", "1210")],
            "VN30": [], "VN100": [],
        }

    def securities(self, board):
        return {"HOSE": [{"symbol": "AAA", "board": "HOSE", "stockType": "Stock", "listedShare": "1000"},
                         {"symbol": "E1VFVN30", "board": "HOSE", "stockType": "ETF"}],
                "UPCOM": [{"symbol": "CCC", "board": "UPCOM", "stockType": "Stock"}],
                "HNX": [{"symbol": "BBB", "board": "HNX", "stockType": "Stock"},
                        {"symbol": "VN41GB", "board": "HNX", "stockType": "Bond"}]}.get(board, [])

    def summary(self, start, end, symbol=None, index=None):
        key = symbol or index
        self.calls.append((key, start, end))
        return [r for r in self.history[key] if start <= r["tradingDate"] <= end]

    def board_summary(self, board, day):
        self.calls.append(("board", day))
        return [{"tradingDate": day, "totalAdvanceStock": "200", "totalDeclineStock": "", "indexValue": "1"}]

    def corporate_actions(self, start, end):
        self.calls.append(("actions", start, end))
        if start <= "2026/11/12" <= end:
            return [{"symbol": "AAA", "eventTitle": "AAA - Trả cổ tức bằng tiền 1000 đồng/cổ phiếu",
                     "exrightDate": "2026/11/12", "ratio": "0.9"},
                    {"symbol": "AAA", "eventTitle": "Mới công bố", "exrightDate": "", "ratio": ""}]
        return []

    def master_data(self, start, end):
        self.calls.append(("master", start))
        return [{"symbol": "AAA", "tradingDate": start, "ceiling": "12.8", "floor": "11.2", "refPrice": "12"},
                {"symbol": "CAAA2601", "tradingDate": start, "ceiling": "1", "floor": "0.1", "refPrice": "0.5"}]


@pytest.fixture(autouse=True)
def short_actions_history(monkeypatch):
    monkeypatch.setattr(pipeline, "ACTIONS_START", date(2026, 11, 1))


@pytest.fixture
def wh(tmp_path):
    with store.Warehouse(tmp_path / "lake") as w:
        yield w


def summary_calls(md, key):
    return [c[1:] for c in md.calls if c[0] == key]


def test_daily_frame_normalization():
    frame = store.daily_frame([row("aaa", "2026/11/13", "12", buy="0"), row("AAA", "2026/11/13", "13"),
                               {"symbol": "BBB", "tradingDate": "bad"}])
    assert len(frame) == 1 and frame.iloc[0]["symbol"] == "AAA" and frame.iloc[0]["close"] == 13
    assert frame["f_buy_vol"].isna().all()
    assert store.daily_frame([row("AAA", "2026/11/13", "12", buy="0")])["buy_vol"].isna().all()
    assert store.daily_frame([row("AAA", "2026/11/13", "12", match="0", buy="0")]).iloc[0]["buy_vol"] == 0
    assert store.daily_frame([row("AAA", "2026/11/13", "0", match="0")]).empty  # phiên trước niêm yết
    board = store.board_frame("HOSE", [{"tradingDate": "2026/11/12", "totalBuyForeign": "0", "totalSellForeign": "0",
                                        "netPurchasesForeignValue": "0", "totalAdvanceStock": "94"}])
    assert board[["f_buy_vol", "f_net_val"]].isna().all().all() and board["advances"].item() == 94


@pytest.mark.parametrize("title, value", [
    ("SSC - Trả cổ tức Cả năm năm 2025 bằng tiền 1000 đồng/cổ phiếu", 1000),
    ("NHT-Trả cổ tức Đợt 2, 2017 bằng tiền 6000 VND/CP", 6000),
    ("X - Trả cổ tức bằng tiền 1.500 đồng/cổ phiếu", 1500),
    ("X - Tạm ứng cổ tức bằng tiền 12%", 1200),
    ("X - Trả cổ tức bằng cổ phiếu tỷ lệ 10%", None),
    ("X - Kết quả giao dịch nội bộ", None),
])
def test_cash_dividend_parsing(title, value):
    assert store.cash_dividend(title) == value


def test_run_walks_history_back_until_listing_and_is_idempotent(wh):
    md = FakeMD()
    result = pipeline.Pipeline(md, wh, end=END).run()
    assert result["securities"] == 3  # AAA (HOSE), BBB (HNX), CCC (UPCOM); ETF, trái phiếu bị loại
    assert wh.rows("SELECT symbol, board FROM securities ORDER BY symbol") == [("AAA", "HOSE"), ("BBB", "HNX"),
                                                                             ("CCC", "UPCOM")]
    assert result["daily"] == 7 and result["index_daily"] == 2 and result["board_daily"] == 2
    # Mỗi lượt gọi ≤ CHUNK_DAYS ngày; BBB cần 3 đoạn có dữ liệu/rỗng xen kẽ rồi dừng ở đoạn rỗng đủ dài.
    for start, stop in summary_calls(md, "BBB") + summary_calls(md, "AAA"):
        assert (store.parse_day(stop) - store.parse_day(start)).days == pipeline.CHUNK_DAYS - 1
    assert len(summary_calls(md, "AAA")) == 2  # đoạn có giá + đoạn rỗng
    assert wh.rows("SELECT date FROM daily WHERE symbol = 'BBB' ORDER BY date") == \
        [(date(2022, 3, 1),), (date(2025, 1, 2),), (date(2026, 11, 13),)]
    assert set(wh.query("SELECT symbol FROM coverage")["symbol"]) >= {"AAA", "BBB", "VNINDEX"}
    assert wh.query("SELECT symbol FROM master")["symbol"].tolist() == ["AAA"]  # bỏ mã ngoài danh mục
    actions = wh.query("SELECT * FROM corporate_actions")
    assert len(actions) == 1 and actions["cash_dividend"].item() == 1000 and actions["ratio"].item() == 0.9

    # Chạy lại vào thứ Bảy: không có phiên mới nên không gọi lại mã nào (kể cả CCC không giao dịch phiên cuối),
    # masterdata hay indexSummary.
    md.calls.clear()
    again = pipeline.Pipeline(md, wh, end=date(2026, 11, 14)).run()
    assert again["daily"] == 0 and again["board_daily"] == 0 and again["master"] == 0
    assert not [c for c in md.calls if c[0] in ("AAA", "BBB", "CCC", "board", "master")]
    assert wh.query("SELECT count(*) AS n FROM daily")["n"][0] == 7
    # Có phiên mới (thứ Hai) thì mã kém thanh khoản vẫn được hỏi lại.
    md.history["VNINDEX"].append(row("VNINDEX", "2026/11/16", "1220"))
    md.calls.clear()
    pipeline.Pipeline(md, wh, end=date(2026, 11, 16)).run()
    assert summary_calls(md, "CCC") == [("2026/11/11", "2026/11/16")]


def test_backfill_extends_old_three_year_store(wh):
    """Kho cũ chỉ có phần gần đây và chưa có coverage: lần chạy sau bổ sung phần lịch sử cũ hơn một lần."""
    md = FakeMD()
    wh.upsert("daily", store.daily_frame([row("BBB", "2026/11/13", "6")]))
    pipeline.Pipeline(md, wh, end=END).run()
    assert wh.query("SELECT count(*) AS n FROM daily WHERE symbol = 'BBB'")["n"][0] == 3
    md.calls.clear()
    pipeline.Pipeline(md, wh, end=END).run()
    assert not summary_calls(md, "BBB")


def test_incremental_update_and_adjustment_reload(wh):
    md = FakeMD()
    md.history["AAA"] = md.history["AAA"][:2]
    pipeline.Pipeline(md, wh, end=date(2026, 11, 12)).run()
    md.history["AAA"].append(row("AAA", "2026/11/13", "12"))

    md.calls.clear()
    pipeline.Pipeline(md, wh, end=END).run()
    assert summary_calls(md, "AAA") == [("2026/11/12", "2026/11/13")]  # chỉ lấy từ ngày cuối đã lưu
    assert wh.query("SELECT count(*) AS n FROM daily WHERE symbol = 'AAA'")["n"][0] == 3

    # SSI điều chỉnh lùi toàn bộ giá -> phải tải lại toàn bộ mã
    md.history["AAA"] = [row("AAA", r["tradingDate"], str(float(r["close"]) * 0.9)) for r in md.history["AAA"]]
    md.history["AAA"].append(row("AAA", "2026/11/14", "11"))
    md.history["VNINDEX"].append(row("VNINDEX", "2026/11/14", "1220"))
    pipeline.Pipeline(md, wh, end=date(2026, 11, 14)).run()
    closes = wh.query("SELECT close FROM daily WHERE symbol='AAA' ORDER BY date")["close"].round(4).tolist()
    assert closes == [9.0, 9.9, 10.8, 11.0]


def test_action_windows_follow_fixed_grid_and_refresh_recent(wh):
    md = FakeMD()
    job = pipeline.Pipeline(md, wh, end=END)
    job.actions()
    windows = [c[1:] for c in md.calls if c[0] == "actions"]
    assert windows == [("2026/11/01", "2026/11/05"), ("2026/11/06", "2026/11/10"), ("2026/11/11", "2026/11/13")]
    md.calls.clear()
    pipeline.Pipeline(md, wh, end=date(2027, 1, 1)).actions()
    again = [c[1] for c in md.calls if c[0] == "actions"]
    assert "2026/11/01" not in again and "2026/12/06" in again  # cửa sổ cũ đã lấy thì không lấy lại


def test_last_close_waits_for_end_of_session():
    assert pipeline.last_close(datetime(2026, 11, 13, 10, 0, tzinfo=VN_TZ)) == date(2026, 11, 12)
    assert pipeline.last_close(datetime(2026, 11, 13, 16, 0, tzinfo=VN_TZ)) == date(2026, 11, 13)


def test_raw_log_writes_gzip_jsonl_without_secrets(tmp_path):
    log = store.RawLog(tmp_path)
    log("/api/v3/data/securitiesSummary", {"symbol": "AAA"}, {"data": [1]})
    files = list(tmp_path.rglob("*.jsonl.gz"))
    assert [f.name for f in files] == ["securitiesSummary.jsonl.gz"]
    record = json.loads(gzip.open(files[0], "rt", encoding="utf-8").readline())
    assert record["params"] == {"symbol": "AAA"} and "token" not in json.dumps(record).lower()


class RoutedRest:
    """Giả lập máy chủ SSI theo đường dẫn, để chạy MarketData thật (kèm RawLog) mà không gọi mạng."""

    rate_limit = {}

    def __init__(self, fake):
        self.fake = fake

    def get(self, path, params):
        f, name = self.fake, path.rsplit("/", 1)[-1]
        if name == "securitiesByBoard":
            data = f.securities(params["board"])
        elif name == "securitiesSummary":
            data = f.summary(params["from"], params["to"], params.get("symbol"), params.get("index"))
        elif name == "indexSummary":
            data = f.board_summary(params["board"], params["tradingDate"])
        elif name == "corporateActions":
            data = f.corporate_actions(params["from"], params["to"])
        else:
            data = f.master_data(params["from"], params["to"])
        return {"data": data, "pagesCount": 1}


def test_rebuild_from_raw_matches_live_run(tmp_path):
    from vnmkt.ssi import MarketData
    fake = FakeMD()
    fake.history["AAA"].insert(0, row("AAA", "2026/11/10", "0", match="0"))  # phiên trước niêm yết
    md = MarketData("k", "s", on_response=store.RawLog(tmp_path / "raw"))
    md._auth = type("A", (), {"rest_client": RoutedRest(fake)})()
    tables = ["securities", "daily", "index_daily", "board_daily", "corporate_actions", "master", "coverage",
              "action_windows"]
    with store.Warehouse(tmp_path / "lake") as live:
        pipeline.Pipeline(md, live, end=END).run()
        pipeline.record_run(live, store.now(), "ok", md.calls, [])
        expected = {t: live.query(f"SELECT * EXCLUDE (fetched_at) FROM {t} ORDER BY ALL") for t in tables}
        runs = live.rows("SELECT status, calls, session FROM runs")
    assert runs == [("ok", md.calls, END)] and md.calls > 0
    with store.Warehouse(tmp_path / "lake") as rebuilt:
        pipeline.rebuild(store.RawLog(tmp_path / "raw"), rebuilt)
        for t in tables:
            pd.testing.assert_frame_equal(rebuilt.query(f"SELECT * EXCLUDE (fetched_at) FROM {t} ORDER BY ALL"),
                                          expected[t], check_dtype=False)
        assert len(rebuilt.query("SELECT * FROM runs")) == 1  # nhật ký chạy được giữ lại


def test_quality_reports_missing_sessions(wh):
    md = FakeMD()
    md.history["AAA"] = [md.history["AAA"][0], md.history["AAA"][2]]  # AAA không có dòng phiên 12/11
    md.history["BBB"].insert(2, row("BBB", "2026/11/12", "5.8"))      # nhưng thị trường có giao dịch phiên đó
    pipeline.Pipeline(md, wh, end=END).run()
    report = pipeline.quality(wh).set_index("symbol")
    assert report.loc["AAA", "missing_sessions"] == 1 and report.loc["AAA", "history_complete"]


def test_first_load_stops_at_last_published_index_session(wh):
    """SSI chưa công bố VN-Index phiên END: mã tải lần đầu (tải toàn bộ) cũng không được lấy phiên END."""
    md = FakeMD()
    md.history["VNINDEX"] = md.history["VNINDEX"][:1]          # chỉ có phiên 12/11
    pipeline.Pipeline(md, wh, end=END).run()
    assert wh.scalar("SELECT max(date) FROM daily") == date(2026, 11, 12)


def test_unpublished_session_is_fetched_on_next_run(wh):
    """Chạy lần 1 khi SSI chưa có phiên END; lần 2 (SSI đã công bố) phải lấy được phiên END cho mọi mã."""
    md = FakeMD()
    full_index, full_aaa = list(md.history["VNINDEX"]), list(md.history["AAA"])
    md.history["VNINDEX"], md.history["AAA"] = full_index[:1], full_aaa[:2]
    pipeline.Pipeline(md, wh, end=END).run()
    assert wh.scalar("SELECT max(through) FROM checked") == date(2026, 11, 12)
    md.history["VNINDEX"], md.history["AAA"] = full_index, full_aaa
    pipeline.Pipeline(md, wh, end=END).run()
    assert wh.scalar("SELECT max(date) FROM daily WHERE symbol = 'AAA'") == END
    assert wh.scalar("SELECT max(date) FROM index_daily WHERE symbol = 'VNINDEX'") == END
