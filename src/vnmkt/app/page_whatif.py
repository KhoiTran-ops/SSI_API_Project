"""Trang Thử lệnh: một lệnh mua hoặc bán làm danh mục thay đổi thế nào. Không gửi lệnh."""

from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, dcc, html, no_update

from .. import exchange, risk, rules
from ..portfolio import PortfolioError, from_dict, to_dict
from ..whatif import BUY, FLOOR_HORIZON, SELL, Order, evaluate
from . import data, ui
from .theme import GRAPH_CONFIG, base_figure, money, number, pct, tokens

PATH = "/thu-lenh"


def price_hint(symbol) -> tuple[float | None, str]:
    """Giá gợi ý (đóng cửa gần nhất) và dòng biên độ phiên kế tiếp của một mã."""
    if not symbol:
        return None, ""
    q = data.quotes([symbol])
    if symbol not in q.index or pd.isna(q.loc[symbol, "price"]):
        return None, f"Thiếu dữ liệu giá của {symbol}."
    row = q.loc[symbol]
    board = exchange.board_of(row["board"])
    last = float(row["price"])
    ref = float(row["ref"]) if pd.notna(row.get("ref")) else last
    return last, (f"{symbol} · {row['board']} · giá đóng cửa {number(last)} ngày {row['date']:%d/%m/%Y} · "
                  f"giá sàn–giá trần phiên kế tiếp {number(exchange.floor_price(board, ref))}–"
                  f"{number(exchange.ceiling_price(board, ref))}")


def _prefill(params: dict | None) -> dict:
    """Giá trị điền sẵn từ địa chỉ: lenh=mua|ban, ma, kl, gia (thiếu giá thì lấy giá đóng cửa gần nhất)."""
    params = params or {}
    out = {"side": SELL if params.get("lenh") == "ban" else BUY, "symbol": None, "qty": None, "price": None}
    symbol = str(params.get("ma", "")).strip().upper()
    if symbol and symbol in set(data.symbols()["symbol"]):
        out["symbol"] = symbol
        q = data.quotes([symbol])
        if symbol in q.index and pd.notna(q.loc[symbol, "price"]):
            out["price"] = float(q.loc[symbol, "price"])
    for key, field_ in (("kl", "qty"), ("gia", "price")):
        try:
            value = float(params[key])
        except (KeyError, ValueError):
            continue
        if value > 0:
            out[field_] = int(value) if field_ == "qty" and value == int(value) else value
    return out


def layout(params: dict | None = None) -> html.Div:
    pre = _prefill(params)
    return html.Div([
        html.Div([html.Div([html.H1("Thử lệnh"),
                            html.P("Ước tính tác động của một lệnh lên danh mục và tỷ lệ ký quỹ.")])], className="page-head"),
        ui.panel(None, html.Div([
            html.Div([html.Span("Loại lệnh"), ui.segmented("wi-side", [(BUY, "Mua"), (SELL, "Bán")], pre["side"])],
                     className="field"),
            html.Label([html.Span("Mã"), ui.symbol_dropdown("wi-symbol", data.symbols(), pre["symbol"])],
                       className="field", style={"flex": "1 1 240px", "minWidth": "200px"}),
            html.Div(ui.field("Khối lượng", ui.number_input("wi-qty", pre["qty"], min_=1, placeholder="Ví dụ 1.000")),
                     style={"flex": "0 1 150px"}),
            html.Div(ui.field("Giá (đồng)", ui.number_input("wi-price", pre["price"], min_=0)), style={"flex": "0 1 150px"}),
            html.Div(ui.field("Phí giao dịch (%)", ui.number_input("wi-fee", 0, min_=0, max_=5)),
                     style={"flex": "0 1 140px"}),
        ], className="row", style={"alignItems": "flex-end", "gap": "14px"}),
            html.Div(price_hint(pre["symbol"])[1], id="wi-hint", className="muted", style={"fontSize": "12px", "marginTop": "8px", "minHeight": "18px"}),
            html.Div(id="wi-apply-msg", className="message", role="status")),
        html.Div(id="wi-lede", className="panel lede"),
        html.Div([html.Div([
            ui.panel("Trước và sau lệnh", html.Div(id="wi-table"), hint="Theo giá đóng cửa phiên gần nhất trong kho"),
            ui.panel("Tỷ trọng trước và sau lệnh", dcc.Graph(id="wi-weights", config=GRAPH_CONFIG)),
        ], className="grid-wide"),
        html.Div([
            html.Div(id="wi-warnings", className="notes"),
            html.Button("Áp dụng vào danh mục", id="wi-apply", className="btn", n_clicks=0, disabled=True),
        ], className="row", style={"justifyContent": "space-between", "alignItems": "center"}),
        ], id="wi-results", className="stack", style={"display": "none"}),
        ui.method_note([
            "Khi mua, tiền trả = giá trị lệnh × (1 + phí). Dùng tiền mặt trước, phần thiếu cộng vào dư nợ. "
            "Khi bán, tiền nhận = giá trị lệnh × (1 − phí − thuế 0,1%). Dùng tiền bán để trả dư nợ trước.",
            "Tiền bán và cổ phiếu mua về tài khoản vào T+2. Khi tính tỷ lệ ký quỹ, tiền bán được tính ngay vào tài sản.",
            "Mô phỏng tối đa 10 phiên giảm sàn liên tiếp để xác định thời điểm gọi ký quỹ. "
            "Tính độ biến động năm từ lợi suất ngày của 250 phiên gần nhất.",
            "Ứng dụng chỉ mô phỏng lệnh, không gửi lệnh và không kiểm tra sức mua thật, mã được ký quỹ hay hạn mức SSI.",
        ]),
    ], className="page")


