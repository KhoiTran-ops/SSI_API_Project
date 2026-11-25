# Rủi ro ký quỹ

Công cụ kiểm soát rủi ro cho tài khoản chứng khoán cá nhân dùng ký quỹ, xây trên dữ liệu SSI FastConnect V3. Đồ án môn
Gói phần mềm ứng dụng cho tài chính 1 (UEL). Công cụ chỉ đọc dữ liệu, không đặt lệnh, không dự báo giá.

> Công cụ phân tích học thuật, không phải khuyến nghị đầu tư.

## Chạy nhanh bằng dữ liệu mẫu (không cần khóa SSI)

Cần Python 3.10–3.13. Tạo môi trường ảo riêng để thư viện đúng phiên bản trong `requirements.txt`:

```bash
git clone https://github.com/KhoiTran-ops/SSI_API_Project.git
cd SSI_API_Project
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python main.py run
```

Mở http://127.0.0.1:8061. Khi máy không có kho thật (`data/lake`), ứng dụng tự dùng `data_sample/lake`: dữ liệu thị
trường thật của SSI (mọi cổ phiếu HOSE từ 2022 và vài mã HNX, UPCoM) cùng một **danh mục mô phỏng**
(`data_sample/portfolio.json`) và nhật ký giao dịch **mô phỏng** (`data_sample/journal.json`).

Kiểm thử:

```bash
python -m pytest
```

## Các trang

| Trang | Trả lời câu hỏi |
|---|---|
| Tổng quan | Tỷ lệ ký quỹ, mức giảm còn chịu được trước khi bị gọi ký quỹ, luật đang vi phạm, mã đang giữ, phiên gần nhất, nếu giảm sàn 1 phiên |
| Thị trường | Phiên đã chọn: độ rộng thị trường; danh mục biến động vì thị trường, ngành hay riêng mã; khối ngoại ở mã đang giữ |
| Kịch bản giảm giá | Nếu giảm sàn 1/3/5 phiên, lặp lại đợt giảm 2022 hoặc giảm theo mức tự nhập: phiên nào bị gọi ký quỹ, phiên nào bị bán giải chấp, bán được bao nhiêu khi thiếu thanh khoản |
| Thử lệnh | Một lệnh mua/bán làm tỷ lệ ký quỹ, sức chịu giảm, tỷ trọng, độ biến động thay đổi thế nào; có vi phạm luật tự đặt không |
| Rủi ro | Mỗi mã góp bao nhiêu vào biến động (Euler), beta, phân rã theo nhóm ngành so với Chỉ số vốn hóa HOSE (Brinson) |
| Hiệu suất | TWR, IRR, so với VN-Index và với không giao dịch, phí thuế, PGR/PLR |
| Danh mục và tài khoản | Nhập mã, khối lượng, giá vốn, tiền mặt, dư nợ, ngưỡng ký quỹ |
| Nhật ký giao dịch | Ghi nạp/rút tiền và lệnh đã khớp; cập nhật danh mục từ nhật ký |
| Luật rủi ro | Giới hạn tự đặt: tỷ trọng một mã, một ngành, tỷ lệ ký quỹ tối thiểu, số phiên bán hết |

Phương pháp, công thức, nguồn số liệu và giới hạn: [docs/thiet-ke.md](docs/thiet-ke.md).

## Dùng với tài khoản SSI của bạn

1. Sao chép `.env.example` thành `.env`, điền cặp khóa FastConnect V3 (`SSI_API_KEY`, `SSI_API_SECRET`). Tệp `.env`
   không được đưa vào Git.
2. Tải dữ liệu thị trường vào `data/lake` (lần đầu khoảng 1,5 giờ vì giới hạn 2 lượt/giây; các lần sau chỉ tải phần
   thiếu):

   ```bash
   python main.py update
   ```

3. Kiểm tra quyền của khóa (chỉ in tên trường và số bản ghi, không in số tài khoản hay số dư):

   ```bash
   python main.py check-access --seconds 60
   ```

4. Ghi dữ liệu thời gian thực trong giờ giao dịch (khớp lệnh, ba bước giá, VN-Index) vào `data/lake/ticks`:

   ```bash
   python main.py record --minutes 30
   ```

5. Đồng bộ danh mục, ký quỹ và lệnh đã khớp từ tài khoản (chỉ đọc; cũng có nút "Đồng bộ từ SSI" trên trang Danh mục
   và tài khoản, Nhật ký giao dịch). Lệnh in kết quả đối chiếu với số SSI; nạp/rút tiền API không có nên tự ghi
   trong Nhật ký:

   ```bash
   python main.py sync-account --dry-run
   python main.py sync-account
   ```

Danh mục, luật và dữ liệu tài khoản chỉ lưu trong `data/` trên máy (Git bỏ qua). Vẫn nhập tay được trên giao diện.

## Lệnh dòng lệnh

| Lệnh | Việc |
|---|---|
| `run [--port]` | Mở giao diện (chỉ trên 127.0.0.1) |
| `update [--full]` | Tải hoặc cập nhật dữ liệu thị trường |
| `record [--minutes] [--symbols]` | Ghi dữ liệu thời gian thực |
| `check` / `check-access` | Kiểm tra kết nối, quyền tài khoản, WebSocket |
| `sync-account [--account] [--fee-rate] [--dry-run]` | Đọc danh mục, ký quỹ và lệnh đã khớp từ tài khoản SSI (chỉ đọc), đối chiếu với số SSI, ghi vào `data/` (cũng có nút "Đồng bộ từ SSI" trên giao diện) |
| `tick-quality [--date]` | Kiểm tra chất lượng dữ liệu thời gian thực đã ghi (trùng, sai thứ tự, muộn, bất thường, mất tín hiệu) |
| `quality` | Báo cáo chất lượng dữ liệu |
| `sample` | Tạo lại bộ dữ liệu mẫu `data_sample/lake` từ kho thật |
| `rebuild` | Dựng lại kho từ phản hồi gốc trong `data/raw`, không gọi SSI |

## An toàn

- Chỉ gọi endpoint đọc: `src/vnmkt/ssi.py` có danh sách trắng `READ_ONLY_PATHS`; mọi đường dẫn đặt, sửa, hủy lệnh bị
  chặn trong code và có kiểm thử.
- Không dùng refresh token; token hết hạn thì đăng nhập lại bằng khóa. REST và WebSocket dùng chung một phiên.
- Không in, ghi log hay lưu khóa, token, số tài khoản.

## Cấu trúc

```
main.py                 điểm chạy
src/vnmkt/
  ssi.py, pipeline.py   gọi SSI (chỉ đọc), tải dữ liệu
  store.py              kho Parquet (DuckDB truy vấn), ghi dữ liệu thời gian thực
  recorder.py           WebSocket -> kho
  tick_quality.py       kiểm tra chất lượng dữ liệu thời gian thực
  performance.py        nhật ký giao dịch, TWR, IRR, PGR/PLR
  exchange.py           biên độ, bước giá, giá trần/sàn
  portfolio.py          danh mục
  market.py             truy vấn thị trường, thanh khoản phiên giảm sàn
  stress.py             kịch bản giảm giá, gọi ký quỹ, bán giải chấp
  whatif.py             thử lệnh
  risk.py               Euler, beta, Brinson
  rules.py              luật rủi ro
  context.py            bối cảnh thị trường: độ rộng, phân tách biến động trong phiên, khối ngoại
  app/                  giao diện Dash
tests/                  kiểm thử tự động (fixtures: mẫu phản hồi WebSocket thật)
data_sample/            dữ liệu mẫu cho người chấm
docs/thiet-ke.md        tài liệu thiết kế
```
