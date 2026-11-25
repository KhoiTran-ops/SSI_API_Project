"""Trang Hiệu suất: TWR, IRR, so với VN-Index và với "không giao dịch gì", chi phí, PGR/PLR (nhật ký ở page_journal)."""

from __future__ import annotations

import plotly.graph_objects as go
from dash import Input, Output, dcc, html

from .. import performance as pf
from ..portfolio import PortfolioError
from . import data, ui
from .theme import LINE_CONFIG, base_figure, money, number, pannable, pct, tokens

PATH = "/hieu-suat"


def layout() -> html.Div:
    return html.Div([
        html.Div([html.H1("Hiệu suất"), html.Div(id="pfm-source")], className="page-head"),
        html.Div(id="pfm-lede", className="panel lede"),
        html.Div(id="pfm-body", className="stack", children=[
            html.Div(id="pfm-tiles", className="tiles"),
            ui.panel("Giá trị khoản đầu tư ban đầu 100 đồng", dcc.Graph(id="pfm-chart", config=LINE_CONFIG,
                                                                      style={"height": "300px"}),
                     hint="Danh mục theo TWR"),
            html.Div([
                ui.panel("Lãi/lỗ đã thực hiện", html.Div(id="pfm-realized")),
                ui.panel("Xu hướng chốt lãi và cắt lỗ", html.Div(id="pfm-disposition"),
                         hint="PGR và PLR theo Odean (1998)"),
            ], className="grid-wide"),
        ]),
        ui.method_note([
            "TWR nối lợi suất từng ngày, loại tác động của nạp và rút tiền. IRR tính theo dòng tiền nạp, rút và giá trị cuối kỳ.",
            "Kịch bản giữ nguyên: giữ danh mục sau ngày giao dịch đầu tiên, không giao dịch thêm.",
            "Vào ngày có lệnh bán, lãi từ mã bán là lãi đã thực hiện, lãi từ mã còn giữ là lãi chưa thực hiện; lỗ tương tự. "
            "PGR = lãi đã thực hiện / (lãi đã thực hiện + lãi chưa thực hiện). Kết quả kém tin cậy khi có ít lệnh bán.",
        ]),
    ], className="page")


# ---------------------------------------------------------------- hiển thị
def evaluate(j: pf.Journal) -> pf.PerformanceReport:
    symbols = sorted({t.symbol for t in j.transactions if t.symbol})
    with data.open_warehouse() as wh:
        m = pf.market_frame(wh, symbols, min(t.date for t in j.transactions))
    return pf.evaluate(j, m), m


def tiles(r: pf.PerformanceReport) -> list:
    years = max((r.end - r.start).days / 365, 1e-9)
    avg = r.daily["value"].clip(lower=0).mean()
    return [
        ui.tile("Lợi suất theo thời gian (TWR)", pct(r.twr, signed=True), f"VN-Index {pct(r.index_return, signed=True)}"),
        ui.tile("Lợi suất theo dòng tiền (IRR)", "–" if r.irr is None else pct(r.irr, signed=True) + "/năm"),
        ui.tile("Nếu giữ nguyên danh mục", pct(r.hold_return, signed=True),
                "TWR ngang kịch bản giữ nguyên" if abs(r.twr - r.hold_return) < 0.0005 else
                f"TWR {'cao' if r.twr > r.hold_return else 'thấp'} hơn kịch bản giữ nguyên "
                f"{pct(abs(r.twr - r.hold_return)).replace('%', ' điểm %')}"),
        ui.tile("Phí và thuế", money(r.costs), f"{pct(r.costs / avg / years if avg > 0 else 0)} giá trị bình quân mỗi năm"),
    ]


def lede(r: pf.PerformanceReport, j: pf.Journal) -> list:
    beat = r.twr - r.index_return
    # Chênh lệch là hiệu hai lợi suất, nên đơn vị là điểm %, không phải %.
    if abs(beat) < 0.0005:
        compare = [f" (VN-Index {pct(r.index_return, signed=True)}), ngang VN-Index"]
    else:
        compare = [f" (VN-Index {pct(r.index_return, signed=True)}), {'cao' if beat > 0 else 'thấp'} hơn ",
                   html.B(pct(abs(beat)).replace("%", " điểm %"))]
    return [f"Từ {r.start:%d/%m/%Y} đến {r.end:%d/%m/%Y}, lợi suất theo thời gian của tài khoản là ",
            html.B(pct(r.twr, signed=True)), *compare,
            ". Nếu giữ nguyên danh mục sau ngày giao dịch đầu tiên, lợi suất là ",
            html.B(pct(r.hold_return, signed=True)), "."]