# ---------------------------------------------------------------- hiển thị
def rule_notes(p, r, quotes, limits: rules.Rules) -> list:
    """Luật đang đạt mà sau lệnh bị vi phạm (luật vốn đã vi phạm thì không nhắc lại ở đây)."""
    before = {(c.rule, c.subject) for c in rules.failures(rules.check(p, quotes, data.liquidity(), limits))}
    new = [c for c in rules.failures(rules.check(r.portfolio_after, quotes, data.liquidity(), limits))
           if (c.rule, c.subject) not in before]
    out = []
    for c in new:
        if c.rule == "max_sessions_to_sell":
            value = "không bán được" if math.isinf(c.value) else f"{c.value:.0f} phiên"
            limit = f"{c.limit:.0f} phiên"
        else:
            value, limit = pct(c.value), pct(c.limit)
        out.append(html.Span(["Luật bị vi phạm: ", html.B(f"{rules.LABELS[c.rule]} {c.subject}".replace(" Danh mục", "")),
                              f" {value} (giới hạn {limit})"], className="rule-hit"))
    return out


def _order(side, symbol, qty, price):
    if not symbol:
        raise PortfolioError("Chọn mã để thử lệnh.")
    if qty is None:
        raise PortfolioError("Nhập khối lượng.")
    if float(qty) <= 0 or float(qty) != int(qty):
        raise PortfolioError("Khối lượng phải là số nguyên dương.")
    if not price:
        raise PortfolioError("Nhập giá lệnh.")
    return Order(side, symbol, int(qty), float(price))


def _ratio_cell(snap, p):
    status = ui.ratio_status(snap.ratio, p.call_ratio, p.force_ratio)
    if status == "no_debt":
        return ui.pill("no_debt")
    return html.Span([pct(snap.ratio), " ", ui.pill(status)],
                     style={"display": "inline-flex", "gap": "6px", "alignItems": "center"})


def _sessions(n):
    if n is None:
        return f"Hơn {FLOOR_HORIZON} phiên"
    return "Hiện tại" if n == 0 else f"{n} phiên"


def _delta(before, after, kind):
    if before is None or after is None or any(isinstance(v, float) and math.isinf(v) for v in (before, after)):
        return "–"
    d = after - before
    if abs(d) < 1e-9:
        return "Không đổi"
    return {"money": money(d, signed=True), "pct": pct(d, signed=True).replace("%", " điểm %"),
            "int": f"{'+' if d > 0 else '−'}{abs(d)} phiên"}[kind]


def volatility(r) -> tuple[float, float] | None:
    """Độ biến động năm (250 phiên gần nhất) của phần cổ phiếu trước và sau lệnh; None nếu thiếu dữ liệu."""
    symbols = sorted(set(r.before.weights) | set(r.after.weights))
    if not symbols:
        return None
    try:
        with data.open_warehouse() as wh:
            rets, _ = risk.returns(wh, symbols, risk.SESSIONS_PER_YEAR)
    except LookupError:
        return None
    if len(rets) < risk.MIN_SESSIONS:
        return None
    cov = rets.cov() * risk.SESSIONS_PER_YEAR

    def vol(weights):
        return risk.euler(pd.Series(weights), cov)[1] if weights else 0.0

    return vol(r.before.weights), vol(r.after.weights)


