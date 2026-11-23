import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from vnmkt import risk, store

SAMPLE = Path(__file__).resolve().parents[1] / "data_sample" / "lake"


def test_euler_contributions_add_up():
    rng = np.random.default_rng(1)
    rets = pd.DataFrame(rng.normal(0, 0.02, size=(300, 4)) @ rng.normal(0, 1, size=(4, 4)) * 0.3,
                        columns=list("ABCD"))
    cov = rets.cov()
    w = pd.Series([0.4, 0.3, 0.2, 0.1], index=list("ABCD"))
    table, vol = risk.euler(w, cov)
    assert math.isclose(vol, math.sqrt(w @ cov @ w), rel_tol=1e-12)
    assert math.isclose(table["rc"].sum(), vol, rel_tol=1e-12)                 # ΣRCᵢ = σp
    assert np.allclose(table["own"] + table["corr"], table["rc"])
    assert math.isclose(table["rc_share"].sum(), 1.0, rel_tol=1e-12)


def test_euler_single_asset_and_independent_assets():
    cov = pd.DataFrame([[0.04, 0.0], [0.0, 0.09]], index=list("AB"), columns=list("AB"))
    table, vol = risk.euler(pd.Series({"A": 1.0, "B": 0.0}), cov)
    assert math.isclose(vol, 0.2) and math.isclose(table.loc["A", "rc_share"], 1.0)
    table, vol = risk.euler(pd.Series({"A": 0.5, "B": 0.5}), cov)
    assert np.allclose(table["corr"], 0.0)                                     # không tương quan: chỉ có phần riêng


def test_risk_report_beta_of_index_like_portfolio():
    rng = np.random.default_rng(2)
    market = pd.Series(rng.normal(0, 0.01, 250))
    rets = pd.DataFrame({"A": 1.5 * market + rng.normal(0, 0.001, 250), "B": 0.5 * market})
    rep = risk.risk_report(rets, market, pd.Series({"A": 0.5, "B": 0.5}))
    assert rep.beta == pytest.approx(1.0, abs=0.02)
    assert rep.table.loc["B", "beta"] == pytest.approx(0.5, abs=1e-9)
    assert rep.systematic_share > 0.99
    with pytest.raises(LookupError):
        risk.risk_report(rets.iloc[:30], market.iloc[:30], pd.Series({"A": 1.0}))


def test_brinson_parts_add_up_to_active_return():
    wp = pd.Series({"Ngân hàng": 0.6, "Bất động sản": 0.4})
    rp = pd.Series({"Ngân hàng": 0.10, "Bất động sản": -0.05})
    wb = pd.Series({"Ngân hàng": 0.3, "Bất động sản": 0.3, "Thép": 0.4})
    rb = pd.Series({"Ngân hàng": 0.08, "Bất động sản": 0.02, "Thép": 0.05})
    t = risk.brinson(wp, rp, wb, rb)
    active = (wp * rp).sum() - (wb * rb).sum()
    assert math.isclose(t[["allocation", "selection", "interaction"]].sum().sum(), active, rel_tol=1e-12)
    assert t.loc["Thép", "selection"] == 0 and t.loc["Thép", "interaction"] == 0


def test_supersector_mapping():
    assert risk.supersector("8355") == "Ngân hàng" and risk.supersector("8633") == "Bất động sản"
    assert risk.supersector(None) == "Khác" and risk.supersector("0000") == "Khác"


@pytest.mark.skipif(not (SAMPLE / "daily").exists(), reason="chưa có data_sample/lake")
def test_risk_and_attribution_on_sample():
    with store.Warehouse(SAMPLE, read_only=True) as wh:
        rets, market = risk.returns(wh, ["HPG", "VCB", "SSI"], 250)
        assert len(rets) >= 200 and not rets.isna().any().any()
        rep = risk.risk_report(rets, market, pd.Series({"HPG": 0.5, "VCB": 0.3, "SSI": 0.2}))
        assert 0.05 < rep.vol < 1.0 and 0.2 < rep.beta < 2.5
        a = risk.attribution(wh, {"HPG": 20_000, "VCB": 5_000, "SSI": 15_000}, 250)
        parts = a.table[["allocation", "selection", "interaction"]].sum().sum()
        assert math.isclose(parts, a.active, abs_tol=1e-12)
        assert math.isclose(a.table["wb"].sum(), 1.0) and math.isclose(a.table["wp"].sum(), 1.0)
