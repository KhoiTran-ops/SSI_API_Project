"""Trang Nhật ký giao dịch: ghi nạp, rút tiền và lệnh đã khớp; cập nhật danh mục từ nhật ký."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import dash_ag_grid as dag
from dash import Input, Output, State, ctx, dcc, html, no_update

from .. import performance as pf
from ..portfolio import Holding, PortfolioError, from_dict, to_dict
from . import data, ui
from .page_performance import evaluate

PATH = "/nhat-ky"


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
        html.Div([html.H1("Nhật ký giao dịch"), html.Div(id="jr-source")], className="page-head"),
        ui.panel(None,
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
                 _grid(),
                 html.Div([html.Div([html.Button("Xóa dòng đã chọn", id="pfm-delete", className="btn ghost", n_clicks=0),
                                     html.Button("Lưu trên máy", id="pfm-save", className="btn", n_clicks=0),
                                     html.Button("Dùng nhật ký mẫu", id="pfm-reset", className="btn ghost", n_clicks=0),
                                     html.Button("Cập nhật danh mục từ nhật ký", id="pfm-sync", className="btn ghost", n_clicks=0)],
                                    className="row"),
                           html.Span(id="pfm-msg", className="message", role="status")],
                          className="row", style={"justifyContent": "space-between", "alignItems": "center",
                                                  "marginTop": "10px"})),
        ui.method_note([
            "Ứng dụng tự tính thuế bán 0,1%.",
            "Không cần ghi cổ tức: lợi suất tính từ giá đã điều chỉnh, đã gồm cổ tức tiền mặt và cổ phiếu thưởng.",
            "Cập nhật danh mục từ nhật ký thay toàn bộ mã, tiền mặt và dư nợ của danh mục bằng số tính từ nhật ký.",
        ]),
    ], className="page")


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

    @app.callback(Output("pfm-grid", "rowData"), Output("jr-source", "children"), Input("journal", "data"))
    def render(current):
        j = pf.from_dict(current)
        return grid_rows(j), html.Span("Dữ liệu mô phỏng" if j.simulated else "Nhập thủ công", className="chip")
