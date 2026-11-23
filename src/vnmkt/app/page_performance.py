"""Trang Hiệu suất: nhật ký giao dịch, TWR, IRR, so với VN-Index và với "không giao dịch gì", chi phí, PGR/PLR."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import dash_ag_grid as dag
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, dcc, html, no_update

from .. import performance as pf
from ..portfolio import Holding, PortfolioError, from_dict, to_dict
from . import data, ui
from .theme import LINE_CONFIG, base_figure, money, number, pannable, pct, tokens

PATH = "/hieu-suat"


def _grid() -> dag.AgGrid:
    num = {"function": "vnNumber(params)"}
    cols = [
        {"field": "date", "headerName": "Ngày", "minWidth": 110, "flex": 1, "sort": "asc",
         "valueFormatter": {"function": "vnDate(params)"}},
        {"field": "kind_label", "headerName": "Loại", "minWidth": 90, "flex": 1},
        {"field": "symbol", "headerName": "Mã", "minWidth": 70, "flex": 1, "cellStyle": {"fontWeight": 600}},
        {"field": "quantity", "headerName": "Khối lượng", "type": "rightAligned", "minWidth": 100, "flex": 1,
         "valueFormatter": num},
        {"field": "price", "headerName": "Giá", "type": "rightAligned", "minWidth": 90, "flex": 1, "valueFormatter": num},
        {"field": "amount", "headerName": "Số tiền", "type": "rightAligned", "minWidth": 110, "flex": 1,
         "valueFormatter": {"function": "vnMoney(params)"}},
        {"field": "costs", "headerName": "Phí và thuế", "type": "rightAligned", "minWidth": 100, "flex": 1,
         "valueFormatter": num},
    ]
    return dag.AgGrid(id="pfm-grid", columnDefs=cols, rowData=[], getRowId="params.data.id",
                      defaultColDef={"sortable": True, "resizable": True, "suppressMovable": True},
                      dashGridOptions={"rowSelection": {"mode": "multiRow", "checkboxes": True, "headerCheckbox": True,
                                                        "enableClickSelection": False},
                                       "selectionColumnDef": {"width": 44, "minWidth": 44, "maxWidth": 44},
                                       "domLayout": "autoHeight", "animateRows": False, "suppressColumnVirtualisation": True,
                                       "pagination": True, "paginationPageSize": 10,
                                       "paginationPageSizeSelector": False,
                                       "localeText": {"noRowsToShow": "Chưa có giao dịch trong nhật ký."}},
                      className="ag-theme-quartz", style={"width": "100%"})


def layout() -> html.Div:
    return html.Div([
        html.Div([html.Div([html.H1("Hiệu suất"),
                            html.P("Dùng nhật ký nạp, rút tiền và giao dịch để đo lợi suất tài khoản.")]),
                  html.Div(id="pfm-source")], className="page-head"),
        html.Div(id="pfm-lede", className="panel lede"),
        html.Div(id="pfm-body", className="stack", children=[
            html.Div(id="pfm-tiles", className="tiles"),
            ui.panel("Giá trị khoản đầu tư ban đầu 100 đồng", dcc.Graph(id="pfm-chart", config=LINE_CONFIG,
                                                                      style={"height": "300px"}),
                     hint="Danh mục theo TWR. Kịch bản giữ nguyên bắt đầu sau ngày giao dịch đầu tiên."),
            html.Div([
                ui.panel("Lãi/lỗ đã thực hiện", html.Div(id="pfm-realized")),
                ui.panel("Xu hướng chốt lãi và cắt lỗ", html.Div(id="pfm-disposition"),
                         hint="PGR và PLR theo Odean (1998)"),
            ], className="grid-wide"),
        ]),
        ui.panel("Nhật ký giao dịch",
                 html.Div([
                     html.Div(ui.field("Ngày", dcc.DatePickerSingle(id="pfm-date", display_format="DD/MM/YYYY",
                                                                    first_day_of_week=1, placeholder="Chọn ngày",
                                                                    className="date")), style={"flex": "0 1 150px"}),
                     html.Div(ui.field("Loại giao dịch", ui.segmented("pfm-kind", [(k, v) for k, v in pf.KINDS.items()], pf.BUY)),
                              style={"flex": "0 1 auto"}),
                     html.Label([html.Span("Mã"), ui.symbol_dropdown("pfm-symbol", data.symbols())], className="field",
                                style={"flex": "1 1 200px", "minWidth": "160px"}),
                     html.Div(ui.field("Khối lượng", ui.number_input("pfm-qty", None, min_=1)), style={"flex": "0 1 120px"}),
                     html.Div(ui.field("Giá hoặc số tiền (đồng)", ui.number_input("pfm-price", None, min_=0)),
                              style={"flex": "0 1 150px"}),
                     html.Div(ui.field("Phí (đồng)", ui.number_input("pfm-fee", None, min_=0)), style={"flex": "0 1 120px"}),
                     html.Button("Thêm", id="pfm-add", className="btn primary", n_clicks=0),
                 ], className="row", style={"alignItems": "flex-end", "gap": "12px", "marginBottom": "12px"}),
                 html.P("Ứng dụng tự tính thuế bán 0,1%. Không cần ghi cổ tức vì lợi suất từ giá đã điều chỉnh bao gồm "
                        "cổ tức tiền mặt và cổ phiếu thưởng.", className="muted",
                        style={"fontSize": "12px", "marginBottom": "10px"}),
                 _grid(),
                 html.Div([html.Div([html.Button("Xóa dòng đã chọn", id="pfm-delete", className="btn ghost", n_clicks=0),
                                     html.Button("Lưu trên máy", id="pfm-save", className="btn", n_clicks=0),
                                     html.Button("Dùng nhật ký mẫu", id="pfm-reset", className="btn ghost", n_clicks=0),
                                     html.Button("Cập nhật danh mục từ nhật ký", id="pfm-sync", className="btn ghost", n_clicks=0)],
                                    className="row"),
                           html.Span(id="pfm-msg", className="message", role="status")],
                          className="row", style={"justifyContent": "space-between", "alignItems": "center",
                                                  "marginTop": "10px"})),
    ], className="page")


# ---------------------------------------------------------------- hiển thị
def row_ids(j: pf.Journal) -> list[str]:
    """Mã dòng cố định theo nội dung giao dịch (thêm số lần lặp nếu trùng), để xóa đúng dòng kể cả khi thứ tự đổi."""
    seen: dict[str, int] = {}
    out = []
    for t in j.transactions:
        base = f"{t.date.isoformat()}|{t.kind}|{t.symbol or ''}|{t.quantity}|{t.price:g}|{t.amount:g}|{t.fee:g}"
        seen[base] = seen.get(base, 0) + 1
        out.append(f"{base}#{seen[base]}")
    return out


def grid_rows(j: pf.Journal) -> list[dict]:
    rows = []
    for rid, t in zip(row_ids(j), j.transactions):
        trade = t.kind in (pf.BUY, pf.SELL)
        rows.append({"id": rid, "date": t.date.isoformat(), "kind_label": pf.KINDS[t.kind],
                     "symbol": t.symbol or "", "quantity": t.quantity if trade else None,
                     "price": t.price if trade else None,
                     "amount": t.value if trade else (t.amount if t.kind == pf.DEPOSIT else -t.amount),
                     "costs": (t.fee + t.tax) if trade else None})
    return rows


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
        html.P("Vào ngày có lệnh bán, lãi từ mã bán là lãi đã thực hiện, lãi từ mã còn giữ là lãi chưa thực hiện. "
               "Phân loại lỗ tương tự. PGR = lãi đã thực hiện / (lãi đã thực hiện + lãi chưa thực hiện). "
               "Kết quả kém tin cậy khi có ít lệnh bán.", className="muted", style={"fontSize": "12px", "marginTop": "6px"}),
    ])


# ---------------------------------------------------------------- callbacks
def register(app) -> None:
    @app.callback(
        Output("journal", "data"), Output("pfm-msg", "children"), Output("pfm-msg", "className"),
        Output("pfm-qty", "value"), Output("pfm-price", "value"), Output("pfm-fee", "value"),
        Output("portfolio", "data", allow_duplicate=True),
        Input("pfm-add", "n_clicks"), Input("pfm-delete", "n_clicks"), Input("pfm-save", "n_clicks"),
        Input("pfm-reset", "n_clicks"), Input("pfm-sync", "n_clicks"),
        State("pfm-date", "date"), State("pfm-kind", "value"), State("pfm-symbol", "value"), State("pfm-qty", "value"),
        State("pfm-price", "value"), State("pfm-fee", "value"), State("pfm-grid", "selectedRows"),
        State("journal", "data"), State("portfolio", "data"), prevent_initial_call=True)
    def mutate(add, delete, save, reset, sync, day, kind, symbol, qty, price, fee, selected, current, port):
        trigger = ctx.triggered_id
        clicks = {"pfm-add": add, "pfm-delete": delete, "pfm-save": save, "pfm-reset": reset, "pfm-sync": sync}
        keep = (no_update,) * 4
        if not clicks.get(trigger):
            return no_update, no_update, no_update, *keep
        try:
            j = pf.from_dict(current)
            if trigger == "pfm-reset":
                return pf.to_dict(data.sample_journal()), "Đã tải nhật ký mẫu.", "message ok", *keep
            if trigger == "pfm-save":
                path = data.save_journal(j)
                return no_update, f"Đã lưu nhật ký vào {path.parent.name}/{path.name}.", "message ok", *keep
            if trigger == "pfm-delete":
                ids = {r["id"] for r in selected or []}
                if not ids:
                    raise PortfolioError("Chọn ít nhất một dòng để xóa.")
                left = tuple(t for rid, t in zip(row_ids(j), j.transactions) if rid not in ids)
                if len(left) == len(j.transactions):
                    raise PortfolioError("Dòng đã chọn không còn trong nhật ký. Chọn lại dòng cần xóa.")
                removed = len(j.transactions) - len(left)
                return pf.to_dict(replace(j, transactions=left)), f"Đã xóa {removed} dòng.", "message ok", *keep
            if trigger == "pfm-sync":
                r, m = evaluate(j)
                held = pf.holdings(j, m.actions)
                p = from_dict(port)
                p = replace(p, holdings=tuple(Holding(s, q) for s, q in sorted(held.items())),
                            cash=max(r.final_cash, 0.0), debt=max(-r.final_cash, 0.0), simulated=j.simulated)
                return (no_update, f"Đã cập nhật {len(held)} mã vào danh mục. Tiền mặt và dư nợ chưa tính cổ tức tiền mặt đã nhận.", "message ok", no_update, no_update, no_update, to_dict(p))
            # thêm dòng
            if not day:
                raise PortfolioError("Chọn ngày giao dịch.")
            row = {"date": day, "kind": kind}
            if kind in (pf.DEPOSIT, pf.WITHDRAW):
                row["amount"] = price
                if not price:
                    raise PortfolioError("Nhập số tiền.")
            else:
                if not symbol or not qty or not price:
                    raise PortfolioError("Chọn mã rồi nhập khối lượng và giá khớp.")
                row.update(symbol=symbol, quantity=qty, price=price, fee=fee or 0)
            new = pf.from_dict({**current, "transactions": [*pf.to_dict(j)["transactions"], row]})
            if date.fromisoformat(day[:10]) > date.today():
                raise PortfolioError("Ngày giao dịch không được sau hôm nay.")
            return pf.to_dict(new), "Đã thêm vào nhật ký.", "message ok", None, None, None, no_update
        except PortfolioError as exc:
            return no_update, str(exc), "message error", *keep

    @app.callback(Output("pfm-grid", "rowData"), Output("pfm-lede", "children"), Output("pfm-body", "style"),
                  Output("pfm-tiles", "children"), Output("pfm-chart", "figure"), Output("pfm-realized", "children"),
                  Output("pfm-disposition", "children"), Output("pfm-source", "children"),
                  Input("journal", "data"), Input("theme", "data"))
    def render(current, theme):
        j = pf.from_dict(current)
        source = html.Span("Dữ liệu mô phỏng" if j.simulated else "Nhập thủ công", className="chip")
        rows = grid_rows(j)
        hide = {"display": "none"}
        if not j.transactions:
            return rows, "Ghi lần nạp tiền đầu tiên và các lệnh đã khớp để tính hiệu suất.", hide, [], {}, \
                None, None, source
        try:
            r, _ = evaluate(j)
        except PortfolioError as exc:
            return rows, f"{exc}", hide, [], {}, None, None, source
        return (rows, lede(r, j), {"display": "flex"}, tiles(r), chart(r, theme), realized_table(r), disposition(r),
                source)
