"""Kiểm thử lớp kết nối SSI bằng phản hồi giả, không gọi mạng."""

import pytest

from vnmkt import ssi
from vnmkt.ssi import MarketData, SsiError, rows_of


class FakeRest:
    def __init__(self, pages):
        self.pages, self.calls, self.rate_limit = pages, [], {"limit": 100, "remaining": 99}

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        return self.pages[params.get("pageIndex", 1) - 1] if isinstance(self.pages, list) else self.pages


class FakeAuth:
    def __init__(self, rest):
        self.rest_client = rest


def session(pages, hook=None):
    md = MarketData("k", "s", on_response=hook)
    md._auth = FakeAuth(FakeRest(pages))
    return md


def test_rows_shapes():
    assert rows_of([{"a": 1}]) == [{"a": 1}]
    assert rows_of({"data": [{"a": 1}], "pagesCount": 1}) == [{"a": 1}]
    assert rows_of({"code": "204"}) == [] and rows_of({"code": 204}) == []
    assert rows_of({"data": None}) == []
    with pytest.raises(SsiError):
        rows_of("oops")


def test_paging_stops_at_pages_count_and_keeps_raw_values():
    pages = [{"data": [{"symbol": "VCB", "totalBuy": None}], "pagesCount": 2},
             {"data": [{"symbol": "VCB", "totalBuy": "0"}], "pagesCount": 2}]
    seen = []
    md = session(pages, hook=lambda path, params, payload: seen.append(params["pageIndex"]))
    rows = md.summary("2026/01/01", "2026/01/31", symbol="VCB")
    assert [r["totalBuy"] for r in rows] == [None, "0"]  # không đổi null thành 0
    assert seen == [1, 2]
    assert md._auth.rest_client.calls[0][1]["pageSize"] == ssi.PAGE_SIZE


def test_summary_requires_exactly_one_filter():
    md = session([])
    with pytest.raises(ValueError):
        md.summary("2026/01/01", "2026/01/02")
    with pytest.raises(ValueError):
        md.summary("2026/01/01", "2026/01/02", symbol="A", index="B")


def test_corporate_actions_uses_allowed_page_size():
    md = session([{"code": 204}])
    assert md.corporate_actions("2026/11/01", "2026/11/05") == []
    assert md._auth.rest_client.calls[0][1]["pageSize"] == 100


def test_expired_token_is_renewed_by_logging_in_again():
    from ssi_sdk.exceptions import AuthenticationError

    class Auth401(FakeAuth):
        logins, is_token_expired = 0, False

        def authenticate(self):
            self.logins += 1
            self.is_token_expired = False

    class Flaky(FakeRest):
        failed = False

        def get(self, path, params):
            if not self.failed:
                self.failed = True
                raise AuthenticationError("401")
            return super().get(path, params)

    md = MarketData("k", "s")
    md._auth = Auth401(Flaky([[{"symbol": "VCB"}]]))
    assert md.securities("HOSE") == [{"symbol": "VCB"}] and md._auth.logins == 1  # 401 giữa chừng: đăng nhập lại 1 lần
    md._auth.is_token_expired = True
    md.securities("HOSE")
    assert md._auth.logins == 2  # hết hạn trước khi gọi: đăng nhập lại trước
    assert md.calls == 2


def test_sdk_errors_are_wrapped_without_details():
    class Boom(FakeRest):
        def get(self, path, params):
            raise ssi.SSIError("token=secret-value")
    md = MarketData("k", "s")
    md._auth = FakeAuth(Boom([]))
    with pytest.raises(SsiError) as exc:
        md.securities("HOSE")
    assert "secret" not in str(exc.value)


def test_order_endpoints_are_blocked():
    from vnmkt.ssi import allowed
    assert allowed("/api/v3/data/securitiesSummary") and allowed("/api/v3/trading/position")
    for path in ("/api/v3/trading/order", "/api/v3/trading/order/batch", "/api/v3/fco/order",
                 "/api/v3/trading/positionX", "/api/v3/auth/requestOtp"):
        assert not allowed(path)


def test_access_helpers_hide_values():
    from vnmkt.access import filled_fields, mask
    assert mask("003C123456") == "*********6"
    fields = filled_fields({"data": {"equity": [{"symbol": "HPG", "quantity": "100", "costPrice": ""}]}})
    assert fields == ["data.equity.quantity", "data.equity.symbol"]
