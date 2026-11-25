"""Trang Kịch bản giảm giá: giảm sàn liên tiếp, đợt giảm 2022, hoặc mức giảm tự nhập, có ràng buộc thanh khoản."""

from __future__ import annotations

import math

import dash_ag_grid as dag
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, dcc, html

from .. import risk, stress
from ..market import MAX_CHAIN_POSITION
from ..portfolio import from_dict
from . import data, ui
from .theme import GRAPH_CONFIG, LINE_CONFIG, base_figure, money, number, pannable, pct, tokens

PATH = "/kich-ban"
SCENARIOS = [("floor1", "1 phiên giảm sàn"), ("floor3", "3 phiên giảm sàn"), ("floor5", "5 phiên giảm sàn"),
             ("y2022", "Đợt giảm 2022"), ("custom", "Tự nhập")]
BOARD_NAMES = {"HOSE": "HOSE", "HNX": "HNX", "UPCOM": "UPCoM"}


def layout(params: dict | None = None) -> html.Div:
    a = stress.Assumptions()
    kind = (params or {}).get("kb")
    kind = kind if kind in dict(SCENARIOS) else "floor3"
    return html.Div([
        html.Div([html.H1("Kịch bản giảm giá")], className="page-head"),
        html.Div([
            html.Div([
                ui.panel("Kịch bản", ui.segmented("st-scenario", SCENARIOS, kind, vertical=True),
                         html.Div([
                             ui.field("Giảm mỗi phiên (%)", ui.number_input("st-drop", 5)),
                             ui.field("Số phiên", ui.number_input("st-sessions", 5, step=1, min_=1, max_=60)),
                         ], id="st-custom", className="fields", style={"display": "none"})),
                html.Div(ui.panel("Mức giảm theo mã", _drops_grid()), id="st-drops-wrap", style={"display": "none"}),
                html.Div(html.Details([html.Summary("Giả định"), html.Div([
                    ui.field("Tỷ lệ tham gia khớp lệnh (%)",
                             ui.number_input("st-part", a.participation * 100, min_=1, max_=100)),
                    ui.field("Số phiên ân hạn", ui.number_input("st-grace", a.call_grace, step=1, min_=1, max_=10)),
                    ui.field("Thuế bán (%)", ui.number_input("st-tax", a.tax_rate * 100, min_=0, max_=5)),
                    ui.field("Phí giao dịch (%)", ui.number_input("st-fee", a.fee_rate * 100, min_=0, max_=5)),
                ], className="fields-col disclosure-body")], className="disclosure"), className="panel"),
            ], className="side stack"),
            html.Div([
                dcc.Store(id="st-overrides", data={}),
                html.Div(id="st-lede", className="panel lede"),
                html.Div(id="st-tiles", className="tiles"),
                ui.panel("Tỷ lệ ký quỹ qua từng phiên", dcc.Graph(id="st-ratio", config=LINE_CONFIG,
                                                                  style={"height": "300px"})),
                html.Div(ui.panel("Bán giải chấp theo phiên", dcc.Graph(id="st-sales", config=GRAPH_CONFIG,
                                                                        style={"height": "240px"})), id="st-sales-wrap"),
                html.Div([
                    html.Details([html.Summary("Chi tiết theo phiên"), html.Div(id="st-table", className="disclosure-body")],
                                 className="disclosure"),
                    html.Details([html.Summary("Cách tính và giới hạn"), html.Div([
                        html.Div(id="st-liquidity"),
                        html.Div(id="st-notes", className="notes", style={"marginTop": "12px"}),
                    ], className="disclosure-body")], className="disclosure"),
                ], className="panel"),
            ], className="stack"),
        ], className="split"),
    ], className="page")


