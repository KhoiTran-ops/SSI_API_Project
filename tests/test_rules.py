import math

import pandas as pd
import pytest

from vnmkt import market, rules
from vnmkt.portfolio import Holding, Portfolio, PortfolioError


def liquidity(ratio=1.0):
    pos = pd.DataFrame([{"board": b, "position": p, "sessions": 10, "locked_share": 0.2, "median": ratio}
                        for b in ("HOSE", "HNX", "UPCOM") for p in range(1, 5)])
    return market.FloorLiquidity(pos, pd.Series(dtype=float), since=None)


QUOTES = pd.DataFrame([
    {"symbol": "VCB", "board": "HOSE", "icb_code": "8355", "price": 50_000.0, "adv": 2_000_000},
    {"symbol": "CTG", "board": "HOSE", "icb_code": "8355", "price": 40_000.0, "adv": 2_000_000},
    {"symbol": "VHM", "board": "HOSE", "icb_code": "8633", "price": 40_000.0, "adv": 10_000},
]).set_index("symbol")
P = Portfolio((Holding("VCB", 10_000), Holding("CTG", 10_000), Holding("VHM", 5_000)), debt=600_000_000)
# Giá trị: VCB 500 tr, CTG 400 tr, VHM 200 tr; L = 1,1 tỷ; R = 500/1100 = 45,45%


def by(checks, rule):
    return {c.subject: c for c in checks if c.rule == rule}


def test_each_rule_reports_value_and_pass():
    r = rules.Rules(max_symbol_weight=0.40, max_sector_weight=0.80, min_margin_ratio=0.50, max_sessions_to_sell=5)
    checks = rules.check(P, QUOTES, liquidity(), r)
    sym = by(checks, "max_symbol_weight")
    assert math.isclose(sym["VCB"].value, 500 / 1100) and not sym["VCB"].passed and sym["CTG"].passed
    sec = by(checks, "max_sector_weight")
    assert math.isclose(sec["Ngân hàng"].value, 900 / 1100) and not sec["Ngân hàng"].passed
    assert sec["Bất động sản"].passed
    margin = by(checks, "min_margin_ratio")["Danh mục"]
    assert math.isclose(margin.value, 500 / 1100) and not margin.passed
    days = by(checks, "max_sessions_to_sell")
    assert days["VCB"].value == 1 and days["VCB"].passed            # 10% × 2.000.000 = 200.000 cổ/phiên > 10.000 cổ
    assert days["VHM"].value == 5 and days["VHM"].passed            # 10% × 10.000 = 1.000 cổ/phiên → 5 phiên
    assert {c.subject for c in rules.failures(checks)} == {"VCB", "Ngân hàng", "Danh mục"}


def test_disabled_rules_and_no_debt():
    r = rules.Rules(max_symbol_weight=None, max_sector_weight=None, min_margin_ratio=0.5, max_sessions_to_sell=None)
    assert rules.check(Portfolio(P.holdings, cash=1e9), QUOTES, liquidity(), r) == []   # không vay: bỏ luật ký quỹ


def test_illiquid_symbol_cannot_be_sold():
    r = rules.Rules(max_symbol_weight=None, max_sector_weight=None, min_margin_ratio=None, max_sessions_to_sell=3)
    days = by(rules.check(P, QUOTES, liquidity(0.0), r), "max_sessions_to_sell")
    assert math.isinf(days["VHM"].value) and not days["VHM"].passed


def test_round_trip_and_validation(tmp_path):
    r = rules.from_dict({"max_symbol_weight": "0.25", "max_sector_weight": "", "min_margin_ratio": 0.4,
                         "max_sessions_to_sell": 4})
    assert r.max_sector_weight is None and r.max_sessions_to_sell == 4
    rules.save(r, tmp_path / "rules.json")
    assert rules.load(tmp_path / "rules.json") == r
    assert rules.load(tmp_path / "missing.json") == rules.Rules()
    for bad in ({"max_symbol_weight": 1.5}, {"min_margin_ratio": 0}, {"max_sessions_to_sell": 2.5}):
        with pytest.raises(PortfolioError):
            rules.from_dict(bad)
