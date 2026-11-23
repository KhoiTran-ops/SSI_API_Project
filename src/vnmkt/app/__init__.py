"""Giao diện Dash. Chạy: python main.py run (chỉ mở trên máy này, 127.0.0.1)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

from dash import Dash, Input, Output, State, dcc, html

from .. import performance, rules
from ..portfolio import to_dict
from . import (data, page_journal, page_market, page_overview, page_performance, page_portfolio, page_risk, page_rules,
               page_stress, page_whatif)

DISCLAIMER = "Công cụ phân tích học thuật, không phải khuyến nghị đầu tư."
NAV = [
    ("Theo dõi", [(page_overview.PATH, "Tổng quan"), (page_market.PATH, "Thị trường")]),
    ("Trước khi giao dịch", [(page_stress.PATH, "Kịch bản giảm giá"), (page_whatif.PATH, "Thử lệnh")]),
    ("Phân tích", [(page_risk.PATH, "Rủi ro"), (page_performance.PATH, "Hiệu suất")]),
    ("Thiết lập", [(page_portfolio.PATH, "Danh mục và tài khoản"), (page_journal.PATH, "Nhật ký giao dịch"),
                   (page_rules.PATH, "Luật rủi ro")]),
]
PAGES = [page for _, pages in NAV for page in pages]


def _sidebar() -> html.Aside:
    return html.Aside([
        html.Div([html.Div("R", className="brand-mark"),
                  html.Div([html.Div("Rủi ro ký quỹ", className="brand-name"),
                            html.Div("Kiểm soát rủi ro tài khoản",
                                     className="brand-sub")])], className="brand"),
        html.Nav(_nav_items(), className="nav"),
    ], className="sidebar")


def _nav_items() -> list:
    items, i = [], 0
    for group, pages in NAV:
        items.append(html.Div(group, className="nav-group"))
        for path, label in pages:
            items.append(dcc.Link(label, href=path, id=f"nav-{i}"))
            i += 1
    return items


RUN_STATUS = {"ok": ("good", "Đã cập nhật"), "partial": ("warning", "Thiếu dữ liệu cập nhật"), "failed": ("critical", "Lỗi cập nhật")}


def status_view(s: dict, simulated: bool = False) -> list:
    """Thanh trên cùng: nguồn danh mục, ngày dữ liệu, lần cập nhật SSI, lần ghi thời gian thực."""
    if s.get("error"):
        return [html.Span("Không đọc được kho dữ liệu", className="pill critical")]
    items = [html.Span("Danh mục mô phỏng" if simulated else "Danh mục nhập tay", className="chip"),
             html.Span(f"Giá đóng cửa {s['session']:%d/%m/%Y}" if s["session"] else "Chưa có giá đóng cửa")]
    if s["run_status"]:
        tone, label = RUN_STATUS.get(s["run_status"], ("neutral", s["run_status"]))
        items.append(html.Span([html.Span(label, className=f"pill {tone}"),
                                f" {s['run_at']:%H:%M %d/%m}" if s["run_at"] else ""]))
    if s["tick_at"]:
        items.append(html.Span(f"Trong phiên {s['tick_at']:%H:%M %d/%m}"))
    items.append(html.Span("Dữ liệu mẫu" if s["sample"] else "SSI FastConnect", className="muted"))
    return items


def create_app() -> Dash:
    app = Dash(__name__, assets_folder=str(Path(__file__).parent / "assets"), title="Rủi ro ký quỹ",
               suppress_callback_exceptions=True, update_title=None)
    app.layout = html.Div([
        dcc.Location(id="url"),
        dcc.Store(id="portfolio", data=to_dict(data.initial_portfolio())),
        dcc.Store(id="rules", data=rules.to_dict(data.initial_rules())),
        dcc.Store(id="journal", data=performance.to_dict(data.initial_journal())),
        dcc.Store(id="theme", data="light"),
        _sidebar(),
        html.Main([html.Div(id="data-status", className="topbar"), html.Div(id="content"),
                   html.Footer(DISCLAIMER, className="footer")], className="main"),
    ], className="shell")

    # Chế độ sáng/tối theo hệ điều hành, để biểu đồ (Plotly không đọc biến CSS) dùng đúng bảng màu.
    app.clientside_callback(
        """function(_) {
            const m = window.matchMedia('(prefers-color-scheme: dark)');
            return m.matches ? 'dark' : 'light';
        }""", Output("theme", "data"), Input("url", "pathname"))

    @app.callback(Output("data-status", "children"), Input("url", "pathname"), Input("portfolio", "data"))
    def refresh_status(_, current):
        return status_view(data.status(), bool((current or {}).get("simulated")))

    @app.callback(Output("content", "children"), *[Output(f"nav-{i}", "className") for i in range(len(PAGES))],
                  Input("url", "pathname"), State("url", "search"), State("portfolio", "data"), State("rules", "data"))
    def route(path, search, current, current_rules):
        # Tham số trên địa chỉ (ví dụ /thu-lenh?ma=HPG&kl=20000) chỉ dùng để điền sẵn ô nhập.
        params = {k: v[0] for k, v in parse_qs((search or "").lstrip("?")).items() if v}
        active = [("active" if path == p else "") for p, _ in PAGES]
        layouts = {page_stress.PATH: lambda: page_stress.layout(params),
                   page_whatif.PATH: lambda: page_whatif.layout(params),
                   page_risk.PATH: page_risk.layout, page_performance.PATH: page_performance.layout,
                   page_market.PATH: page_market.layout, page_journal.PATH: page_journal.layout,
                   page_portfolio.PATH: lambda: page_portfolio.layout(current),
                   page_rules.PATH: lambda: page_rules.layout(current_rules)}
        page = layouts[path]() if path in layouts else page_overview.layout()
        if path not in dict(PAGES):
            active[0] = "active"
        return page, *active

    page_overview.register(app)
    page_portfolio.register(app)
    page_journal.register(app)
    page_stress.register(app)
    page_whatif.register(app)
    page_risk.register(app)
    page_rules.register(app)
    page_performance.register(app)
    page_market.register(app)
    return app


def run(port: int = 8061, debug: bool = False) -> None:
    create_app().run(host="127.0.0.1", port=port, debug=debug)
