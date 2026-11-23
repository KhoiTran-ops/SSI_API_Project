"""Trang Rủi ro và hiệu suất: phân rã rủi ro theo Euler, beta, và phân rã Brinson theo nhóm ngành."""

from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, dcc, html

from .. import risk
from ..portfolio import from_dict
from . import data, ui
from .theme import GRAPH_CONFIG, base_figure, number, pct, tokens

PATH = "/rui-ro"
WINDOWS = [("63", "3 tháng"), ("125", "6 tháng"), ("250", "1 năm"), ("750", "3 năm")]


def layout() -> html.Div:
    return html.Div([
        html.Div([html.H1("Rủi ro"),
                  ui.segmented("rk-window", WINDOWS, "250")], className="page-head"),
        html.Div(id="rk-lede", className="panel lede"),
        html.Div(id="rk-body", className="stack", children=[
            html.Div(id="rk-tiles", className="tiles"),
            html.Div([
                ui.panel("Tỷ trọng và đóng góp rủi ro", dcc.Graph(id="rk-contrib", config=GRAPH_CONFIG)),
                ui.panel("Chỉ số rủi ro theo mã", html.Div(id="rk-table")),
            ], className="grid-narrow-wide"),
            ui.panel(f"Phân rã theo nhóm ngành so với {risk.REFERENCE_NAME}", html.Div(id="rk-brinson")),
            html.Div([html.Details([html.Summary("Cách tính và giới hạn"), html.Div(id="rk-method", className="disclosure-body")],
                                   className="disclosure")], className="panel"),
        ]),
    ], className="page")


def _holdings(p):
    q = data.quotes(p.symbols)
    held = [h for h in p.holdings if h.symbol in q.index]
    values = pd.Series({h.symbol: h.quantity * float(q.loc[h.symbol, "price"]) for h in held})
    return q, held, values


def tiles(rep: risk.RiskReport, att: risk.Attribution, leverage: float) -> list:
    if math.isinf(leverage):
        on_equity = ui.tile("Độ biến động năm trên tài sản ròng", "–", "Tài sản ròng bằng hoặc dưới 0")
    else:
        on_equity = ui.tile("Độ biến động năm trên tài sản ròng", pct(rep.vol * leverage),
                            f"Đòn bẩy {number(leverage, 2)} lần" if leverage > 1.001 else "Không dùng đòn bẩy")
    return [
        ui.tile("Độ biến động năm", pct(rep.vol), f"VN-Index {pct(rep.market_vol)}"),
        on_equity,
        ui.tile("Beta so với VN-Index", number(rep.beta, 2),
                f"{pct(rep.systematic_share, 0)} phương sai do thị trường"),
        ui.tile("Lợi suất giai đoạn", pct(att.portfolio_return, signed=True), "Giả định giữ nguyên danh mục hiện tại"),
    ]


def lede(rep: risk.RiskReport, att: risk.Attribution) -> list:
    t = rep.table.sort_values("rc_share", ascending=False)
    top = t.index[0]
    gap = t.loc[top, "rc_share"] - t.loc[top, "weight"]
    parts = [f"Từ {rep.start:%d/%m/%Y} đến {rep.end:%d/%m/%Y} ({rep.sessions} phiên), ", html.B(top),
             f" có tỷ trọng {pct(t.loc[top, 'weight'])} và đóng góp {pct(t.loc[top, 'rc_share'])} vào rủi ro danh mục"]
    parts.append(" (cao hơn tỷ trọng)" if gap > 0.01 else "")
    parts += [". Nếu giữ nguyên danh mục hiện tại suốt giai đoạn, lợi suất là ", html.B(pct(att.portfolio_return,
              signed=True)), f". Cùng kỳ, {risk.REFERENCE_NAME} đạt {pct(att.benchmark_return, signed=True)}."]
    return parts


def contrib_figure(rep: risk.RiskReport, theme) -> go.Figure:
    t = tokens(theme)
    tab = rep.table.sort_values("rc_share")
    fig = base_figure(theme, height=max(180, 44 * len(tab) + 40))
    for name, col, color in (("Tỷ trọng", "weight", t["muted"]), ("Đóng góp rủi ro", "rc_share", t["series1"])):
        fig.add_trace(go.Bar(x=tab[col], y=tab.index, orientation="h", name=name, marker=dict(color=color, cornerradius=4),
                             customdata=[pct(v) for v in tab[col]], hovertemplate=f"{name}: %{{customdata}}<extra></extra>"))
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.1, showlegend=True, hovermode="y unified",
                      margin=dict(l=8, r=16, t=28, b=8))
    fig.update_xaxes(tickformat=".0%", rangemode="tozero")
    fig.update_yaxes(showgrid=False, tickfont=dict(color=t["text"]))
    return fig


