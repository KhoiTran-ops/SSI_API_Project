"""Trang Luật rủi ro: người dùng tự đặt giới hạn và xem danh mục đang đạt hay vi phạm."""

from __future__ import annotations

import math

from dash import Input, Output, State, ctx, html, no_update

from .. import rules
from ..portfolio import PortfolioError, from_dict
from . import data, ui
from .theme import number, pct

PATH = "/luat"
PERCENT_RULES = ("max_symbol_weight", "max_sector_weight", "min_margin_ratio")
HELP = {
    "max_symbol_weight": "Tỷ trọng tính theo giá trị danh mục",
    "max_sector_weight": "Nhóm ngành theo ICB cấp 2",
    "min_margin_ratio": "Chỉ áp dụng khi có dư nợ ròng",
    "max_sessions_to_sell": "Giả định lệnh bán chiếm 10% khối lượng khớp mỗi phiên giảm sàn",
}


def _field_value(r: rules.Rules, key: str):
    v = getattr(r, key)
    if v is None:
        return None
    return round(v * 100, 2) if key in PERCENT_RULES else v


def layout(current_rules: dict) -> html.Div:
    r = rules.from_dict(current_rules)
    fields = []
    for key in ("max_symbol_weight", "max_sector_weight", "min_margin_ratio", "max_sessions_to_sell"):
        unit = " (%)" if key in PERCENT_RULES else " (phiên)"
        # Không đặt min/max: Dash 4 gửi None khi giá trị ngoài khoảng, luật sẽ bị tắt mà người dùng không biết.
        fields.append(ui.field(rules.LABELS[key] + unit, ui.number_input(f"rl-{key}", _field_value(r, key)),
                               HELP[key]))
    return html.Div([
        html.Div([html.Div([html.H1("Luật rủi ro"),
                            html.P("Đặt giới hạn cho danh mục và lệnh mô phỏng, để trống ô tương ứng để tắt luật.")])], className="page-head"),
        html.Div([
            ui.panel("Giới hạn", html.Div(fields, className="fields"),
                     html.Div([html.Button("Lưu trên máy", id="rl-save", className="btn primary", n_clicks=0),
                               html.Span(id="rl-msg", className="message", role="status")],
                              className="row", style={"marginTop": "14px", "alignItems": "center"})),
            ui.panel("Kiểm tra danh mục", html.Div(id="rl-status")),
        ], className="grid-even"),
    ], className="page")


def _fmt(key: str, value: float) -> str:
    if key in PERCENT_RULES:
        return pct(value)
    return "Không bán được" if math.isinf(value) else f"{number(value)} phiên"


def status_view(checks: list[rules.Check], r: rules.Rules) -> html.Div:
    rows = []
    for key in ("max_symbol_weight", "max_sector_weight", "min_margin_ratio", "max_sessions_to_sell"):
        limit = getattr(r, key)
        items = [c for c in checks if c.rule == key]
        if limit is None:
            rows.append([rules.LABELS[key], "Tắt", "–", "–"])
            continue
        if not items:
            rows.append([rules.LABELS[key], _fmt(key, limit), "Không áp dụng", "–"])
            continue
        worst = min(items, key=lambda c: c.value) if key == "min_margin_ratio" else max(items, key=lambda c: c.value)
        bad = [c for c in items if not c.passed]
        label = worst.subject if key != "min_margin_ratio" else ""
        rows.append([rules.LABELS[key], ("≥ " if key == "min_margin_ratio" else "≤ ") + _fmt(key, limit),
                     f"{label} {_fmt(key, worst.value)}".strip(),
                     ui.pill("fail", f"Vi phạm ({len(bad)})") if bad else ui.pill("pass")])
    return html.Div(ui.table(["Luật", "Giới hạn", "Gần giới hạn nhất", "Trạng thái"], rows), className="compare")


def register(app) -> None:
    keys = ("max_symbol_weight", "max_sector_weight", "min_margin_ratio", "max_sessions_to_sell")

    @app.callback(Output("rules", "data"), Output("rl-msg", "children"), Output("rl-msg", "className"),
                  *[Input(f"rl-{k}", "value") for k in keys], Input("rl-save", "n_clicks"),
                  State("rules", "data"), prevent_initial_call=True)
    def update(*args):
        *values, save, current = args
        try:
            r = rules.from_dict({k: (None if v is None else (v / 100 if k in PERCENT_RULES else v))
                                 for k, v in zip(keys, values)})
        except PortfolioError as exc:
            return no_update, str(exc), "message error"
        if ctx.triggered_id == "rl-save" and save:
            path = data.save_rules(r)
            return rules.to_dict(r), f"Đã lưu giới hạn vào {path.parent.name}/{path.name}.", "message ok"
        return rules.to_dict(r), "", "message"

    @app.callback(Output("rl-status", "children"), Input("rules", "data"), Input("portfolio", "data"))
    def status(current_rules, current):
        p, r = from_dict(current), rules.from_dict(current_rules)
        if not p.holdings:
            return html.P("Chưa có mã trong danh mục.", className="muted")
        checks = rules.check(p, data.quotes(p.symbols), data.liquidity(), r)
        return status_view(checks, r)