def _drops_grid() -> dag.AgGrid:
    return dag.AgGrid(
        id="st-drops", rowData=[], getRowId="params.data.symbol",
        columnDefs=[{"field": "sector", "headerName": "Nhóm ngành", "flex": 2, "minWidth": 160, "sort": "asc"},
                    {"field": "symbol", "headerName": "Mã", "flex": 1, "minWidth": 80, "cellStyle": {"fontWeight": 600}},
                    {"field": "drop", "headerName": "Giảm mỗi phiên (%)", "flex": 1, "minWidth": 140, "editable": True,
                     "type": "rightAligned", "cellClass": "cell-edit", "cellEditor": "agNumberCellEditor",
                     "cellEditorParams": {"min": 0, "max": 15, "precision": 2}}],
        defaultColDef={"sortable": True, "resizable": True, "suppressMovable": True},
        dashGridOptions={"domLayout": "autoHeight", "singleClickEdit": True, "stopEditingWhenCellsLoseFocus": True},
        className="ag-theme-quartz", style={"width": "100%"})


def _clamp_drop(v) -> float:
    return min(max(float(v if v is not None else 5), 0.0), 15.0) / 100


# ---------------------------------------------------------------- dựng kịch bản và lời giải thích
def build_scenario(kind: str, drop, sessions, p, rows=None) -> tuple[stress.Scenario, list[str]]:
    if kind.startswith("floor"):
        n = int(kind[5:])
        return stress.Scenario.floor(n), [f"Phiên {i}" for i in range(n + 1)]
    if kind == "y2022":
        start, end, returns, volume, replaced = data.path_2022(p.symbols)
        label = f"diễn biến giá từ {start:%d/%m/%Y} đến {end:%d/%m/%Y}"
        dates = [f"{start:%d/%m/%Y}"] + [f"{d:%d/%m/%Y}" for d in returns.index]
        return stress.Scenario.path(returns, volume, label, replaced), dates
    n = int(min(max(sessions or 5, 1), 60))
    d = _clamp_drop(drop)
    drops = {r["symbol"]: _clamp_drop(r.get("drop")) for r in rows or [] if r.get("symbol") in p.symbols}
    if drops and all(abs(v - d) < 1e-12 for v in drops.values()):
        drops = None
    return stress.Scenario.uniform(d, n, drops), [f"Phiên {i}" for i in range(n + 1)]


def _when(index: int | None, labels: list[str], floor_kind: bool) -> str:
    if index is None:
        return ""
    if index == 0:
        return "hiện tại"
    return f"phiên {index}" if floor_kind else f"ngày {labels[index]} (phiên {index})"


def lede(p, r: stress.StressResult, labels: list[str], kind: str) -> list:
    """Câu tóm tắt sinh từ số liệu kết quả (không dùng mô hình ngôn ngữ)."""
    custom = (f"Nếu từng mã giảm theo mức đã nhập trong {r.scenario.sessions} phiên" if r.scenario.drops else
              f"Nếu giá giảm {pct(r.scenario.drop)} mỗi phiên trong {r.scenario.sessions} phiên")
    scenario = {"y2022": f"Theo {r.scenario.label}", "custom": custom}.get(kind, f"Nếu mọi mã giảm sàn {r.scenario.sessions} phiên liên tiếp")
    floor_kind = kind != "y2022"
    change = r.equity_change / r.start_equity if r.start_equity > 0 else None
    loss = [". Tài sản ròng ", html.B(f"giảm {money(-r.equity_change)}" if r.equity_change < 0 else "không giảm"),
            f" ({pct(change)})" if change is not None and r.equity_change < 0 else ""]
    if p.debt <= p.cash:
        return [scenario, ", tài khoản không có dư nợ ròng nên không bị gọi ký quỹ", *loss, "."]
    if r.first_call is None:
        return [scenario, ", tỷ lệ ký quỹ ", html.B("không xuống dưới ngưỡng gọi ký quỹ"),
                f" (thấp nhất {pct(r.sessions['ratio'].min())})", *loss, "."]
    if r.first_call == 0:
        parts = ["Tài khoản ", html.B("hiện tại đã dưới ngưỡng gọi ký quỹ"),
                 f" (tỷ lệ ký quỹ {pct(r.sessions.loc[0, 'ratio'])}). ", scenario]
    else:
        parts = [scenario, ", tài khoản bị gọi ký quỹ ở ", html.B(_when(r.first_call, labels, floor_kind))]
    if r.first_force is not None:
        sold = r.sales["value"].sum()
        joiner = ", tài khoản bị bán giải chấp từ " if r.first_call == 0 else " và bị bán giải chấp từ "
        parts += [joiner, html.B(_when(r.first_force, labels, floor_kind)),
                  f". Tổng giá trị đã bán {money(sold)}"]
        if r.unsold_value > 0:
            parts += [". Còn ", html.B(f"{money(r.unsold_value)} chưa bán được"), " do thiếu thanh khoản"]
    else:
        parts += [", tài khoản chưa bị bán giải chấp" if r.first_call == 0
                  else " nhưng chưa bị bán giải chấp"]
    return parts + loss + ["."]


