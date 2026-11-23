"""Trang Danh mục và tài khoản: nhập cổ phiếu, tiền mặt, dư nợ, ngưỡng ký quỹ. Các hàm tóm tắt dùng chung với Tổng quan."""

from __future__ import annotations

import math
from dataclasses import replace

import dash_ag_grid as dag
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, html, no_update

from .. import stress
from ..portfolio import Holding, PortfolioError, from_dict, to_dict, validate
from . import data, ui
from .theme import base_figure, money, pct, tokens

PATH = "/danh-muc"


def _grid() -> dag.AgGrid:
    money_fmt = {"function": "vnMoney(params)"}
    columns = [
        {"field": "symbol", "headerName": "Mã", "minWidth": 70, "flex": 1, "cellStyle": {"fontWeight": 600}},
        {"field": "board", "headerName": "Sàn", "minWidth": 70, "flex": 1},
        {"field": "sector", "headerName": "Nhóm ngành", "flex": 2, "minWidth": 120, "tooltipField": "sector"},
        {"field": "quantity", "headerName": "Khối lượng", "editable": True, "type": "rightAligned", "minWidth": 96, "flex": 1,
         "cellClass": "cell-edit", "valueFormatter": {"function": "vnNumber(params)"},
         "cellEditor": "agNumberCellEditor", "cellEditorParams": {"min": 1, "precision": 0}},
        {"field": "cost_price", "headerName": "Giá vốn", "editable": True, "type": "rightAligned", "minWidth": 88, "flex": 1,
         "cellClass": "cell-edit", "valueFormatter": {"function": "vnNumber(params)"},
         "cellEditor": "agNumberCellEditor", "cellEditorParams": {"min": 0, "precision": 0}},
        {"field": "price", "headerName": "Giá", "type": "rightAligned", "minWidth": 80, "flex": 1,
         "valueFormatter": {"function": "vnNumber(params)"}},
        {"field": "value", "headerName": "Giá trị", "type": "rightAligned", "minWidth": 84, "flex": 1, "valueFormatter": money_fmt},
        {"field": "weight", "headerName": "Tỷ trọng", "type": "rightAligned", "minWidth": 80, "flex": 1,
         "valueFormatter": {"function": "vnPercent(params)"}},
        {"field": "pnl", "headerName": "Lãi/lỗ", "type": "rightAligned", "minWidth": 76, "flex": 1,
         "valueFormatter": {"function": "vnPercent(params)"}},
        {"field": "floor_sessions", "headerName": "Phiên bán hết", "type": "rightAligned", "minWidth": 104, "flex": 1,
         "headerTooltip": "Số phiên giảm sàn để bán hết nếu lệnh bán chiếm 10% khối lượng khớp dự kiến mỗi phiên",
         "valueFormatter": {"function": "vnSessions(params)"}},
    ]
    return dag.AgGrid(
        id="pf-grid", columnDefs=columns, rowData=[], getRowId="params.data.symbol",
        defaultColDef={"sortable": True, "resizable": True, "suppressMovable": True},
        dashGridOptions={"rowSelection": {"mode": "multiRow", "checkboxes": True, "headerCheckbox": True,
                                          "enableClickSelection": False},
                         "singleClickEdit": True, "stopEditingWhenCellsLoseFocus": True, "animateRows": False, "suppressColumnVirtualisation": True,
                         "domLayout": "autoHeight",
                         "tooltipShowDelay": 300, "selectionColumnDef": {"width": 44, "minWidth": 44, "maxWidth": 44},
                         "localeText": {"noRowsToShow": "Chưa có mã trong danh mục."}},
        className="ag-theme-quartz", style={"width": "100%"})


