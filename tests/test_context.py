import math
from pathlib import Path

import pytest

from vnmkt import context, store

SAMPLE = Path(__file__).resolve().parents[1] / "data_sample" / "lake"
pytestmark = pytest.mark.skipif(not (SAMPLE / "daily").exists(), reason="chưa có data_sample/lake")


def test_day_attribution_parts_add_up():
    with store.Warehouse(SAMPLE, read_only=True) as wh:
        day = context.sessions(wh, 3)[0]
        a = context.attribute_day(wh, {"HPG": 20_000, "VCB": 5_000, "SSI": 15_000}, day)
    t = a.table
    assert math.isclose(t["weight"].sum(), 1.0)
    assert ((t["market"] + t["sector_part"] + t["specific"] - t["ret"]).abs() < 1e-12).all()
    assert math.isclose(sum(a.parts.values()), a.portfolio_return, abs_tol=1e-12)
    assert (t["beta"] > 0).all()


def test_breadth_and_foreign_radar():
    with store.Warehouse(SAMPLE, read_only=True) as wh:
        day = context.sessions(wh, 1)[0]
        b = context.breadth(wh, day)
        radar = context.foreign_radar(wh, ["HPG", "ACV"], day)
    assert b.index_value > 0 and b.advances is not None
    assert set(radar.index) == {"HPG", "ACV"}
    assert (radar["days5"] <= 5).all() and (radar["days20"] <= 20).all()
    used = radar["room_used"].dropna()
    assert ((used >= 0) & (used <= 1.0001)).all()