def comparison(r, p, vols=None) -> html.Div:
    b, a = r.before, r.after
    sym = r.order.symbol
    top_b = max(b.weights.items(), key=lambda kv: kv[1], default=(None, None))
    top_a = max(a.weights.items(), key=lambda kv: kv[1], default=(None, None))
    rows = [
        ["Giá trị danh mục", money(b.market_value), money(a.market_value), _delta(b.market_value, a.market_value, "money")],
        ["Dư nợ ròng", money(b.net_debt), money(a.net_debt), _delta(b.net_debt, a.net_debt, "money")],
        ["Tài sản ròng", money(b.equity), money(a.equity), _delta(b.equity, a.equity, "money")],
        ["Tỷ lệ ký quỹ", _ratio_cell(b, p), _ratio_cell(a, p),
         _delta(None if math.isinf(b.ratio) else b.ratio, None if math.isinf(a.ratio) else a.ratio, "pct")],
        ["Mức giảm đến ngưỡng gọi ký quỹ",
         "Không áp dụng" if b.call_gap is None else pct(b.call_gap_pct) if b.call_gap > 0 else "Dưới ngưỡng gọi ký quỹ",
         "Không áp dụng" if a.call_gap is None else pct(a.call_gap_pct) if a.call_gap > 0 else "Dưới ngưỡng gọi ký quỹ",
         _delta(b.call_gap_pct, a.call_gap_pct, "pct")],
        ["Số phiên giảm sàn đến ngưỡng gọi ký quỹ", _sessions(b.floor_sessions) if b.call_gap is not None else "Không áp dụng",
         _sessions(a.floor_sessions) if a.call_gap is not None else "Không áp dụng",
         _delta(b.floor_sessions, a.floor_sessions, "int")],
        [f"Tỷ trọng {sym}", pct(b.weights.get(sym, 0.0)), pct(a.weights.get(sym, 0.0)),
         _delta(b.weights.get(sym, 0.0), a.weights.get(sym, 0.0), "pct")],
        *([["Độ biến động năm", pct(vols[0]), pct(vols[1]), _delta(vols[0], vols[1], "pct")]] if vols else []),
        ["Mã có tỷ trọng lớn nhất", f"{top_b[0]} {pct(top_b[1])}" if top_b[0] else "–",
         f"{top_a[0]} {pct(top_a[1])}" if top_a[0] else "–", ""],
    ]
    body = []
    for label, before, after, delta in rows:
        cell = html.Div([after, html.Div(delta, className="delta")]) if delta and delta != "–" else after
        body.append([label, before, cell])
    return html.Div(ui.table(["", "Trước", "Sau"], body), className="compare")


def lede(r, p) -> list:
    o = r.order
    verb = "Mua" if o.side == BUY else "Bán"
    parts = [f"{verb} {number(o.quantity)} {o.symbol} ở giá {number(o.price)} đồng. Giá trị lệnh {money(r.value)}, "
             f"phí và thuế {money(r.costs)}. "]
    b, a = r.before, r.after
    if a.call_gap is None:
        parts.append("Sau lệnh, tài khoản không có dư nợ ròng.")
        return parts
    added_debt = r.portfolio_after.debt - p.debt
    if added_debt > 0:
        parts += [f"Tiền mặt không đủ. Mô phỏng tính phần thiếu {money(added_debt)} vào dư nợ. "]
    if b.call_gap is None:
        parts += ["Trong kịch bản này, tỷ lệ ký quỹ sau lệnh là ", html.B(pct(a.ratio))]
    else:
        parts += ["Tỷ lệ ký quỹ ", html.B(f"{pct(b.ratio)} → {pct(a.ratio)}")]
    if a.call_gap > 0:
        parts += [". Danh mục chạm ngưỡng gọi ký quỹ nếu giá trị giảm ", html.B(pct(a.call_gap_pct))]
        if a.floor_sessions is not None:
            parts += [f" (khoảng {a.floor_sessions} phiên giảm sàn liên tiếp)"]
        parts.append(".")
    else:
        parts += [". ", html.B("Sau lệnh, tỷ lệ ký quỹ dưới ngưỡng gọi ký quỹ.")]
    return parts