def tiles(r: stress.StressResult, labels: list[str], kind: str) -> list:
    floor_kind = kind != "y2022"

    def when(i):
        if i is None:
            return "Không"
        if i == 0:
            return "Hiện tại"
        return f"Phiên {i}" if floor_kind else labels[i]

    end = r.sessions.iloc[-1]
    change = r.equity_change / r.start_equity if r.start_equity > 0 else None
    return [
        ui.tile("Gọi ký quỹ", when(r.first_call), None if r.first_call is None else
                f"Tỷ lệ ký quỹ {pct(r.sessions.loc[r.first_call, 'ratio'])}"),
        ui.tile("Bán giải chấp", when(r.first_force),
                f"Đã bán {money(r.sales['value'].sum())}" if r.first_force else None),
        ui.tile("Tài sản ròng cuối kịch bản", money(end["equity"]),
                f"{money(r.equity_change, signed=True)} ({pct(change, signed=True)})" if change is not None else None),
        ui.tile("Chưa bán được", money(r.unsold_value) if r.first_force else "–",
                "Giá trị chưa khớp ở phiên cuối" if r.first_force else "Không phát sinh bán giải chấp"),
    ]


def ratio_figure(p, r: stress.StressResult, labels: list[str], theme) -> go.Figure:
    t = tokens(theme)
    s = r.sessions
    fig = base_figure(theme, height=300)
    finite = s["ratio"].replace([math.inf, -math.inf], math.nan)
    fig.add_hrect(y0=-10, y1=p.force_ratio,
                  fillcolor=t["band"], line_width=0, layer="below")
    for y, color, name in ((p.call_ratio, t["serious"], "Gọi ký quỹ"), (p.force_ratio, t["critical"], "Bán giải chấp")):
        fig.add_hline(y=y, line=dict(color=color, width=1.5))
        fig.add_annotation(x=1, xref="paper", y=y, yref="y", text=f"{name} {pct(y, 0)}", showarrow=False,
                           xanchor="right", yanchor="bottom", font=dict(color=t["text2"], size=11),
                           bgcolor="rgba(0,0,0,0)")
    status_text = {k: v[1] for k, v in ui.STATUS.items()}
    fig.add_trace(go.Scatter(
        x=labels, y=finite, mode="lines+markers" if len(s) <= 40 else "lines",
        line=dict(color=t["series1"], width=2, shape="linear"),
        marker=dict(size=8, color=t["series1"], line=dict(color=t["surface"], width=2)),
        customdata=[[status_text.get(st, st), money(e), money(v)] for st, e, v in
                    zip(s["status"], s["equity"], s["market_value"])],
        hovertemplate="Tỷ lệ ký quỹ %{y:.1%}<br>%{customdata[0]}<br>Tài sản ròng %{customdata[1]}"
                      "<br>Giá trị danh mục %{customdata[2]}<extra></extra>"))
    fig.update_yaxes(tickformat=".0%")
    fig.update_xaxes(type="category", nticks=8, showgrid=False)
    return pannable(fig, theme, 0, len(s) - 1, finite, include=(p.call_ratio, p.force_ratio))