def risk_table(rep: risk.RiskReport) -> html.Div:
    tab = rep.table.sort_values("rc_share", ascending=False)
    rows = [[s, pct(r.weight), pct(r.vol), number(r.beta, 2), pct(r.rc_share),
             pct(r["own"] / rep.vol), pct(r["corr"] / rep.vol)] for s, r in tab.iterrows()]
    rows.append(["Danh mục", pct(1.0), pct(rep.vol), number(rep.beta, 2), pct(1.0),
                 pct(tab["own"].sum() / rep.vol), pct(tab["corr"].sum() / rep.vol)])
    return html.Div(ui.table(["Mã", "Tỷ trọng", "Độ biến động năm", "Beta", "Đóng góp rủi ro", "Phần riêng", "Phần tương quan"],
                             rows), className="compare")


def brinson_view(att: risk.Attribution) -> html.Div:
    tab = att.table
    held = tab[(tab["wp"] > 0) | (tab["allocation"].abs() >= 0.005)]
    rest = tab.drop(held.index)
    rows = [[s, pct(r.wp), pct(r.wb), pct(r.rp, signed=True) if r.wp > 0 else "–", pct(r.rb, signed=True),
             pct(r.allocation, signed=True), pct(r.selection, signed=True), pct(r.interaction, signed=True)]
            for s, r in held.iterrows()]
    if len(rest):
        rows.append([f"Nhóm khác ({len(rest)})", pct(rest["wp"].sum()), pct(rest["wb"].sum()), "–", "–",
                     pct(rest["allocation"].sum(), signed=True), "–", "–"])
    total = tab[["allocation", "selection", "interaction"]].sum()
    rows.append(["Tổng", pct(1.0), pct(1.0), pct(att.portfolio_return, signed=True),
                 pct(att.benchmark_return, signed=True), pct(total["allocation"], signed=True),
                 pct(total["selection"], signed=True), pct(total["interaction"], signed=True)])
    summary = (f"Chênh lệch {pct(att.active, signed=True)} = phân bổ {pct(total['allocation'], signed=True)} "
               f"+ chọn mã {pct(total['selection'], signed=True)} + tương tác {pct(total['interaction'], signed=True)}.")
    return html.Div([html.P(summary, className="text-2", style={"marginBottom": "10px"}),
                     ui.table(["Nhóm ngành", "Tỷ trọng danh mục", "Tỷ trọng chỉ số", "Lợi suất danh mục",
                               "Lợi suất chỉ số", "Phân bổ", "Chọn mã", "Tương tác"], rows)], className="compare")


def method(rep: risk.RiskReport, att: risk.Attribution) -> list:
    return [html.Ul([
        html.Li("Tính lợi suất ngày từ giá đóng cửa đã điều chỉnh theo sự kiện quyền, gồm cổ tức tiền mặt. Dùng 250 phiên "
                "để quy đổi độ biến động và hiệp phương sai về năm. Chỉ dùng phiên có giá của tất cả các mã."),
        html.Li("Theo Euler, đóng góp rủi ro của mỗi mã = tỷ trọng × độ biến động biên. Tổng đóng góp bằng độ biến động danh mục."),
        html.Li("Độ biến động năm trên tài sản ròng = độ biến động phần cổ phiếu × đòn bẩy (giá trị danh mục / tài sản ròng)."),
        html.Li("Phân rã theo Brinson, Hood và Beebower (1986) dùng nhóm ngành ICB cấp 2 và giả định giữ nguyên khối lượng "
                f"hiện tại suốt giai đoạn. Mốc đối chiếu là {risk.REFERENCE_NAME}, tính từ mọi cổ phiếu HOSE với trọng số "
                "bằng số cổ phiếu niêm yết hiện tại × giá đầu kỳ."),
        html.Li("Kết quả đo lường rủi ro trong quá khứ, không dùng để dự báo."),
    ], style={"margin": 0, "paddingLeft": "18px", "color": "var(--text-2)", "fontSize": "13px", "lineHeight": 1.7})]


def register(app) -> None:
    @app.callback(Output("rk-lede", "children"), Output("rk-body", "style"), Output("rk-tiles", "children"),
                  Output("rk-contrib", "figure"), Output("rk-contrib", "style"), Output("rk-table", "children"),
                  Output("rk-brinson", "children"), Output("rk-method", "children"),
                  Input("portfolio", "data"), Input("rk-window", "value"), Input("theme", "data"))
    def run(current, window, theme):
        p = from_dict(current)
        hide = {"display": "none"}
        if not p.holdings:
            return "Thêm cổ phiếu ở trang Danh mục để xem rủi ro.", hide, [], {}, {}, None, None, None
        q, held, values = _holdings(p)
        sessions = int(window or 250)
        try:
            with data.open_warehouse() as wh:
                rets, market = risk.returns(wh, list(values.index), sessions)
                rep = risk.risk_report(rets, market, values / values.sum())
                att = risk.attribution(wh, {h.symbol: h.quantity for h in held}, sessions)
        except LookupError as exc:
            return f"Không đủ dữ liệu cho giai đoạn này ({exc}).", hide, [], {}, {}, None, None, None
        lmv = float(values.sum())
        equity = lmv + p.cash - p.debt
        leverage = lmv / equity if equity > 0 else math.inf
        fig = contrib_figure(rep, theme)
        return (lede(rep, att), {"display": "flex"}, tiles(rep, att, leverage), fig,
                {"height": f"{fig.layout.height}px"}, risk_table(rep), brinson_view(att), method(rep, att))

