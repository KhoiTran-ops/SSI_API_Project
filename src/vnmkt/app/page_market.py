"""Trang Thị trường: độ rộng thị trường một phiên, biến động của danh mục tách theo thị trường/ngành/riêng, khối ngoại."""

from __future__ import annotations

import math

import plotly.graph_objects as go
from dash import Input, Output, dcc, html

from .. import context
from ..portfolio import from_dict
from . import data, ui
from .theme import GRAPH_CONFIG, base_figure, money, number, pct, tokens

PATH = "/thi-truong"
Z_ALERT = 2.0  # |z| > 2: ngoài khoảng ~95% các tổng 5 phiên đã gặp của chính mã đó (nếu gần phân phối chuẩn)


def layout() -> html.Div:
    with data.open_warehouse() as wh:
        days = context.sessions(wh, 30)
    options = [{"label": f"{d:%d/%m/%Y}", "value": d.isoformat()} for d in days]
    return html.Div([
        html.Div([html.H1("Thị trường"),
                  html.Div(dcc.Dropdown(id="mk-day", options=options, value=options[0]["value"] if options else None,
                                        clearable=False, searchable=False, className="dropdown"),
                           style={"width": "170px"})], className="page-head"),
        html.Div(id="mk-lede", className="panel lede"),
        html.Div(id="mk-tiles", className="tiles"),
        html.Div([
            ui.panel("Đóng góp vào lợi suất danh mục", dcc.Graph(id="mk-parts", config=GRAPH_CONFIG)),
            ui.panel("Khối ngoại theo mã nắm giữ", html.Div(id="mk-foreign"),
                     hint=f"Đánh dấu khi |z| > {number(Z_ALERT)}"),
        ], className="grid-even"),
        ui.method_note([
            "Lợi suất mỗi mã trong phiên = phần thị trường (beta × lợi suất VN-Index, beta từ 250 phiên trước) + phần ngành "
            "(lợi suất theo vốn hóa của nhóm ngành ICB cấp 2 trên HOSE, trừ phần thị trường) + phần riêng (phần còn lại). "
            "Nhân lợi suất từng mã với tỷ trọng đầu phiên rồi cộng để tính lợi suất danh mục.",
            "Với mã HNX và UPCoM, dùng lợi suất của nhóm ngành tương ứng trên HOSE.",
            "Giá trị mua ròng của khối ngoại = giá trị mua − giá trị bán. z = (tổng 5 phiên gần nhất − trung bình) / độ lệch chuẩn của "
            "các tổng 5 phiên trong 120 phiên trước. Bỏ qua phiên thiếu số liệu, không tính là 0.",
            "Kết quả mô tả phiên đã qua, không giải thích nguyên nhân hay dự báo.",
        ]),
    ], className="page")


def tiles(b: context.Breadth, a: context.DayAttribution | None) -> list:
    adv = f"{number(b.advances)} tăng · {number(b.declines)} giảm" if b.advances is not None else "–"
    edge = f"{number(b.ceiling)} trần · {number(b.floor)} sàn" if b.ceiling is not None else None
    return [
        ui.tile("VN-Index", number(b.index_value, 2) if b.index_value else "–", pct(b.index_pct, 2, signed=True)),
        ui.tile("Độ rộng HOSE", adv, edge),
        ui.tile("Khối ngoại mua ròng trên HOSE", money(b.foreign_net, signed=True) if b.foreign_net is not None else "Thiếu số liệu",
                f"Giá trị khớp lệnh {money(b.match_val)}" if b.match_val else None),
        ui.tile("Lợi suất trong phiên", pct(a.portfolio_return, 2, signed=True) if a and len(a.table) else "–"),
    ]