def layout(p: dict) -> html.Div:
    return html.Div([
        html.Div([html.H1("Danh mục và tài khoản"), html.Div(id="pf-source")], className="page-head"),
        html.Div([
            ui.panel("Cổ phiếu đang nắm giữ",
                     html.Div([
                         html.Div(ui.symbol_dropdown("pf-add-symbol", data.symbols()),
                                  style={"flex": "1 1 220px", "minWidth": "180px"}),
                         html.Div(ui.number_input("pf-add-qty", None, min_=1, placeholder="Khối lượng"),
                                  style={"flex": "0 1 140px"}),
                         html.Div(ui.number_input("pf-add-cost", None, min_=0, placeholder="Giá vốn (tùy chọn)"),
                                  style={"flex": "0 1 170px"}),
                         html.Button("Thêm", id="pf-add", className="btn primary", n_clicks=0),
                     ], className="row", style={"marginBottom": "12px"}),
                     _grid(),
                     html.Div([html.Div([html.Button("Xóa mã đã chọn", id="pf-delete", className="btn ghost",
                                                     n_clicks=0),
                                         html.Span(id="pf-hold-msg", className="message", role="status")],
                                        className="row", style={"alignItems": "center"})],
                              className="row", style={"justifyContent": "space-between", "marginTop": "10px",
                                                      "alignItems": "center"})),
            html.Div([
                ui.panel("Tài khoản",
                         html.Div([
                             ui.field("Tiền mặt (triệu đồng)", ui.number_input("pf-cash", p["cash"] / 1e6, min_=0)),
                             ui.field("Dư nợ ký quỹ (triệu đồng)", ui.number_input("pf-debt", p["debt"] / 1e6, min_=0),
                                      "Gồm nợ gốc và lãi"),
                             ui.field("Ngưỡng gọi ký quỹ (%)",
                                      ui.number_input("pf-call", round(p["call_ratio"] * 100, 2), min_=1, max_=99)),
                             ui.field("Ngưỡng bán giải chấp (%)",
                                      ui.number_input("pf-force", round(p["force_ratio"] * 100, 2), min_=1, max_=99)),
                         ], className="fields"),
                         html.Div([html.Button("Lưu trên máy", id="pf-save", className="btn primary", n_clicks=0),
                                   html.Button("Dùng danh mục mẫu", id="pf-reset", className="btn ghost", n_clicks=0)],
                                  className="row", style={"marginTop": "14px"}),
                         html.Div(id="pf-message", className="message", role="status", style={"marginTop": "8px"})),
            ]),
        ], className="grid-wide"),
        ui.method_note([
            "Ứng dụng dùng giá đóng cửa phiên gần nhất trong kho, đã điều chỉnh theo sự kiện quyền. Nếu hôm nay mới hơn phiên gần "
            "nhất trong kho và có dữ liệu khớp lệnh ghi qua WebSocket (lệnh record), trang này dùng giá khớp gần nhất "
            "với nhãn \"Giá trong phiên\". "
            "Các trang phân tích khác dùng giá đóng cửa. Tỷ lệ ký quỹ = (giá trị danh mục − dư nợ ròng) / giá trị danh mục. "
            "Dư nợ ròng = dư nợ − tiền mặt. Tài sản ròng = giá trị danh mục + tiền mặt − dư nợ.",
            "Mức giảm đến ngưỡng gọi ký quỹ = giá trị danh mục − dư nợ ròng / (1 − ngưỡng gọi ký quỹ). "
            "Công thức giả định mọi mã giảm cùng tỷ lệ.",
            "Số phiên bán hết = khối lượng nắm giữ / (10% × khối lượng bình quân 20 phiên × tỷ lệ khớp lịch sử của phiên giảm "
            "sàn đầu tiên trên cùng sàn).",
            "Ngưỡng mặc định theo SSI. Tỷ lệ có thể khác theo mã và hợp đồng. Ứng dụng không kiểm tra mã có được ký quỹ hay không.",
        ]),
    ], className="page")


# ---------------------------------------------------------------- tính toán hiển thị
def summary(p) -> dict:
    """Giá trị, tài sản ròng, tỷ lệ ký quỹ và bảng từng mã của danh mục (đã validate)."""
    q = data.quotes(p.symbols)
    live = data.live_prices([s for s in p.symbols if s in q.index])
    rows, lmv = [], 0.0
    for h in p.holdings:
        price = float(q.loc[h.symbol, "price"]) if h.symbol in q.index else math.nan
        if h.symbol in live:
            price = live[h.symbol][0]
        value = h.quantity * price
        lmv += 0 if math.isnan(value) else value
        rows.append({"symbol": h.symbol, "board": q.loc[h.symbol, "board"] if h.symbol in q.index else None,
                     "sector": q.loc[h.symbol, "icb_name"] if h.symbol in q.index else None,
                     "quantity": h.quantity, "cost_price": h.cost_price, "price": price, "value": value,
                     "pnl": (price / h.cost_price - 1) if h.cost_price and not math.isnan(price) else None})
    for r in rows:
        r["weight"] = r["value"] / lmv if lmv > 0 and not math.isnan(r["value"]) else None
    held = p.with_holdings([h for h in p.holdings if h.symbol in q.index])
    days = stress.sessions_to_sell(held, q, data.liquidity()) if held.holdings else {}
    for r in rows:
        d = days.get(r["symbol"])
        r["floor_sessions"] = None if d is None else ("inf" if math.isinf(d) else d)
        for k in ("price", "value"):
            if isinstance(r[k], float) and math.isnan(r[k]):
                r[k] = None
    net = p.debt - p.cash
    ratio = math.inf if net <= 0 else ((lmv - net) / lmv if lmv > 0 else -math.inf)
    missing = [h.symbol for h in p.holdings if h.symbol not in q.index]
    return {"rows": rows, "lmv": lmv, "equity": lmv - net, "net_debt": max(net, 0.0), "ratio": ratio,
            "missing": missing, "quote_date": q["date"].max() if len(q) else None,
            "live_at": max((t for _, t in live.values()), default=None), "live_count": len(live)}