def sales_figure(r: stress.StressResult, labels: list[str], theme) -> go.Figure:
    t = tokens(theme)
    s = r.sessions[(r.sessions["sold_value"] > 0) | (r.sessions["shortfall_value"] > 0)]  # chỉ phiên có bán
    fig = base_figure(theme, height=240)
    x = [labels[i] for i in s["session"]]
    fig.add_trace(go.Bar(x=x, y=s["sold_value"] / 1e6, name="Bán được", marker=dict(color=t["series1"], cornerradius=4),
                         customdata=[money(v) for v in s["sold_value"]],
                         hovertemplate="Bán được %{customdata}<extra></extra>"))
    fig.add_trace(go.Bar(x=x, y=s["shortfall_value"] / 1e6, name="Không bán được",
                         marker=dict(color=t["series2"], cornerradius=4),
                         customdata=[money(v) for v in s["shortfall_value"]],
                         hovertemplate="Không bán được %{customdata}<extra></extra>"))
    fig.update_layout(barmode="group", bargap=0.6 if len(x) <= 4 else 0.35, bargroupgap=0.08, showlegend=True,
                      margin=dict(l=8, r=8, t=28, b=8))
    fig.update_xaxes(type="category", showgrid=False, nticks=10)
    fig.update_yaxes(tickformat=",.0f", ticksuffix=" tr", rangemode="tozero")
    return fig


def session_table(r: stress.StressResult, labels: list[str]) -> html.Div:
    status_text = {k: v[1] for k, v in ui.STATUS.items()}
    rows = []
    for row in r.sessions.itertuples():
        rows.append([labels[row.session], money(row.market_value), money(row.net_debt), money(row.equity),
                     "–" if math.isinf(row.ratio) else pct(row.ratio),
                     ui.pill(row.status if row.status != "safe" else "safe", status_text[row.status]),
                     money(row.sold_value) if row.sold_value else "–",
                     money(row.shortfall_value) if row.shortfall_value else "–"])
    return ui.table(["Phiên", "Giá trị danh mục", "Dư nợ ròng", "Tài sản ròng", "Tỷ lệ ký quỹ", "Trạng thái",
                     "Bán được", "Không bán được"], rows)


def liquidity_table() -> html.Div:
    liq = data.liquidity()
    t = liq.by_position.pivot(index="board", columns="position", values="median")
    n = liq.by_position.groupby("board")["sessions"].sum()
    rows = [[BOARD_NAMES.get(b, b), *[number(t.loc[b].get(k), 2) for k in range(1, MAX_CHAIN_POSITION + 1)],
             number(n.get(b))] for b in ("HOSE", "HNX", "UPCOM") if b in t.index]
    return html.Div([
        html.P(f"Tỷ lệ khối lượng khớp trong phiên giảm sàn so với bình quân 20 phiên trước (trung vị từ năm "
               f"{liq.since.year}). Áp dụng thêm hệ số riêng cho mã có ít nhất 5 phiên giảm sàn.",
               className="text-2", style={"fontSize": "13px", "marginBottom": "8px"}),
        ui.table(["Sàn", "Phiên đầu", "Phiên 2", "Phiên 3", "Từ phiên 4", "Số phiên"], rows)])


