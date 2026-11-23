"""Hệ màu và định dạng số dùng chung cho giao diện.

Màu theo bảng tham chiếu đã kiểm chứng của dataviz (series-1 xanh dương, series-2 cam; bốn màu trạng thái
dành riêng cho an toàn / cảnh báo / gọi ký quỹ / bán giải chấp và luôn đi kèm nhãn chữ).
"""

from __future__ import annotations

import math

import plotly.graph_objects as go

THEMES = {
    "light": {"surface": "#fcfcfb", "text": "#0b0b0b", "text2": "#52514e", "muted": "#8a8984",
              "grid": "#ebeae6", "axis": "#d6d5d0", "series1": "#2a78d6", "series2": "#eb6834", "series3": "#1baf7a",
              "good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
              "band": "rgba(208,59,59,0.06)"},
    "dark": {"surface": "#1a1a19", "text": "#f0efec", "text2": "#c3c2b7", "muted": "#8f8e86",
             "grid": "#2b2b29", "axis": "#3a3a37", "series1": "#3987e5", "series2": "#d95926", "series3": "#199e70",
             "good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
             "band": "rgba(208,59,59,0.10)"},
}
FONT = "Be Vietnam Pro, Segoe UI, system-ui, sans-serif"


def tokens(theme: str | None) -> dict:
    return THEMES.get(theme or "light", THEMES["light"])


def base_figure(theme: str | None, height: int = 300) -> go.Figure:
    t = tokens(theme)
    fig = go.Figure()
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(family=FONT, size=12, color=t["text2"]),
        hoverlabel=dict(bgcolor=t["surface"], bordercolor=t["axis"], font=dict(family=FONT, color=t["text"])),
        showlegend=False, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(color=t["text2"])),
        separators=",.")
    axis = dict(showgrid=True, gridcolor=t["grid"], gridwidth=1, zeroline=False, linecolor=t["axis"],
                tickfont=dict(color=t["muted"]), ticks="", showline=False, automargin=True)
    fig.update_xaxes(**axis, showspikes=True, spikecolor=t["axis"], spikethickness=1, spikedash="solid",
                     spikemode="across", fixedrange=True)
    fig.update_yaxes(**axis, fixedrange=True)
    fig.update_layout(dragmode=False)
    return fig


def fit_range(values, include=(), pad: float = 0.08) -> list[float] | None:
    """Khoảng trục y vừa các điểm đang thấy cùng các mốc phải luôn hiện (ngưỡng, mốc 100).

    assets/charts.js dùng đúng công thức này khi người dùng kéo hoặc thu phóng trục thời gian.
    """
    vals = [float(v) for v in list(values) + list(include)
            if v is not None and isinstance(v, (int, float)) and math.isfinite(v)]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    span = hi - lo or abs(hi) * 0.1 or 1.0
    return [lo - span * pad, hi + span * pad]


def pannable(fig: go.Figure, theme: str | None, x0, x1, ys, include=()) -> go.Figure:
    """Biểu đồ đường: kéo và thu phóng theo thời gian trong đúng khoảng có dữ liệu, trục y tự co giãn theo.

    `x0`, `x1`: đầu và cuối dữ liệu (chỉ số phiên với trục category, ngày với trục ngày).
    `include`: các giá trị y luôn nằm trong khung nhìn. Nhấp đúp hoặc nút đặt lại đưa về toàn bộ khoảng.
    """
    t = tokens(theme)
    if isinstance(x0, (int, float)):
        x0, x1 = x0 - 0.5, x1 + 0.5
    fig.update_xaxes(fixedrange=False, range=[x0, x1], minallowed=x0, maxallowed=x1)
    fig.update_yaxes(range=fit_range(ys, include))
    fig.update_layout(dragmode="pan", meta={"yfit": {"include": [float(v) for v in include]}},
                      modebar=dict(bgcolor="rgba(0,0,0,0)", color=t["muted"], activecolor=t["series1"]))
    return fig


GRAPH_CONFIG = {"displayModeBar": False, "responsive": True, "scrollZoom": False, "doubleClick": False}
LINE_CONFIG = {"responsive": True, "scrollZoom": True, "displaylogo": False, "doubleClick": "reset",
               "modeBarButtons": [["pan2d", "zoom2d", "resetScale2d"]], "locale": "vi"}


# ---- định dạng số kiểu Việt Nam: phân cách nghìn là dấu chấm, thập phân là dấu phẩy ----
def _vn(text: str) -> str:
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def number(value: float | None, digits: int = 0) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    return _vn(f"{value:,.{digits}f}")


def money(value: float | None, signed: bool = False) -> str:
    """Số tiền rút gọn: 1,83 tỷ; 826 tr; 12.300 đ."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    sign = ("+" if value > 0 else "−" if value < 0 else "") if signed else ("−" if value < 0 else "")
    v = abs(value)
    if v >= 1e9:
        body = f"{number(v / 1e9, 2)} tỷ"
    elif v >= 1e6:
        body = f"{number(v / 1e6, 0 if v >= 1e8 else 1)} tr"
    else:
        body = f"{number(v)} đ"
    return sign + body


def pct(value: float | None, digits: int = 1, signed: bool = False) -> str:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return "–"
    sign = "+" if signed and value > 0 else "−" if value < 0 else ""
    return sign + _vn(f"{abs(value) * 100:,.{digits}f}") + "%"
