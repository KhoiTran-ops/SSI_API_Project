"""Trang Tổng quan: tình trạng tài khoản ký quỹ, mã đang giữ, phiên gần nhất và mức chịu một phiên giảm sàn. Chỉ xem."""

from __future__ import annotations

import math

from dash import Input, Output, dcc, html

from .. import context, rules, stress
from ..portfolio import from_dict
from . import data, page_portfolio, page_stress, ui
from .theme import GRAPH_CONFIG, money, number, pct

PATH = "/"


def layout() -> html.Div:
    return html.Div([
        html.Div([html.H1("Tổng quan"),
                  html.Div([html.Div(id="ov-source"),
                            dcc.Link("Sửa danh mục", href=page_portfolio.PATH, className="btn")], className="row",
                           style={"alignItems": "center"})], className="page-head"),
        html.Div(id="ov-tiles", className="tiles"),
        html.Div([
            ui.panel("Cổ phiếu đang nắm giữ", html.Div(id="ov-holdings")),
            ui.panel("Tỷ trọng", dcc.Graph(id="ov-weights", config=GRAPH_CONFIG)),
        ], className="grid-wide"),
        html.Div([
            html.Div(id="ov-market", className="panel link-card"),
            html.Div(id="ov-floor", className="panel link-card"),
        ], className="grid-even"),
    ], className="page")


def holdings_table(s) -> html.Div:
    if not s["rows"]:
        return html.P(["Danh mục trống. ", dcc.Link("Thêm cổ phiếu", href=page_portfolio.PATH)], className="muted")
    rows = []
    for r in sorted(s["rows"], key=lambda r: -(r["value"] or 0)):
        sessions = r["floor_sessions"]
        rows.append([html.B(r["symbol"]), number(r["quantity"]), number(r["price"]), money(r["value"]),
                     pct(r["weight"]) if r["weight"] is not None else "–",
                     pct(r["pnl"], signed=True) if r["pnl"] is not None else "–",
                     "Không bán được" if sessions == "inf" else ("–" if sessions is None else f"{number(sessions)} phiên")])
    return ui.table(["Mã", "Khối lượng", "Giá", "Giá trị", "Tỷ trọng", "Lãi/lỗ", "Phiên bán hết"], rows)


def tiles(p, s, failed: int, active: int) -> list:
    ratio_tile = page_portfolio.tiles(p, s)[3]
    rule_tile = ui.tile("Luật rủi ro", "Chưa bật luật" if not active else
                        ("Đạt tất cả" if not failed else f"{failed} vi phạm"),
                        dcc.Link("Xem luật", href="/luat"),
                        badge=None if not active else ui.pill("fail" if failed else "pass"))
    return [ratio_tile, ui.tile("Tài sản ròng", money(s["equity"]), f"Giá trị danh mục {money(s['lmv'])} · {len(p.holdings)} mã"),
            ui.tile("Dư nợ ròng", money(s["net_debt"])), rule_tile]


def market_card(p) -> list:
    day = data.last_session()
    if day is None:
        return [html.H2("Phiên gần nhất"), html.P("Chưa có dữ liệu.", className="muted")]
    q = data.quotes(p.symbols)
    weights = {h.symbol: h.quantity for h in p.holdings if h.symbol in q.index}
    with data.open_warehouse() as wh:
        b = context.breadth(wh, day)
        try:
            a = context.attribute_day(wh, weights, day) if weights else None
        except LookupError:
            a = None
    port = pct(a.portfolio_return, 2, signed=True) if a is not None and len(a.table) else "–"
    return [html.Div([html.H2(f"Phiên {day:%d/%m/%Y}"), dcc.Link("Thị trường ›", href="/thi-truong")],
                     className="card-head"),
            html.Div([_kv("VN-Index", pct(b.index_pct, 2, signed=True) if b.index_pct is not None else "–"),
                      _kv("Danh mục", port),
                      _kv("Tăng / giảm", f"{number(b.advances)} / {number(b.declines)}" if b.advances is not None else "–")],
                     className="kv-row")]


def floor_card(p) -> list:
    head = html.Div([html.H2("Nếu giảm sàn 1 phiên"), dcc.Link("Kịch bản ›", href=f"{page_stress.PATH}?kb=floor1")],
                    className="card-head")
    q = data.quotes(p.symbols)
    held = p.with_holdings([h for h in p.holdings if h.symbol in q.index])
    if not held.holdings:
        return [head, html.P("Danh mục trống.", className="muted")]
    r = stress.simulate(held, q, data.liquidity(), stress.Scenario.floor(1), stress.Assumptions())
    last = r.sessions.iloc[-1]
    ratio = "–" if math.isinf(last["ratio"]) else pct(last["ratio"])
    change = r.sessions["market_value"].iloc[-1] / r.sessions["market_value"].iloc[0] - 1 \
        if r.sessions["market_value"].iloc[0] else 0.0
    return [head, html.Div([_kv("Tỷ lệ ký quỹ", html.Span([ratio, " ", ui.pill(last["status"])])),
                            _kv("Giá trị danh mục", pct(change, signed=True)),
                            _kv("Tài sản ròng", money(r.equity_change, signed=True))], className="kv-row")]


def _kv(label, value) -> html.Div:
    return html.Div([html.Span(label, className="kv-label"), html.Span(value, className="kv-value")], className="kv")


def register(app) -> None:
    @app.callback(Output("ov-tiles", "children"), Output("ov-holdings", "children"),
                  Output("ov-weights", "figure"), Output("ov-weights", "style"), Output("ov-source", "children"),
                  Output("ov-market", "children"), Output("ov-floor", "children"),
                  Input("portfolio", "data"), Input("rules", "data"), Input("theme", "data"))
    def render(current, current_rules, theme):
        p = from_dict(current)
        s = page_portfolio.summary(p)
        q = data.quotes(p.symbols)
        checks = rules.check(p, q, data.liquidity(), rules.from_dict(current_rules)) if p.holdings else []
        fig = page_portfolio.weights_figure(s, theme)
        return (tiles(p, s, len(rules.failures(checks)), len(checks)), holdings_table(s),
                fig, {"height": f"{fig.layout.height}px"}, page_portfolio.source_chips(s), market_card(p), floor_card(p))