def tiles(p, s) -> list:
    status = ui.ratio_status(s["ratio"], p.call_ratio, p.force_ratio)
    ratio_text = "Dư nợ ròng ≤ 0" if status == "no_debt" else pct(s["ratio"])
    gap = (s["lmv"] - s["net_debt"] / (1 - p.call_ratio)) if status not in ("no_debt",) and s["lmv"] > 0 else None
    gap_text = None
    if gap is not None:
        gap_text = (f"Cách ngưỡng gọi ký quỹ {money(gap)} ({pct(gap / s['lmv'])})"
                    if gap > 0 else "Đang dưới ngưỡng gọi ký quỹ")
    return [
        ui.tile("Giá trị danh mục", money(s["lmv"]), f"{len(p.holdings)} mã"),
        ui.tile("Tài sản ròng", money(s["equity"])),
        ui.tile("Dư nợ ròng", money(s["net_debt"])),
        ui.tile("Tỷ lệ ký quỹ", ratio_text, gap_text,
                None if status == "no_debt" else ui.meter(s["ratio"], p.call_ratio, p.force_ratio),
                badge=None if status == "no_debt" else ui.pill(status)),
    ]


def weights_figure(s, theme):
    t = tokens(theme)
    rows = sorted([r for r in s["rows"] if r["weight"]], key=lambda r: r["weight"])
    fig = base_figure(theme, height=max(120, 34 * len(rows) + 24))
    if not rows:
        fig.add_annotation(text="Chưa có dữ liệu", showarrow=False, font=dict(color=t["muted"]))
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        return fig
    fig.add_trace(go.Bar(
        x=[r["weight"] for r in rows], y=[r["symbol"] for r in rows], orientation="h",
        marker=dict(color=t["series1"], cornerradius=4), width=0.55,
        text=[pct(r["weight"]) for r in rows], textposition="outside", cliponaxis=False,
        textfont=dict(color=t["text2"], size=12),
        customdata=[[money(r["value"])] for r in rows],
        hovertemplate="<b>%{y}</b><br>%{text} · %{customdata[0]}<extra></extra>"))
    fig.update_layout(hovermode="closest", margin=dict(l=8, r=44, t=4, b=4), bargap=0.35)
    fig.update_xaxes(visible=False, range=[0, max(r["weight"] for r in rows) * 1.18])
    fig.update_yaxes(showgrid=False, tickfont=dict(color=t["text"], size=12))
    return fig