def weights_figure(r, theme) -> go.Figure:
    t = tokens(theme)
    symbols = sorted(set(r.before.weights) | set(r.after.weights), key=lambda s: r.after.weights.get(s, 0))
    fig = base_figure(theme, height=max(160, 40 * len(symbols) + 40))
    for name, snap, color in (("Trước", r.before, t["muted"]), ("Sau", r.after, t["series1"])):
        vals = [snap.weights.get(s, 0.0) for s in symbols]
        fig.add_trace(go.Bar(x=vals, y=symbols, orientation="h", name=name, marker=dict(color=color, cornerradius=4),
                             customdata=[pct(v) for v in vals], hovertemplate=f"{name}: %{{customdata}}<extra></extra>"))
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.1, showlegend=True, hovermode="y unified",
                      margin=dict(l=8, r=16, t=28, b=8), legend=dict(traceorder="normal"))
    fig.update_xaxes(tickformat=".0%", rangemode="tozero")
    fig.update_yaxes(showgrid=False, tickfont=dict(color=t["text"]))
    return fig


# ---------------------------------------------------------------- callbacks
def register(app) -> None:
    @app.callback(Output("wi-price", "value"), Output("wi-hint", "children"), Input("wi-symbol", "value"),
                  prevent_initial_call=True)
    def prefill(symbol):
        return price_hint(symbol)

    @app.callback(Output("wi-lede", "children"), Output("wi-table", "children"), Output("wi-weights", "figure"),
                  Output("wi-weights", "style"), Output("wi-warnings", "children"), Output("wi-apply", "disabled"),
                  Output("wi-results", "style"),
                  Input("wi-side", "value"), Input("wi-symbol", "value"), Input("wi-qty", "value"),
                  Input("wi-price", "value"), Input("wi-fee", "value"), Input("portfolio", "data"),
                  Input("rules", "data"), Input("theme", "data"))
    def compute(side, symbol, qty, price, fee, current, current_rules, theme):
        p = from_dict(current)
        empty = base_figure(theme, 160)
        hidden = {"height": "160px"}
        try:
            order = _order(side, symbol, qty, price)
            q = data.quotes([*p.symbols, order.symbol])
            missing = [s for s in p.symbols if s not in q.index]
            if missing:
                raise PortfolioError(f"Thiếu dữ liệu giá của {', '.join(missing)}. Kiểm tra các mã ở trang Danh mục.")
            r = evaluate(p, order, q, data.liquidity(), fee_rate=(float(fee) if fee is not None else 0.0) / 100)
        except PortfolioError as exc:
            return str(exc), None, empty, hidden, None, True, {"display": "none"}
        fig = weights_figure(r, theme)
        notes = [html.Span(w) for w in r.warnings] + rule_notes(p, r, q, rules.from_dict(current_rules))
        return (lede(r, p), comparison(r, p, volatility(r)), fig, {"height": f"{fig.layout.height}px"}, notes, False,
                {"display": "flex"})

    @app.callback(Output("portfolio", "data", allow_duplicate=True), Output("wi-apply-msg", "children"),
                  Output("wi-apply-msg", "className"), Output("wi-qty", "value"),
                  Input("wi-apply", "n_clicks"),
                  State("wi-side", "value"), State("wi-symbol", "value"), State("wi-qty", "value"),
                  State("wi-price", "value"), State("wi-fee", "value"), State("portfolio", "data"),
                  prevent_initial_call=True)
    def apply_order(clicks, side, symbol, qty, price, fee, current):
        if not clicks or ctx.triggered_id != "wi-apply":
            return no_update, no_update, no_update, no_update
        try:
            p = from_dict(current)
            order = _order(side, symbol, qty, price)
            q = data.quotes([*p.symbols, order.symbol])
            r = evaluate(p, order, q, data.liquidity(), fee_rate=(float(fee) if fee is not None else 0.0) / 100)
        except PortfolioError as exc:
            return no_update, str(exc), "message error", no_update
        verb = "mua" if side == BUY else "bán"
        # Xóa ô khối lượng để không áp cùng một lệnh hai lần khi bấm lại.
        return (to_dict(r.portfolio_after), f"Đã cập nhật danh mục theo lệnh {verb} {number(qty)} {symbol}. "
                "Có thể lưu thay đổi ở trang Danh mục.", "message ok", None)