def chart(r: pf.PerformanceReport, theme) -> go.Figure:
    t = tokens(theme)
    d = r.daily
    fig = base_figure(theme, 300)
    for name, col, color, width in (("Danh mục", "twr_index", t["series1"], 2.5), ("VN-Index", "index_level", t["muted"], 1.5),
                                     ("Giữ nguyên danh mục", "hold_index", t["series2"], 1.5)):
        fig.add_trace(go.Scatter(x=list(d.index), y=d[col], name=name, mode="lines", line=dict(color=color, width=width),
                                 hovertemplate=f"{name}: %{{y:,.1f}}<extra></extra>"))
    fig.add_hline(y=100, line=dict(color=t["axis"], width=1))
    fig.update_layout(showlegend=True, margin=dict(l=8, r=8, t=28, b=8))
    # Nhãn trục đổi theo mức phóng: năm → tháng/năm → ngày/tháng; khung di chuột luôn có ngày đầy đủ.
    fig.update_xaxes(showgrid=False, nticks=8, hoverformat="%d/%m/%Y", tickformatstops=[
        dict(dtickrange=[None, 20 * 86_400_000], value="%d/%m/%Y"),  # bước nhãn dưới 20 ngày
        dict(dtickrange=["M1", "M12"], value="%m/%Y"),
        dict(dtickrange=["M12", None], value="%Y")])
    ys = [v for col in ("twr_index", "index_level", "hold_index") for v in d[col]]
    return pannable(fig, theme, f"{d.index[0]:%Y-%m-%d}", f"{d.index[-1]:%Y-%m-%d}", ys, include=(100,))


def realized_table(r: pf.PerformanceReport) -> html.Div:
    if r.realized.empty:
        return html.P("Chưa có lệnh bán.", className="muted")
    rows = [[f"{x.date:%d/%m/%Y}", x.symbol, number(x.quantity), number(x.price), money(x.cost), money(x.proceeds),
             html.Span(money(x.gain, signed=True)), pct(x.gain / x.cost, signed=True) if x.cost else "–"]
            for x in r.realized.itertuples()]
    total = r.realized["gain"].sum()
    rows.append(["Tổng", "", "", "", "", "", money(total, signed=True), ""])
    return html.Div(ui.table(["Ngày", "Mã", "Khối lượng", "Giá bán", "Giá vốn", "Tiền bán ròng", "Lãi/lỗ", "%"], rows),
                    className="compare")


def disposition(r: pf.PerformanceReport) -> html.Div:
    if r.pgr is None and r.plr is None:
        return html.P("Cần ít nhất một ngày có lệnh bán và vẫn nắm giữ mã khác.", className="muted")
    d = r.disposition
    verdict = ("Tỷ lệ chốt lãi cao hơn tỷ lệ cắt lỗ" if d is not None and d > 0.05 else
               "Tỷ lệ cắt lỗ cao hơn tỷ lệ chốt lãi" if d is not None and d < -0.05 else
               "Chênh lệch giữa hai tỷ lệ không rõ rệt")
    return html.Div([
        html.Div([ui.tile("PGR", "–" if r.pgr is None else pct(r.pgr), "Tỷ lệ chốt lãi"),
                  ui.tile("PLR", "–" if r.plr is None else pct(r.plr), "Tỷ lệ cắt lỗ")],
                 style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "10px"}),
        html.P([html.B(verdict), f" (PGR − PLR = {pct(d, signed=True) if d is not None else '–'})."],
               style={"marginTop": "12px"}),
    ])


# ---------------------------------------------------------------- callbacks
def register(app) -> None:
    @app.callback(Output("pfm-lede", "children"), Output("pfm-body", "style"),
                  Output("pfm-tiles", "children"), Output("pfm-chart", "figure"), Output("pfm-realized", "children"),
                  Output("pfm-disposition", "children"), Output("pfm-source", "children"),
                  Input("journal", "data"), Input("theme", "data"))
    def render(current, theme):
        j = pf.from_dict(current)
        source = html.Span("Dữ liệu mô phỏng" if j.simulated else (f"Lệnh từ {j.label}" if j.label.startswith("SSI") else "Nhập thủ công"), className="chip")
        hide = {"display": "none"}
        if not j.transactions:
            return ["Nhật ký trống. ", dcc.Link("Ghi giao dịch", href="/nhat-ky")], hide, [], {}, None, None, source
        try:
            r, _ = evaluate(j)
        except PortfolioError as exc:
            return f"{exc}", hide, [], {}, None, None, source
        return lede(r, j), {"display": "flex"}, tiles(r), chart(r, theme), realized_table(r), disposition(r), source