# ---------------------------------------------------------------- callbacks
def register(app) -> None:
    outputs = ["store", "hold_msg", "hold_cls", "acct_msg", "acct_cls", "symbol", "qty", "cost",
               "cash", "debt", "call", "force"]

    @app.callback(
        Output("portfolio", "data"), Output("pf-hold-msg", "children"), Output("pf-hold-msg", "className"),
        Output("pf-message", "children"), Output("pf-message", "className"),
        Output("pf-add-symbol", "value"), Output("pf-add-qty", "value"), Output("pf-add-cost", "value"),
        Output("pf-cash", "value"), Output("pf-debt", "value"), Output("pf-call", "value"), Output("pf-force", "value"),
        Input("pf-add", "n_clicks"), Input("pf-delete", "n_clicks"), Input("pf-reset", "n_clicks"),
        Input("pf-save", "n_clicks"), Input("pf-grid", "cellValueChanged"),
        Input("pf-cash", "value"), Input("pf-debt", "value"), Input("pf-call", "value"), Input("pf-force", "value"),
        State("pf-add-symbol", "value"), State("pf-add-qty", "value"), State("pf-add-cost", "value"),
        State("pf-grid", "selectedRows"), State("portfolio", "data"),
        prevent_initial_call=True)
    def mutate(add, delete, reset, save, changed, cash, debt, call, force, symbol, qty, cost, selected, current):
        trigger = ctx.triggered_id
        clicks = {"pf-add": add, "pf-delete": delete, "pf-reset": reset, "pf-save": save}
        out = dict.fromkeys(outputs, no_update)
        # Dash gọi lại callback khi trang được chèn vào; chỉ xử lý nút khi thật sự được bấm.
        if (trigger in clicks and not clicks[trigger]) or (trigger == "pf-grid" and not changed):
            return tuple(out.values())
        holdings_area = trigger in ("pf-add", "pf-delete", "pf-grid")

        def say(text, ok=True):
            key = "hold" if holdings_area else "acct"
            out[f"{key}_msg"], out[f"{key}_cls"] = text, "message ok" if ok else "message error"

        try:
            p = from_dict(current)
            if trigger == "pf-reset":
                s = data.sample_portfolio()
                out.update(store=to_dict(s), symbol=None, qty=None, cost=None, cash=s.cash / 1e6,
                           debt=s.debt / 1e6, call=round(s.call_ratio * 100, 2), force=round(s.force_ratio * 100, 2))
                say("Đã tải danh mục mẫu.")
            elif trigger == "pf-save":
                path = data.save_portfolio(p)
                say(f"Đã lưu danh mục vào {path.parent.name}/{path.name}.")
            elif trigger == "pf-add":
                if not symbol:
                    raise PortfolioError("Chọn mã cần thêm.")
                if not qty:
                    raise PortfolioError("Khối lượng phải là số nguyên dương.")
                if symbol in p.symbols:
                    raise PortfolioError(f"Danh mục đã có {symbol}. Sửa khối lượng trong bảng.")
                p = validate(p.with_holdings([*p.holdings, Holding(symbol, qty, cost or None)]))
                out.update(store=to_dict(p), symbol=None, qty=None, cost=None)
                say(f"Đã thêm {symbol}.")
            elif trigger == "pf-delete":
                gone = {r["symbol"] for r in selected or []}
                if not gone:
                    raise PortfolioError("Chọn ít nhất một mã để xóa.")
                out["store"] = to_dict(p.with_holdings([h for h in p.holdings if h.symbol not in gone]))
                say(f"Đã xóa {', '.join(sorted(gone))}.")
            elif trigger == "pf-grid":
                holdings = {h.symbol: h for h in p.holdings}
                for e in changed if isinstance(changed, list) else [changed]:
                    row = e.get("data") or {}
                    if row.get("symbol") in holdings:
                        holdings[row["symbol"]] = Holding(row["symbol"], row.get("quantity"),
                                                          row.get("cost_price") or None)
                out["store"] = to_dict(validate(p.with_holdings(holdings.values())))
                say("")
            else:  # ô tài khoản
                if None in (cash, debt, call, force):
                    raise PortfolioError("Nhập tiền mặt và dư nợ. Nhập cả hai ngưỡng từ 1 đến 99%.")
                out["store"] = to_dict(validate(replace(
                    p, cash=float(cash) * 1e6, debt=float(debt) * 1e6, call_ratio=float(call) / 100,
                    force_ratio=float(force) / 100)))
                say("")
        except PortfolioError as exc:
            say(str(exc), ok=False)
            if trigger == "pf-grid":  # ghi lại dữ liệu cũ để bảng hiện đúng giá trị đang dùng
                out["store"] = current
        return tuple(out.values())

    @app.callback(Output("pf-grid", "rowData"), Output("pf-source", "children"), Input("portfolio", "data"))
    def render(current):
        p = from_dict(current)
        s = summary(p)
        return s["rows"], source_chips(s)


def source_chips(s) -> html.Div:
    chips = []
    if s["live_count"]:
        chips.append(html.Span(f"Giá trong phiên đến {s['live_at']:%H:%M} ({s['live_count']} mã)", className="chip",
                               title="Các trang phân tích khác dùng giá đóng cửa phiên gần nhất trong kho."))
    if s["missing"]:
        chips.append(html.Span(f"Không có giá: {', '.join(s['missing'])}", className="chip"))
    return html.Div(chips, className="row")
