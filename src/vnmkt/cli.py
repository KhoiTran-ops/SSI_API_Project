"""Dòng lệnh: python main.py <lệnh>. Chạy `python main.py -h` để xem các lệnh."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import date, datetime, timedelta

from . import pipeline, store
from .config import BOARD, ROOT, VN_TZ, ConfigError, credentials, data_dir
from .ssi import MarketData, SsiError


def _fmt(day) -> str:
    return day.strftime("%Y/%m/%d")


def check(_: argparse.Namespace) -> int:
    """Kiểm tra kết nối và từng endpoint sẽ dùng; chỉ đọc, không ghi dữ liệu."""
    today = datetime.now(VN_TZ).date()
    session = [_fmt(today)]  # thay bằng phiên cuối của VNINDEX khi đã lấy được (ngày nghỉ không có dữ liệu)
    with MarketData(*credentials()) as md:
        print("Xác thực: OK (token Market Data, không có quyền giao dịch)")
        steps = [
            ("securitiesByBoard HOSE", lambda: md.securities(BOARD)),
            ("securitiesSummary VCB, 3 năm", lambda: md.summary(_fmt(today - timedelta(days=1100)), _fmt(today), symbol="VCB")),
            ("securitiesSummary index VNINDEX, 30 ngày", lambda: md.summary(_fmt(today - timedelta(days=30)), _fmt(today), index="VNINDEX")),
            ("indexSummary HOSE, phiên cuối", lambda: md.board_summary(BOARD, session[-1])),
            ("corporateActions 5 ngày", lambda: md.corporate_actions(_fmt(today - timedelta(days=4)), _fmt(today))),
            ("masterdata phiên cuối (mọi sàn)", lambda: md.master_data(session[-1], session[-1])),
        ]
        for name, call in steps:
            try:
                rows = call()
            except SsiError as exc:
                print(f"  {name:<42} LỖI: {exc}")
                continue
            dates = sorted(str(r.get("tradingDate", "")) for r in rows if isinstance(r, dict) and r.get("tradingDate"))
            span = f"{dates[0]} → {dates[-1]}" if dates else ""
            print(f"  {name:<42} {len(rows):>6} bản ghi  {span}")
            if "VNINDEX" in name and dates:
                session.append(dates[-1][:10])
        print(f"Giới hạn tần suất (header lần gọi cuối): {md.rate_limit or 'không có'}")
    return 0


def update(args: argparse.Namespace) -> int:
    """Nạp lần đầu hoặc cập nhật phần còn thiếu; --full tải lại toàn bộ lịch sử."""
    root = data_dir()
    key = credentials()
    with store.Warehouse(root / "lake") as wh:
        started, status, calls, failures = store.now(), "failed", 0, []
        try:
            with MarketData(*key, on_response=store.RawLog(root / "raw")) as md:
                job = pipeline.Pipeline(md, wh)
                try:
                    result = job.run(full=args.full)
                finally:
                    calls, failures = md.calls, job.failures
            status = "partial" if failures else "ok"
        except SsiError as exc:
            failures = [*failures, str(exc)]
            raise
        finally:
            pipeline.record_run(wh, started, status, calls, failures)
    for table, n in result.items():
        print(f"  {table:<18} {n:>8} dòng ghi")
    for failure in job.failures:
        print(f"  Bỏ qua — {failure}")
    return 1 if job.failures else 0


def rebuild(_: argparse.Namespace) -> int:
    """Dựng lại kho từ data/raw (không gọi SSI), dùng khi thay đổi quy tắc chuẩn hóa."""
    root = data_dir()
    if not (root / "raw").is_dir():
        raise ConfigError(f"Không có phản hồi gốc trong {root / 'raw'}.")
    with store.Warehouse(root / "lake") as wh:
        for table, n in pipeline.rebuild(store.RawLog(root / "raw"), wh).items():
            print(f"  {table:<18} {n:>8} dòng ghi")
    return 0


def quality(_: argparse.Namespace) -> int:
    """In báo cáo chất lượng dữ liệu từ kho (không gọi SSI)."""
    path = data_dir() / "lake"
    if not path.exists():
        raise ConfigError("Chưa có kho dữ liệu; chạy `python main.py update` trước.")
    with store.Warehouse(path, read_only=True) as wh:
        report = pipeline.quality(wh)
        totals = wh.query("SELECT (SELECT count(*) FROM daily) AS daily, (SELECT count(*) FROM index_daily) AS index_daily,"
                          " (SELECT count(*) FROM board_daily) AS board_daily, (SELECT max(date) FROM daily) AS last_day")
    print(totals.to_string(index=False))
    print(f"Mã: {len(report)}, thiếu phiên: {(report['missing_sessions'] > 0).sum()}, "
          f"thiếu dữ liệu đặt lệnh: {(report['missing_orders'] > 0).sum()}")
    print(report.head(15).to_string(index=False))
    return 0


SAMPLE_START = date(2022, 1, 1)  # HOSE từ 2022: có đủ đợt giảm 2022 cho kịch bản lịch sử
# Toàn bộ lịch sử chỉ cho vài mã ngoài HOSE, để có ví dụ biên độ sàn HNX (10%) và UPCoM (15%).
SAMPLE_FULL = ("SHS", "PVS", "MSR", "ACV")


def sample(_: argparse.Namespace) -> int:
    """Tạo data_sample/lake từ kho thật: mọi cổ phiếu HOSE từ SAMPLE_START và toàn bộ lịch sử vài mã HNX/UPCoM.
    Chỉ có dữ liệu thị trường công khai, không có dữ liệu tài khoản."""
    src = data_dir() / "lake"
    dst = ROOT / "data_sample" / "lake"
    if store.scan(src, "daily") is None:
        raise ConfigError("Chưa có kho dữ liệu thật để trích mẫu; chạy `python main.py update` trước.")
    shutil.rmtree(dst, ignore_errors=True)
    t = {name: store.scan(src, name) for name in store.TABLES}
    hose = f"symbol IN (SELECT symbol FROM {t['securities']} WHERE board = 'HOSE')"
    full = list(SAMPLE_FULL)
    steps = {
        "securities": (f"SELECT * FROM {t['securities']}", []),
        "index_daily": (f"SELECT * FROM {t['index_daily']} WHERE date >= ?", [SAMPLE_START]),
        "daily": (f"SELECT * FROM {t['daily']} WHERE (date >= ? AND {hose}) OR (symbol IN ? AND NOT {hose})",
                  [SAMPLE_START, full]),
        "board_daily": (f"SELECT * FROM {t['board_daily']}", []),
        "corporate_actions": (f"SELECT * FROM {t['corporate_actions']} WHERE {hose} OR symbol IN ?", [full]),
        "master": (f"SELECT * FROM {t['master']} WHERE date = (SELECT max(date) FROM {t['master']}) "
                   f"AND ({hose} OR symbol IN ?)", [full]),
        "runs": (f"SELECT * FROM {t['runs']}", []),
    }
    with store.Warehouse(dst) as wh:
        for table, (sql, params) in steps.items():
            if t[table] is None:
                continue
            print(f"  {table:<18} {wh.insert_sql(table, sql, params):>8} dòng")
    size = sum(f.stat().st_size for f in dst.rglob("*.parquet"))
    print(f"Đã tạo {dst} ({size / 1e6:.1f} MB).")
    return 0


def check_access(args: argparse.Namespace) -> int:
    """Kiểm tra quyền truy vấn tài khoản và WebSocket; chỉ đọc, không in số tài khoản hay số dư."""
    from . import access
    with MarketData(*credentials()) as md:
        access.check_rest(md)
        if args.seconds > 0:
            access.check_stream(md, args.seconds)
    return 0


def record(args: argparse.Namespace) -> int:
    """Ghi dữ liệu thời gian thực vào data/lake/ticks. Mặc định: các mã trong danh mục và VN-Index."""
    from . import portfolio, recorder
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        path = ROOT / "data" / "portfolio.json"
        symbols = portfolio.load(path if path.exists() else ROOT / "data_sample" / "portfolio.json").symbols
    writer = store.TickWriter(data_dir() / "lake")
    with MarketData(*credentials()) as md:
        counts = recorder.Recorder(md, writer, symbols, ["VNINDEX"]).run(args.minutes * 60)
    print(f"Đã ghi {sum(counts.values())} thông điệp: {counts}")
    return 0


def tick_check(args: argparse.Namespace) -> int:
    """Báo cáo chất lượng dữ liệu thời gian thực của một ngày (mặc định hôm nay)."""
    from . import tick_quality
    day = date.fromisoformat(args.date) if args.date else datetime.now(VN_TZ).date()
    root = data_dir() / "lake"
    trades = tick_quality.read_topic(root, day, "trade")
    with store.Warehouse(root, read_only=True) as wh:
        refs = tick_quality.references(wh, sorted(set(trades["symbol"])) if len(trades) else [], day)
    r = tick_quality.check(root, day, refs)
    print(f"Ngày {day:%d/%m/%Y}: {r.counts}, {r.symbols} mã")
    print(f"  trùng {r.duplicates}, sai thứ tự {r.out_of_order}, đến muộn >{tick_quality.LATE_SECONDS}s {r.late}, "
          f"bất thường {r.abnormal}, giá mua > giá bán {r.crossed_quotes}")
    print(f"  mất tín hiệu >{tick_quality.GAP_SECONDS}s trong giờ khớp lệnh: {len(r.gaps)}"
          + "".join(f"\n    {a:%H:%M:%S} -> {b:%H:%M:%S}" for a, b in r.gaps))
    if not refs:
        print("  (không kiểm tra biên độ: kho chưa có phiên trước ngày này)")
    return 0 if r.ok else 1


def run(args: argparse.Namespace) -> int:
    """Mở giao diện trên máy này (127.0.0.1); không cần khóa SSI vì chỉ đọc kho."""
    from .app import run as serve
    serve(port=args.port, debug=args.debug)
    return 0


def main(argv: list[str] | None = None) -> int:
    # Khi đầu ra bị chuyển hướng (tệp log, pipe), Python trên Windows dùng cp1252 và lỗi khi in tiếng Việt.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="main.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="kiểm tra kết nối SSI").set_defaults(func=check)
    up = sub.add_parser("update", help="nạp lần đầu hoặc cập nhật dữ liệu vào kho")
    up.add_argument("--full", action="store_true", help="tải lại toàn bộ lịch sử")
    up.set_defaults(func=update)
    sub.add_parser("sample", help="trích bộ dữ liệu mẫu nhỏ vào data_sample/").set_defaults(func=sample)
    sub.add_parser("rebuild", help="dựng lại kho từ phản hồi gốc, không gọi SSI").set_defaults(func=rebuild)
    acc = sub.add_parser("check-access", help="kiểm tra quyền tài khoản và WebSocket (chỉ đọc)")
    acc.add_argument("--seconds", type=int, default=20, help="số giây nghe WebSocket (0 để bỏ qua)")
    acc.set_defaults(func=check_access)
    sub.add_parser("quality", help="báo cáo chất lượng dữ liệu").set_defaults(func=quality)
    rec = sub.add_parser("record", help="ghi dữ liệu thời gian thực (WebSocket) vào kho")
    rec.add_argument("--minutes", type=float, default=30, help="số phút ghi (Ctrl+C để dừng sớm)")
    rec.add_argument("--symbols", default="", help="danh sách mã, cách nhau bởi dấu phẩy (mặc định: mã trong danh mục)")
    rec.set_defaults(func=record)
    tq = sub.add_parser("tick-quality", help="kiểm tra chất lượng dữ liệu thời gian thực đã ghi")
    tq.add_argument("--date", default="", help="ngày YYYY-MM-DD (mặc định hôm nay)")
    tq.set_defaults(func=tick_check)
    srv = sub.add_parser("run", help="mở giao diện tại http://127.0.0.1:<port>")
    srv.add_argument("--port", type=int, default=8061)
    srv.add_argument("--debug", action="store_true", help="chế độ phát triển của Dash (tự tải lại)")
    srv.set_defaults(func=run)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx ghi cả URL kèm tham số (có số tài khoản) ở mức INFO: không được in ra.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return args.func(args)
    except (ConfigError, SsiError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1
