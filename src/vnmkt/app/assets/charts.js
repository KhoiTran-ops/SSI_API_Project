// Biểu đồ đường (layout.meta.yfit do theme.pannable gắn): khi kéo, thu phóng hoặc ẩn/hiện đường,
// trục y co giãn theo các điểm đang thấy cùng các mốc luôn hiện, đúng công thức theme.fit_range.
// Biểu đồ cột không có meta này và đã khóa trục ở phía Python.
(function () {
  var PAD = 0.08;

  // Nhãn nút tiếng Việt. Plotly nạp sau tệp này thì tự đăng ký các mục trong window.PlotlyLocales.
  var locale = {
    moduleType: "locale", name: "vi",
    dictionary: {
      "Pan": "Kéo", "Zoom": "Phóng to vùng chọn", "Reset axes": "Về mặc định",
      "Double-click to zoom back out": "Nhấp đúp để về mặc định"
    }
  };
  if (window.Plotly) window.Plotly.register(locale);
  else (window.PlotlyLocales = window.PlotlyLocales || []).push(locale);

  // Khoảng y cho khung nhìn x = [x0, x1] (đơn vị của trục x); null nếu không cần đổi.
  function fitted(gd, x0, x1) {
    var meta = gd.layout && gd.layout.meta && gd.layout.meta.yfit;
    var full = gd._fullLayout;
    if (!meta || !full || !full.xaxis || !full.yaxis) return null;
    var xa = full.xaxis;
    var a = xa.r2c(x0), b = xa.r2c(x1);
    var lo = Math.min(a, b), hi = Math.max(a, b);
    var vals = (meta.include || []).slice();
    (gd._fullData || []).forEach(function (tr) {
      if (tr.visible !== true || !tr.x || !tr.y) return;
      for (var i = 0; i < tr.x.length; i++) {
        var x = xa.d2c(tr.x[i]), y = tr.y[i];
        if (x >= lo && x <= hi && typeof y === "number" && isFinite(y)) vals.push(y);
      }
    });
    if (!vals.length) return null;
    var ymin = Math.min.apply(null, vals), ymax = Math.max.apply(null, vals);
    var span = (ymax - ymin) || Math.abs(ymax) * 0.1 || 1;
    var next = [ymin - span * PAD, ymax + span * PAD], cur = full.yaxis.range;
    if (Math.abs(cur[0] - next[0]) < 1e-9 && Math.abs(cur[1] - next[1]) < 1e-9) return null;
    return next;
  }

  // Đổi trục sau khi Plotly xử lý xong sự kiện hiện tại (gọi relayout lồng trong sự kiện có thể bị bỏ qua).
  function apply(gd, x0, x1, setX) {
    setTimeout(function () {
      var y = fitted(gd, x0, x1), upd = {};
      if (setX) upd["xaxis.range"] = [x0, x1];
      if (y) upd["yaxis.range"] = y;
      if (Object.keys(upd).length) window.Plotly.relayout(gd, upd);
    }, 0);
  }

  function bind(gd) {
    if (!gd.on || gd._vnmktEv === gd._ev) return;  // Plotly.purge tạo bộ sự kiện mới: gắn lại
    gd._vnmktEv = gd._ev;
    gd.on("plotly_relayout", function (ev) {
      ev = ev || {};
      var xa = gd._fullLayout && gd._fullLayout.xaxis;
      if (!xa || !gd.layout.meta || !gd.layout.meta.yfit) return;
      if (ev["xaxis.autorange"] && xa.minallowed !== undefined) {  // về đúng khoảng mặc định
        apply(gd, xa.minallowed, xa.maxallowed, true);
      } else if (Object.keys(ev).some(function (k) { return k.indexOf("xaxis") === 0; })) {
        apply(gd, xa.range[0], xa.range[1], false);
      }
    });
    gd.on("plotly_restyle", function () {
      var xa = gd._fullLayout && gd._fullLayout.xaxis;
      if (xa && gd.layout.meta && gd.layout.meta.yfit) apply(gd, xa.range[0], xa.range[1], false);
    });
  }

  var queued = false;
  function scan() {
    queued = false;
    document.querySelectorAll(".js-plotly-plot").forEach(bind);
  }
  new MutationObserver(function () {
    if (!queued) { queued = true; requestAnimationFrame(scan); }
  }).observe(document.documentElement, {childList: true, subtree: true});
})();
