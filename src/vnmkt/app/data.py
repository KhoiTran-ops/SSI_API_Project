"""Truy cập dữ liệu cho giao diện: kho Parquet (chỉ đọc), danh mục người dùng, các bảng tính sẵn có cache."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

from .. import market, performance, rules, store
from ..config import ROOT, data_dir
from ..portfolio import Portfolio, load, save

SAMPLE_LAKE = ROOT / "data_sample" / "lake"
SAMPLE_PORTFOLIO = ROOT / "data_sample" / "portfolio.json"


def lake() -> tuple[Path, bool]:
    """Kho đang dùng và cờ "là dữ liệu mẫu". Không có kho thật thì dùng data_sample."""
    real = data_dir() / "lake"
    if store.scan(real, "daily") is not None:
        return real, real.resolve() == SAMPLE_LAKE.resolve()
    return SAMPLE_LAKE, True


def _key() -> str:
    """Khóa cache: đường dẫn kho + thời điểm thay thư mục daily (đổi sau mỗi lần cập nhật kho)."""
    root, _ = lake()
    daily = root / "daily"
    return f"{root}|{daily.stat().st_mtime_ns if daily.exists() else 0}"


def _root(key: str) -> Path:
    return Path(key.split("|", 1)[0])


def open_warehouse() -> store.Warehouse:
    """Mở kho đang dùng ở chế độ chỉ đọc (dùng trong `with`)."""
    return store.Warehouse(lake()[0], read_only=True)


@lru_cache(maxsize=2)
def _liquidity(key: str) -> market.FloorLiquidity:
    with store.Warehouse(_root(key), read_only=True) as wh:
        return market.floor_liquidity(wh)


def liquidity() -> market.FloorLiquidity:
    return _liquidity(_key())


@lru_cache(maxsize=2)
def _symbols(key: str) -> pd.DataFrame:
    with store.Warehouse(_root(key), read_only=True) as wh:
        return market.known_symbols(wh)


def symbols() -> pd.DataFrame:
    return _symbols(_key())


@lru_cache(maxsize=64)
def _quotes(key: str, symbols_: tuple[str, ...]) -> pd.DataFrame:
    with store.Warehouse(_root(key), read_only=True) as wh:
        return market.quotes(wh, list(symbols_))


def quotes(symbols_: list[str]) -> pd.DataFrame:
    return _quotes(_key(), tuple(sorted(set(symbols_))))


@lru_cache(maxsize=4)
def _path_2022(key: str, symbols_: tuple[str, ...]):
    with store.Warehouse(_root(key), read_only=True) as wh:
        start, end = market.drawdown_window(wh, 2022)
        returns, volume, replaced = market.historical_path(wh, list(symbols_), start, end)
    return start, end, returns, volume, replaced


def path_2022(symbols_: list[str]):
    start, end, returns, volume, replaced = _path_2022(_key(), tuple(symbols_))
    return start, end, returns.copy(), volume.copy(), replaced


@lru_cache(maxsize=1)
def _last_session(key: str):
    with store.Warehouse(_root(key), read_only=True) as wh:
        return wh.scalar("SELECT max(date) FROM index_daily WHERE symbol = 'VNINDEX'")


def last_session():
    return _last_session(_key())


def user_portfolio_path() -> Path:
    """Luôn trong data/ (Git bỏ qua), kể cả khi chạy bằng DATA_DIR=data_sample, để không ghi đè bộ mẫu."""
    return ROOT / "data" / "portfolio.json"


def initial_portfolio() -> Portfolio:
    path = user_portfolio_path()
    return load(path if path.exists() else SAMPLE_PORTFOLIO)


def sample_portfolio() -> Portfolio:
    return load(SAMPLE_PORTFOLIO)


def save_portfolio(p: Portfolio) -> Path:
    path = user_portfolio_path()
    save(p, path)
    return path


def rules_path() -> Path:
    return ROOT / "data" / "rules.json"


def initial_rules() -> rules.Rules:
    return rules.load(rules_path())


def save_rules(r: rules.Rules) -> Path:
    rules.save(r, rules_path())
    return rules_path()


SAMPLE_JOURNAL = ROOT / "data_sample" / "journal.json"


def journal_path() -> Path:
    return ROOT / "data" / "journal.json"


def initial_journal() -> performance.Journal:
    return performance.load(journal_path() if journal_path().exists() else SAMPLE_JOURNAL)


def sample_journal() -> performance.Journal:
    return performance.load(SAMPLE_JOURNAL)


def save_journal(j: performance.Journal) -> Path:
    performance.save(j, journal_path())
    return journal_path()


def status() -> dict:
    """Trạng thái dữ liệu cho thanh bên: phiên cuối, lần cập nhật cuối, lần ghi thời gian thực cuối."""
    root, sample = lake()
    out = {"sample": sample, "session": None, "run_at": None, "run_status": None, "tick_at": None}
    try:
        with store.Warehouse(root, read_only=True) as wh:
            out["session"] = wh.scalar("SELECT max(date) FROM index_daily WHERE symbol = 'VNINDEX'")
            row = wh.rows("SELECT finished_at, status FROM runs ORDER BY started_at DESC LIMIT 1")
            if row:
                out["run_at"], out["run_status"] = row[0]
    except Exception:  # kho hỏng hoặc đang được thay: thanh bên báo không đọc được, không làm treo trang
        out["error"] = True
    days = sorted((root / "ticks").glob("date=*"))
    if days:
        files = list(days[-1].glob("topic=*/*.parquet"))
        if files:
            import duckdb
            con = duckdb.connect(":memory:")
            try:
                paths = ", ".join("'" + f.as_posix().replace("'", "''") + "'" for f in files)
                out["tick_at"] = con.execute(f"SELECT max(received_at) FROM read_parquet([{paths}], "
                                             "union_by_name = true)").fetchone()[0]
            except duckdb.Error:
                pass
            finally:
                con.close()
    return out


def live_prices(symbols: list[str]) -> dict:
    """Giá khớp gần nhất hôm nay từ dữ liệu thời gian thực đã ghi, chỉ khi hôm nay mới hơn phiên cuối trong kho.
    Trả {mã: (giá, thời điểm)}; không có dữ liệu thì rỗng. Bỏ dòng trùng và giá không dương (tick_quality)."""
    from datetime import datetime
    from ..config import VN_TZ
    from ..tick_quality import clean_trades
    today = datetime.now(VN_TZ).date()
    session = last_session()
    if not symbols or (session is not None and today <= session):
        return {}
    trades = clean_trades(lake()[0], today)
    if trades.empty:
        return {}
    trades = trades[trades["symbol"].isin(symbols)]
    last = trades.sort_values("time").groupby("symbol").tail(1)
    return {r.symbol: (float(r.price), r.time) for r in last.itertuples()}
