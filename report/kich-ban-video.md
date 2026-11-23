# Kịch bản video demo (6–8 phút)

Quay màn hình ứng dụng chạy bằng `python main.py run`. Không quay tệp `.env`, không quay số tài khoản hay số dư thật.
Khi dùng bộ mẫu, nói rõ danh mục và nhật ký là **mô phỏng**, giá và thanh khoản là dữ liệu SSI thật.

| Thời lượng | Màn hình | Nói gì |
|---|---|---|
| 0:00–0:40 | Trang Danh mục | Vấn đề: tỷ lệ ký quỹ hiện tại không cho biết chịu được bao nhiêu phiên giảm sàn. Người dùng: nhà đầu tư cá nhân dùng ký quỹ. Thanh bên: phiên dữ liệu, lần cập nhật, lần ghi thời gian thực |
| 0:40–1:30 | Danh mục | Sửa khối lượng một mã, đổi dư nợ: tỷ lệ ký quỹ, thanh ngưỡng, cột "phiên bán hết" thay đổi |
| 1:30–3:00 | Kịch bản giảm giá | 3 phiên sàn → 5 phiên sàn (có bán giải chấp, phần không bán được); bảng thanh khoản trong "Giả định"; Đợt giảm 2022 (ngày bị gọi, ngày bị bán); Tự nhập theo mã |
| 3:00–4:00 | Thử lệnh | Mua 20.000 HPG: tỷ lệ ký quỹ, sức chịu giảm, độ biến động, cảnh báo vi phạm luật. Bán MWG: ngược lại |
| 4:00–4:45 | Rủi ro | Đóng góp rủi ro so với tỷ trọng, beta; phân rã theo nhóm ngành so với Chỉ số vốn hóa HOSE |
| 4:45–5:30 | Hiệu suất | Nhật ký mô phỏng; TWR, IRR, so với VN-Index và với không giao dịch; PGR/PLR |
| 5:30–6:00 | Luật rủi ro | Hạ tỷ trọng tối đa xuống 20%: vi phạm |
| 6:00–7:00 | Dòng lệnh | `python -m pytest -q -p no:dash` (78 kiểm thử); `python main.py tick-quality --date 2026-11-23`; `check-access` (chỉ in tên trường, số tài khoản đã che); nhắc danh sách endpoint chỉ đọc |
| 7:00–7:30 | — | Giới hạn: không dự báo, giả định thanh khoản, ngưỡng mặc định; công cụ phân tích học thuật, không phải khuyến nghị đầu tư |