def lede(b: context.Breadth, a: context.DayAttribution | None, radar) -> list:
    parts = [f"Phiên {b.day:%d/%m/%Y}, VN-Index {pct(b.index_pct, 2, signed=True)}"]
    if b.advances is not None:
        parts.append(f" với {number(b.advances)} mã tăng và {number(b.declines)} mã giảm trên HOSE")
    parts.append(". ")
    if a is not None and len(a.table):
        p = a.parts
        contrib = (a.table["weight"] * a.table["ret"]).sort_values()
        lead = contrib.index[-1] if abs(contrib.iloc[-1]) >= abs(contrib.iloc[0]) else contrib.index[0]
        parts += ["Danh mục ", html.B(pct(a.portfolio_return, 2, signed=True)), ": thị trường ",
                  pct(p["market"], 2, signed=True), ", nhóm ngành ", pct(p["sector_part"], 2, signed=True), ", phần riêng ",
                  pct(p["specific"], 2, signed=True), ". Mã tác động mạnh nhất: ", html.B(lead),
                  f" ({pct(float(contrib[lead]), 2, signed=True)})."]
    alerts = [s for s, z in radar["z"].items() if z is not None and not math.isnan(z) and abs(z) > Z_ALERT] \
        if len(radar) else []
    if alerts:
        parts += [" Giá trị mua ròng của khối ngoại vượt ngưỡng thống kê ở ", html.B(", ".join(alerts)), "."]
    return parts


def parts_figure(a: context.DayAttribution, theme) -> go.Figure:
    t = tokens(theme)
    tab = a.table.copy()
    for col in ("market", "sector_part", "specific"):
        tab[col] = tab[col] * tab["weight"]
    tab = tab.iloc[(tab["market"] + tab["sector_part"] + tab["specific"]).argsort()]
    fig = base_figure(theme, height=max(200, 44 * len(tab) + 60))
    for name, col, color in (("Thị trường", "market", t["series1"]), ("Nhóm ngành", "sector_part", t["series2"]),
                             ("Riêng mã", "specific", t["series3"])):
        fig.add_trace(go.Bar(x=tab[col], y=tab.index, orientation="h", name=name, marker=dict(color=color),
                             customdata=[pct(v, 2, signed=True) for v in tab[col]],
                             hovertemplate=f"{name}: %{{customdata}}<extra></extra>"))
    fig.add_vline(x=0, line=dict(color=t["axis"], width=1))
    fig.update_layout(barmode="relative", showlegend=True, hovermode="y unified", margin=dict(l=8, r=16, t=28, b=8))
    fig.update_xaxes(tickformat=".2%")
    fig.update_yaxes(showgrid=False, tickfont=dict(color=t["text"]))
    return fig


def foreign_table(radar) -> html.Div:
    if not len(radar):
        return html.P("Không có số liệu khối ngoại.", className="muted")
    rows = []
    for s, r in radar.iterrows():
        z = r["z"]
        flag = ui.pill("near", "Vượt ngưỡng") if z is not None and not math.isnan(z) and abs(z) > Z_ALERT else ""
        rows.append([s, money(r["net5"], signed=True) if r["net5"] is not None else "–",
                     f"{int(r['days5'])}/5", money(r["net20"], signed=True) if r["net20"] is not None else "–",
                     "–" if z is None or math.isnan(z) else number(z, 1), pct(r["room_used"], 0) if r["room_used"] is not None
                     and not math.isnan(r["room_used"]) else "–", flag])
    return html.Div(ui.table(["Mã", "Mua ròng 5 phiên", "Số phiên có dữ liệu", "Mua ròng 20 phiên", "z", "Room đã dùng", ""], rows),
                    className="compare")


def register(app) -> None:
    @app.callback(Output("mk-lede", "children"), Output("mk-tiles", "children"), Output("mk-parts", "figure"),
                  Output("mk-parts", "style"), Output("mk-foreign", "children"),
                  Input("mk-day", "value"), Input("portfolio", "data"), Input("theme", "data"))
    def run(day, current, theme):
        from datetime import date as _date
        if not day:
            return "Chưa có dữ liệu phiên giao dịch.", [], {}, {}, None
        d = _date.fromisoformat(day)
        p = from_dict(current)
        q = data.quotes(p.symbols)
        weights = {h.symbol: h.quantity for h in p.holdings if h.symbol in q.index}
        with data.open_warehouse() as wh:
            b = context.breadth(wh, d)
            try:
                a = context.attribute_day(wh, weights, d) if weights else None
            except LookupError:
                a = None
            radar = context.foreign_radar(wh, list(weights), d)
        fig = parts_figure(a, theme) if a is not None and len(a.table) else base_figure(theme, 160)
        return (lede(b, a, radar), tiles(b, a), fig, {"height": f"{fig.layout.height}px"}, foreign_table(radar))
