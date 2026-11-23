import json
from datetime import datetime
from pathlib import Path

from vnmkt import recorder

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ws_messages.json").read_text(encoding="utf-8"))


def message(kind: str):
    """Dựng lại đối tượng cùng tên lớp với thông điệp của SDK từ mẫu phản hồi thật."""
    obj = type(kind, (), {})()
    for k, v in FIXTURE[kind].items():
        setattr(obj, k, v)
    return obj


def test_trade_in_dong():
    topic, rec = recorder.normalize(message("TradeMessage"))
    assert topic == "trade" and rec["symbol"] == "SSI"
    assert rec["price"] > 1000                                  # đồng, không phải nghìn đồng
    assert isinstance(rec["time"], datetime) and rec["time"].microsecond > 0


def test_quote_three_levels_in_dong():
    topic, rec = recorder.normalize(message("QuoteMessage"))
    assert topic == "quote"
    assert rec["bid1"] > 1000 and rec["ask1"] > rec["bid1"]     # SDK đã đổi sang đồng (khác tài liệu)
    assert {"bid3", "ask3_vol"} <= set(rec) and "bid4" not in rec


def test_index_summary_strings_become_numbers():
    topic, rec = recorder.normalize(message("IndexSummaryMessage"))
    assert topic == "index" and rec["index"] == "VNINDEX"
    assert isinstance(rec["value"], float) and rec["value"] > 100
    assert rec["advances"] + rec["declines"] + rec["unchanged"] > 0


def test_unknown_messages_are_skipped():
    assert recorder.normalize({"method": "subscribe", "status": "ok"}) is None
