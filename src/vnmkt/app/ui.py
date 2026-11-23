"""Các khối giao diện dùng chung: thẻ chỉ số, nhãn trạng thái, thanh ngưỡng ký quỹ, ô nhập, khung."""

from __future__ import annotations

import math

from dash import dcc, html

from .theme import pct

STATUS = {  # trạng thái -> (lớp màu, nhãn)
    "no_debt": ("neutral", "Không có dư nợ ròng"),
    "safe": ("good", "An toàn"),
    "near": ("warning", "Gần ngưỡng"),
    "call": ("serious", "Gọi ký quỹ"),
    "force": ("critical", "Bán giải chấp"),
    "pass": ("good", "Đạt"),
    "fail": ("critical", "Vi phạm"),
}
NEAR_MARGIN = 0.05  # cách ngưỡng gọi ký quỹ dưới 5 điểm % thì báo "gần ngưỡng"


def ratio_status(ratio: float, call: float, force: float) -> str:
    if math.isinf(ratio) and ratio > 0:
        return "no_debt"
    if ratio < force:
        return "force"
    if ratio < call:
        return "call"
    return "near" if ratio < call + NEAR_MARGIN else "safe"


def pill(status: str, text: str | None = None) -> html.Span:
    tone, label = STATUS[status]
    return html.Span(text or label, className=f"pill {tone}")


def tile(label: str, value, sub=None, extra=None, badge=None) -> html.Div:
    return html.Div([html.Div([html.Span(label), badge], className="tile-label"),
                     html.Span(value, className="tile-value"),
                     *( [html.Span(sub, className="tile-sub")] if sub is not None else []),
                     *( [extra] if extra is not None else [])], className="panel tile")


def meter(ratio: float, call: float, force: float) -> html.Div:
    """Thanh 0–100%: vùng bán giải chấp, vùng gọi ký quỹ, vạch là tỷ lệ hiện tại."""
    shown = 1.0 if math.isinf(ratio) else min(max(ratio, 0.0), 1.0)
    return html.Div([
        html.Div(className="meter-zone critical", style={"left": 0, "width": f"{force * 100:.2f}%"}),
        html.Div(className="meter-zone serious", style={"left": f"{force * 100:.2f}%",
                                                         "width": f"{(call - force) * 100:.2f}%"}),
        html.Div(className="meter-mark", style={"left": f"{shown * 100:.2f}%"}),
    ], className="meter", role="img",
        **{"aria-label": f"Tỷ lệ ký quỹ {pct(ratio)}. Gọi ký quỹ dưới {pct(call, 0)}, "
                         f"bán giải chấp dưới {pct(force, 0)}"})


def panel(title: str | None, *children, hint=None, body=True, class_name: str = "") -> html.Div:
    head = [html.Div([html.H2(title), html.Span(hint, className="hint") if hint else None], className="panel-head")
            ] if title else []
    content = [html.Div(list(children), className="panel-body")] if body else list(children)
    return html.Div(head + content, className=f"panel {class_name}".strip())


def field(label: str, control, help_text: str | None = None) -> html.Label:
    return html.Label([html.Span(label), control, html.Small(help_text) if help_text else None], className="field")


def number_input(id_, value, step="any", min_=None, max_=None, placeholder=None) -> dcc.Input:
    return dcc.Input(id=id_, type="number", value=value, step=step, min=min_, max=max_, debounce=True,
                     placeholder=placeholder, className="input", inputMode="decimal")


def symbol_dropdown(id_, symbols, value=None) -> dcc.Dropdown:
    """Ô chọn mã: tìm theo mã hoặc tên công ty."""
    options = [{"label": f"{r.symbol} · {r.name or ''}", "value": r.symbol, "search": f"{r.symbol} {r.name or ''}"}
               for r in symbols.itertuples()]
    return dcc.Dropdown(id=id_, options=options, value=value, placeholder="Tìm mã", searchable=True, clearable=True,
                        className="dropdown", optionHeight=34,
                        labels={"search": "Tìm mã hoặc tên công ty", "clear_search": "Xóa",
                                "clear_selection": "Bỏ chọn", "no_options_found": "Không tìm thấy mã"})


def segmented(id_, options: list[tuple[str, str]], value: str) -> dcc.RadioItems:
    return dcc.RadioItems(
        id=id_, value=value, className="segmented", inline=True,
        options=[{"label": html.Span(label, className="seg-option"), "value": key} for key, label in options])


def method_note(items: list[str], title: str = "Cách tính và giới hạn") -> html.Div:
    """Khối thu gọn ở cuối trang: công thức và giới hạn, đọc khi cần, không chiếm chỗ."""
    return html.Div(html.Details([html.Summary(title), html.Div(html.Ul([html.Li(x) for x in items], className="method"),
                                                               className="disclosure-body")],
                                 className="disclosure"), className="panel")


def table(columns: list[str], rows: list[list]) -> html.Div:
    return html.Div(html.Table([html.Thead(html.Tr([html.Th(c) for c in columns])),
                                html.Tbody([html.Tr([html.Td(v) for v in r]) for r in rows])], className="table"),
                    className="table-wrap")
