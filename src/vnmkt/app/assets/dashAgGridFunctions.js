// Định dạng số kiểu Việt Nam cho bảng AG Grid (phân cách nghìn ".", thập phân ",").
var dagfuncs = (window.dashAgGridFunctions = window.dashAgGridFunctions || {});

function vn(value, digits) {
  if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "–";
  if (!Number.isFinite(Number(value))) return "∞";
  return Number(value).toLocaleString("vi-VN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

dagfuncs.vnNumber = function (params, digits) {
  return vn(params.value, digits || 0);
};

dagfuncs.vnPercent = function (params, digits) {
  if (params.value === null || params.value === undefined) return "–";
  var d = digits === undefined ? 1 : digits;
  var sign = params.value < 0 ? "−" : params.value > 0 && params.colDef.field === "pnl" ? "+" : "";
  return sign + vn(Math.abs(params.value) * 100, d) + "%";
};

dagfuncs.vnMoney = function (params) {
  var v = params.value;
  if (v === null || v === undefined) return "–";
  var a = Math.abs(v);
  if (a >= 1e9) return vn(v / 1e9, 2) + " tỷ";
  if (a >= 1e6) return vn(v / 1e6, a >= 1e8 ? 0 : 1) + " tr";
  return vn(v, 0);
};

dagfuncs.vnSessions = function (params) {
  var v = params.value;
  if (v === null || v === undefined) return "–";
  if (v === "inf" || !Number.isFinite(Number(v))) return "Không bán được";
  return vn(v, 0) + " phiên";
};

// Ngày ISO "2026-01-05" -> "05/01/2026" (giữ giá trị ISO để sắp xếp đúng).
dagfuncs.vnDate = function (params) {
  var v = params.value;
  if (!v) return "–";
  var p = String(v).slice(0, 10).split("-");
  return p.length === 3 ? p[2] + "/" + p[1] + "/" + p[0] : v;
};