# ---------------------------------------------------------------- callbacks
def register(app) -> None:
    @app.callback(Output("st-custom", "style"), Output("st-drops-wrap", "style"), Input("st-scenario", "value"))
    def toggle_custom(kind):
        return ({"display": "grid" if kind == "custom" else "none", "marginTop": "12px"},
                {"display": "block" if kind == "custom" else "none"})

    @app.callback(Output("st-overrides", "data"), Input("st-drops", "cellValueChanged"), Input("st-drop", "value"),
                  State("st-overrides", "data"), prevent_initial_call=True)
    def overrides(changed, _drop, current):
        # AG Grid không cập nhật rowData khi sửa ô, nên giữ các mức đã sửa riêng. Đổi mức chung thì bỏ hết.
        if ctx.triggered_id == "st-drop":
            return {}
        out = dict(current or {})
        for e in changed if isinstance(changed, list) else [changed] if changed else []:
            row = e.get("data") or {}
            if row.get("symbol"):
                out[row["symbol"]] = row.get("drop")
        return out

    @app.callback(Output("st-drops", "rowData"), Input("portfolio", "data"), Input("st-drop", "value"),
                  State("st-overrides", "data"))
    def drops_rows(current, drop, edited):
        p = from_dict(current)
        q = data.quotes(p.symbols)
        keep = {} if ctx.triggered_id == "st-drop" else (edited or {})
        base = round(_clamp_drop(drop) * 100, 2)
        return [{"symbol": s, "sector": risk.supersector(q.loc[s, "icb_code"]) if s in q.index else "Khác",
                 "drop": keep.get(s, base)} for s in p.symbols]

    @app.callback(
        Output("st-lede", "children"), Output("st-tiles", "children"), Output("st-ratio", "figure"),
        Output("st-sales", "figure"), Output("st-sales-wrap", "style"), Output("st-table", "children"),
        Output("st-liquidity", "children"), Output("st-notes", "children"),
        Input("portfolio", "data"), Input("st-scenario", "value"), Input("st-drop", "value"),
        Input("st-sessions", "value"), Input("st-part", "value"), Input("st-grace", "value"),
        Input("st-tax", "value"), Input("st-fee", "value"), Input("theme", "data"), Input("st-overrides", "data"))
    def run(current, kind, drop, sessions, part, grace, tax, fee, theme, edited):
        p = from_dict(current)
        empty = base_figure(theme, 120)
        empty.update_xaxes(visible=False)
        empty.update_yaxes(visible=False)
        if not p.holdings:
            msg = "Thêm cổ phiếu ở trang Danh mục để chạy kịch bản."
            return msg, [], empty, empty, {"display": "none"}, None, liquidity_table(), None
        q = data.quotes(p.symbols)
        missing = [s for s in p.symbols if s not in q.index]
        if missing:
            p = p.with_holdings([h for h in p.holdings if h.symbol in q.index])
        # Ô trống hoặc ngoài khoảng cho phép (Dash gửi None) thì dùng giá trị mặc định của ô đó.
        d = stress.Assumptions()

        def pick(v, default, lo, hi):
            return default if v is None else min(max(float(v), lo), hi)

        a = stress.Assumptions(participation=pick(part, d.participation * 100, 1, 100) / 100,
                               call_grace=int(pick(grace, d.call_grace, 1, 10)),
                               tax_rate=pick(tax, d.tax_rate * 100, 0, 5) / 100,
                               fee_rate=pick(fee, d.fee_rate * 100, 0, 5) / 100)
        rows = [{"symbol": s, "drop": v} for s, v in (edited or {}).items()]
        scenario, labels = build_scenario(kind, drop, sessions, p, rows)
        r = stress.simulate(p, q, data.liquidity(), scenario, a)
        notes = [html.Span(n) for n in r.notes]
        if missing:
            notes.append(html.Span(f"Kịch bản bỏ qua mã thiếu giá: {', '.join(missing)}."))
        notes += [html.Span("Kịch bản dùng tiền bán để giảm dư nợ ngay, không chờ T+2. Giả định không nộp thêm tiền và không tính lãi vay phát sinh."),
                  html.Span("Khi bán giải chấp, mô phỏng bán mã thanh khoản cao trước để đưa tỷ lệ ký quỹ về ngưỡng gọi ký quỹ.")]
        show_sales = {"display": "block"} if r.first_force else {"display": "none"}
        return (lede(p, r, labels, kind), tiles(r, labels, kind), ratio_figure(p, r, labels, theme),
                sales_figure(r, labels, theme) if r.first_force else empty, show_sales, session_table(r, labels),
                liquidity_table(), notes)
