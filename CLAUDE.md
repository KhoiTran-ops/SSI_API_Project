# CLAUDE.md — bối cảnh dự án (đọc trước khi làm)

Cập nhật: 22/11/2026. Trả lời người dùng bằng **tiếng Việt**.

## 1. Đồ án

- Đồ án cá nhân cuối kỳ "Gói phần mềm ứng dụng cho tài chính 1" (UEL). Đề: `F:\Exia\261BFF401101_261BFF401102_GPMUD1_DETHICUOIKY_DOAN.docx`.
- Người dùng đã quyết định: dùng **SSI FastConnect V3**, bỏ hẳn NEO API. Chỗ nào đề ghi "NEO API" thì hiểu là SSI.
- Tiêu chí chấm (100 điểm): vấn đề và giá trị 15; khai thác API và kỹ thuật dữ liệu 20; phương pháp tài chính 20; chất lượng kỹ thuật 20; sản phẩm và trải nghiệm 10; kiểm thử và kiểm chứng 10; tài liệu 5. Lộ khóa thì bị trừ nặng. Phát sinh lệnh thật là vi phạm.

## 2. Ý tưởng đã chốt (sau nhiều vòng loại bỏ)

**Bộ kiểm soát rủi ro cá nhân cho tài khoản SSI.** Ứng dụng đọc tài khoản SSI của chính người dùng (chỉ đọc, không đặt lệnh), ghép với dữ liệu thị trường. Phát triển từ gợi ý số 8, 9, 7, 5, 14 của đề. **Không dự báo, không phát hiện thao túng, không khuyến nghị mua bán.** Người dùng đã bác bỏ mọi hướng dự báo hoặc phát hiện.

Lõi, theo thứ tự ưu tiên:

1. **Kịch bản giảm sàn liên tiếp kèm ràng buộc thanh khoản** cho tài khoản margin thật. Đây là kiểm tra sức chịu đựng, không phải dự báo. Tỷ lệ ký quỹ R = (L − D)/L. Mỗi mã giảm theo biên độ sàn của nó: HOSE 7%, HNX 10%, UPCoM 15%. Trả lời: phiên nào bị gọi ký quỹ, phiên nào bị bán giải chấp. Khi bán giải chấp ở giá sàn, mỗi phiên chỉ khớp được một phần, tính theo khối lượng khớp lịch sử của mã đó trong các phiên giảm sàn. Các ngưỡng lấy từ API `ppmmrAccount` (`marginRatio`, `callLMV`, `forceLMV`, `callMargin`, `callForcesell`), **không tự giả định**. Trình bày theo thang mức độ: 1 phiên sàn; 2–3 phiên; đường đi giá lịch sử của đợt giảm 2022; kịch bản người dùng tự nhập. Không gọi đây là "tình huống xấu nhất". Số liệu đã tính trên HOSE 2013–2026 từ kho: 26.789 phiên mã giảm sàn; khoảng 21% bị khóa sàn cả phiên (giá cao nhất = thấp nhất), những phiên đó chỉ khớp khoảng 24% giá trị so với bình quân 20 phiên; 16,6% chuỗi sàn dài từ 2 phiên, 4,8% dài từ 3 phiên; dài nhất 29 phiên.
2. **Thử lệnh trước khi đặt** (what-if một lệnh, không khớp, không theo dõi tiếp): lệnh đó làm thay đổi tỷ trọng, phần rủi ro gánh, khoảng cách tới ngưỡng gọi ký quỹ và tiền T+2 thế nào. Có thể dùng `trading/maxBuySell` (chỉ đọc).
3. **Phân rã rủi ro theo Euler**: RC_i = w_i·∂σ_p/∂w_i, kèm tách phần rủi ro do mã tự biến động và phần do tương quan với cả danh mục. Hiệp phương sai ước lượng từ lợi suất ngày quá khứ; ghi rõ là đo lường, không phải dự báo.
4. **Hiệu suất**: TWR so với VN-Index, IRR/MWR; phân rã Brinson (Brinson, Hood & Beebower, FAJ 1986) theo ngành ICB thành phần phân bổ và phần chọn mã; PGR/PLR (Odean, Journal of Finance 1998) từ lịch sử lệnh; chi phí phí và thuế; so với kịch bản "không giao dịch gì" trong cùng giai đoạn.
5. **Bộ luật rủi ro do người dùng tự đặt** (tỷ trọng tối đa mỗi mã/ngành, tỷ lệ ký quỹ tối thiểu, số phiên tối đa để bán hết), kiểm tra liên tục và kiểm tra cả lệnh đang thử.
6. Bối cảnh (làm sau): tổng quan thị trường kèm tác động lên danh mục (tách theo mô hình chỉ số đơn thành phần thị trường, ngành, riêng mã); ra-đa khối ngoại cho mã đang giữ; trợ lý giải thích bằng **mẫu câu sinh từ số liệu, không dùng AI tạo sinh**, mỗi con số đều truy được về nguồn.

Kiểm chứng: đối chiếu số tự tính với số SSI trả về (`marginRatio`, tài sản ròng, số cổ phiếu sau sự kiện quyền). Brinson và Euler phải cộng lại đúng bằng tổng. Viết thành kiểm thử tự động.

## 3. Quy tắc bắt buộc

- Khóa chỉ nằm trong `.env` (Git bỏ qua). Không in, log hay commit khóa hoặc token.
- **Chỉ gọi endpoint đọc.** `src/vnmkt/ssi.py` có `READ_ONLY_PATHS` và hàm `allowed()`. Không được nới danh sách này sang endpoint đặt, sửa hoặc hủy lệnh.
- Dữ liệu tài khoản là dữ liệu cá nhân: chỉ lưu trên máy, không đưa vào Git. Che số tài khoản trong giao diện, báo cáo và video.
- Code dựa trên **phản hồi thật**, không đoán theo tài liệu. Ví dụ: trong tài liệu WebSocket, giá ở topic `trade` tính bằng đồng, còn ở `quote`, `put`, `market` tính bằng nghìn đồng. Dữ liệu mẫu cho người chấm phải là bản làm sạch của phản hồi thật, ghi rõ là mô phỏng.
- Không dùng refresh token. Token hết hạn (khoảng 15 phút) thì đăng nhập lại bằng khóa, không OTP. SSI có thể coi refresh token bị dùng lại là bị lộ. **Mỗi khóa chỉ có một phiên đăng nhập**, nên REST và WebSocket phải dùng chung một tiến trình và một token.
- Giới hạn 2 lượt/giây; chỉ tải phần còn thiếu.
- Không phồng codebase. Code thử nghiệm dùng xong thì xóa. Chia việc nhỏ, làm xong bước nào kiểm tra bước đó rồi báo người dùng.
- Không bịa nguồn. Tài liệu tham khảo phải kiểm tra được, ưu tiên tạp chí Q3 trở lên.
- Mọi màn hình ghi: "Công cụ phân tích học thuật, không phải khuyến nghị đầu tư." Chỉ ghi **một lần ở chân trang**. Người dùng (22/11/2026) yêu cầu đạt các yêu cầu kỹ thuật, bảo mật và của đề nhưng **không rải cảnh báo, ghi chú tuân thủ khắp giao diện**.
- Giao diện phải chỉn chu, đẹp nhưng không màu mè; tối ưu trải nghiệm. Làm xong thì tự review code.
- **Chỉ làm trên máy, không push lên GitHub.** Repo chưa có commit nào.
- Chưa đặt tên sản phẩm và logo. Gói Python tạm tên `vnmkt`; giao diện tạm dùng nhãn "Rủi ro ký quỹ".

## 4. Hiện trạng code (`F:\Exia\SSI_API_Project`)

- `src/vnmkt/config.py`: đọc `.env`, `VN_TZ` cố định UTC+7, `BOARD`, `BOARDS`, `RATE_LIMIT_PER_SECOND=2`, `data_dir()`.
- `src/vnmkt/ssi.py`: lớp `MarketData` bọc `ssi-sdk` 3.3.2 (`rest_client.get`, giữ JSON gốc). Gồm: danh sách trắng endpoint đọc; `read(path, params)`; `relogin()`; thuộc tính `auth` để dùng chung với WebSocket.
- `src/vnmkt/store.py`, `pipeline.py`: tải dữ liệu thị trường ngày (chạy thật ngày 14/11/2026: 8.142 lượt gọi). **Kho là Parquet** trong `data/lake/` (từ 22/11/2026), truy vấn bằng DuckDB trong bộ nhớ. `Warehouse(root)` ở chế độ ghi nạp bảng vào RAM (khoảng 3 giây), đóng thì ghi lại các bảng đã đổi; thay thư mục kiểu nguyên tử, có khóa tệp chống hai tiến trình cùng ghi, tự khôi phục khi lần ghi trước bị dừng giữa chừng. Ở chế độ chỉ đọc chỉ tạo view (0,04 giây). `daily` chia theo năm. `TickWriter` và `compact_ticks` ghi dữ liệu realtime theo lô vào `ticks/date=/topic=/`; chưa nối vào WebSocket. Kho DuckDB cũ đã chuyển sang Parquet (đối chiếu khớp 100%) và đã xóa ngày 22/11/2026, cùng lệnh `migrate` dùng một lần.
- `exchange.py`: biên độ, bước giá, giá sàn, lô 100. `portfolio.py`: `Holding`, `Portfolio`, kiểm tra hợp lệ, lưu/đọc JSON. `market.py`: giá cuối và khối lượng bình quân 20 phiên, thanh khoản phiên sàn theo vị trí trong chuỗi (`floor_liquidity`), cửa sổ đỉnh–đáy 2022, đường đi giá lịch sử. `stress.py`: mô phỏng giảm giá, gọi ký quỹ, bán giải chấp có ràng buộc thanh khoản (lõi số 1, xong; tài khoản đang dưới ngưỡng thì phiên hiện tại tính là ngày vi phạm thứ nhất). `whatif.py`: thử lệnh mua/bán trên bản sao danh mục, so sánh trước/sau (lõi số 2, xong). `rules.py`: bộ luật rủi ro (lõi số 5, xong). `performance.py` + trang Hiệu suất: nhật ký, TWR, IRR, PGR/PLR (lõi 4 xong). `tick_quality.py` + lệnh `tick-quality`. Giá tham chiếu UPCoM = giá bình quân phiên trước (cột `ref` của `market.quotes`). `report/bao-cao.md` (bản thảo báo cáo theo khung đề) và `report/kich-ban-video.md`. `recorder.py` + lệnh `record`: ghi WebSocket vào kho (chạy thật 23/11/2026). `risk.py`: Euler, beta, Brinson theo nhóm ngành ICB cấp 2 so với "Chỉ số vốn hóa HOSE" (chỉ số tự tính từ mọi cổ phiếu HOSE theo số cổ phiếu niêm yết hiện tại; người dùng yêu cầu gọi đúng tên này và **không so với VN-Index**) (lõi 3 và một phần lõi 4, xong; TWR/IRR, PGR/PLR, chi phí còn chờ lịch sử lệnh/dòng tiền). `exchange.ceiling_price` cho biên độ.
- `src/vnmkt/app/`: giao diện Dash + dash-ag-grid + Plotly, lệnh `python main.py run [--port 8061]`, chỉ mở trên 127.0.0.1. Bảy trang: Danh mục (`/`), Thị trường (`/thi-truong`, `context.py`: độ rộng, phân tách thị trường/ngành/riêng mã, ra-đa khối ngoại), Kịch bản giảm giá (`/kich-ban`), Thử lệnh (`/thu-lenh`), Rủi ro (`/rui-ro`), Hiệu suất (`/hieu-suat`), Luật rủi ro (`/luat`). Thanh bên hiện trạng thái dữ liệu (phiên cuối, lần cập nhật, lần ghi thời gian thực). Luật lưu ở `data/rules.json`. `README.md` cho người chấm. Lưu ý Dash 4: `dcc.Input type=number` coi giá trị lệch `step`/`min` là không hợp lệ và gửi `None`, nên ô thập phân để `step="any"`; dropdown dùng `ui.symbol_dropdown` (nhãn tiếng Việt qua `labels`). Danh mục người dùng lưu ở `data/portfolio.json`; danh mục mẫu `data_sample/portfolio.json` có cờ mô phỏng. Không có kho thật thì tự dùng `data_sample/lake`. Xem trước trong khung trình duyệt: cấu hình `app` trong `F:\Exia\.claude\launch.json`. Server không tự tải lại, nên sửa Python xong phải khởi động lại.
- `src/vnmkt/access.py` cùng lệnh `python main.py check-access [--seconds N]`: kiểm tra quyền tài khoản (`account/info`, `position`, `accountBalance`, `ppmmrAccount`), độ sâu `orderBook` từ 7 ngày đến 3 năm, và WebSocket (trade/quote/room/indexsummary). Chỉ in tên trường và số bản ghi, không in giá trị. `orderBook` gửi ngày `YYYY/MM/DD`. Đã thay `token_provider` của `Stream` bằng cơ chế đăng nhập lại.
- Các lệnh khác: `check`, `update [--full]`, `sample` (tạo `data_sample/lake`: HOSE từ 2022 cùng vài mã HNX/UPCoM, 22,7 MB), `rebuild`, `quality`, `run`. Kiểm thử: `python -m pytest -q -p no:dash` (92 kiểm thử qua).
- `docs/thiet-ke.md` (22/11/2026): thiết kế theo mục 2. Gồm mô hình `Portfolio` chung cho 3 nguồn, công thức bán giải chấp S ≥ L − (L−D)/R\*, và bảng kiểm chứng. Ngưỡng ký quỹ mặc định lấy theo dải hiển thị trên trang Margin Loan của SSI (>0,35 an toàn; 0,25–0,35 cảnh báo; <0,25 bán giải chấp), người dùng sửa được. SSI không công bố con số Rtt chung. Danh mục mã được ký quỹ là file PDF trên trang Giao dịch ký quỹ, chưa đọc được nội dung vì máy không có thư viện PDF.

## 5. Dữ liệu đã xác nhận

- Kho `data/lake/` (Parquet; trước đây là `data/warehouse.duckdb`):
  - 1.517 mã của cả 3 sàn, mã nào cũng có ngành ICB và số cổ phiếu niêm yết;
  - `daily` khoảng 3,6 triệu dòng, có từ năm 2000, cập nhật đến 13/11/2026;
  - VN-Index từ 2007, VN30 từ 2012, VN100 từ 2018;
  - 28.403 sự kiện quyền (2017–2026);
  - `board_daily` 252 phiên;
  - `master` (giá trần/sàn/tham chiếu) của phiên cuối.
- Tiêu đề sự kiện quyền theo mẫu tách được: "bằng tiền X đồng/cổ phiếu"; "tỷ lệ x%" (cổ tức cổ phiếu, cổ phiếu thưởng); "tỷ lệ a : b giá P đồng/cp" (quyền mua). Cột `ratio` là hệ số điều chỉnh giá. `recordDate` và `issueDate` có trong phản hồi gốc nhưng bảng chưa lưu, cần bổ sung.
- **Kết quả `check-access --seconds 0` ngày 22/11/2026 (Chủ nhật, chưa thử WebSocket):**
  - Xác thực không OTP được; token 15 phút. Khóa **có quyền đọc tài khoản**: `account/info` trả 3 tiểu khoản Cash, Margin, Derivative.
  - `position`, `accountBalance`, `ppmmrAccount` đều 200, nhưng **cả 3 tiểu khoản đều trống**: 0 mã; tiểu khoản Margin chỉ có `marginRatio`, `action`; tiểu khoản Cash có `accountBalance`, `withdrawable`, `ee*`, `collateralAsset*`.
  - SDK (`rest_client`) đã bóc lớp `data`: phản hồi có dạng `{"equity": {...}, "receivedTime": ...}` hoặc `{"derivative": {...}}`.
  - `orderBook`: ngày phải theo `YYYY/MM/DD` (ISO bị 400). Phản hồi có các khóa `accountNo`, `orderList`, `totalRecord`, `receivedTime`; mọi cửa sổ 7 ngày đến 3 năm đều 0 lệnh, nên **chưa biết lịch sử lùi được bao xa**.
  - Log INFO của httpx in URL kèm số tài khoản: đã hạ xuống WARNING trong `cli.main`.
- `ppmmrAccount` (cổ phiếu) trả đủ **74 trường, mọi giá trị đều là chuỗi** (kể cả số). Có `callLMV`, `forceLMV`, `callMargin`, `callForcesell`, `marginCall`, `marginCallLMVSold`, `lmv`, `lmvMargin`, `debt`, `totalDebt`, `ee50`…`ee90`, `sellT0/T1/T2`, `buyT0/T1/T2`, mỗi trường có bản `...SSI` đi kèm. `doverdue_ssi` có thể là null. Trên tài khoản trống, các trường ngưỡng đều bằng 0. Chỉ `marginRatio`, `action`, `ee*`, `purchasingPower` khác 0. (`filled_fields` của `check-access` bỏ qua giá trị 0, nên lần đầu tưởng thiếu trường.)
- `callLMV`, `forceLMV` là **giá trị thị trường** (đồng) tại đó bị gọi ký quỹ hoặc bán giải chấp, tính theo dư nợ. API không trả tỷ lệ ngưỡng. Khi D > 0 có thể suy ra R_call = 1 − D/callLMV; công thức này **chưa kiểm chứng**.
- **23/11/2026 (đã kiểm chứng):** `orderBook` tham số `from`/`to` đúng như SDK; từ 2000/01/01 đến nay chỉ trả 3 lệnh, đều đặt ngày 23/11/2026 (mua LO, trạng thái `EX`, không khớp), trước đó 0 lệnh, nên API không cho lịch sử lệnh cũ. Trường lệnh: `clientRequestId, orderId, symbol, side, orderType, quantity, price, filledQty, cancelQty, avgPrice, inputTime, modifiedTime, orderStatus, message` (giá trị là chuỗi, giờ dạng `YYYY/MM/DD ...`). Trường `position.equity[]`: `accountNo, symbol, quantity, blockQuantity, dividendQuantity, buyingQuantity, boughtQuantity, sellingQuantity, soldQuantity, t1SellQuantity, t2SellQuantity, t1BuyQuantity, t2BuyQuantity, costPrice, mortgageQuantity, sellableQuantity, restrictedQuantity` (đều là chuỗi; 3 dòng hiện đều bằng 0).

## 6. Không làm được hoặc chưa biết

- **Không làm được:**
  - nhắc hạn đăng ký và nộp tiền quyền mua (API không có hạn này);
  - xem khớp lệnh hoặc sổ lệnh của các phiên đã qua (chỉ có từ khi bắt đầu ghi luồng);
  - tính các mã đã hủy niêm yết (sai lệch sống sót).
- **Chưa biết:**
  - lịch sử lệnh lùi về được bao xa (tài khoản chưa có lệnh nào);
  - người dùng có nạp tiền, mua cổ phiếu hoặc kích hoạt margin để demo không. Hiện tài khoản trống nên phải có danh mục nhập tay hoặc mô phỏng.

## 6b. Trạng thái 23/11/2026 cuối ngày

- Kho dữ liệu đầy đủ đến phiên 23/11/2026 (VN-Index 1.697,14). `data_sample/lake` đã tạo lại từ kho này. 95 kiểm thử qua.
- Biểu đồ: cột khóa trục (`theme.base_figure`); đường (Tỷ lệ ký quỹ, Hiệu suất) kéo/thu phóng trong đúng khoảng dữ liệu qua `theme.pannable`, trục y tự co giãn ở `assets/charts.js`, nhấp đúp hoặc nút về mặc định.
- Đã review toàn bộ `src/vnmkt` và sửa: thanh khoản mã ít giao dịch (khối lượng bình quân theo lịch phiên, kịch bản 2022 giữ đường giá thật, rủi ro/Brinson không còn lỗi khi thiếu phiên), thứ tự giao dịch trong ngày và hệ số điều chỉnh theo phiên ghi nhận, `update` không lấy/không đánh dấu phiên SSI chưa công bố đủ (bảng `checked`).
- Codex đang biên tập chữ giao diện (`src/vnmkt/app/`) và sửa logic Thử lệnh (`whatif.py`, có thể cả `rules.py`, `portfolio.py`). Khi Codex xong: chạy lại kiểm thử, review phần Codex sửa, rà 7 trang trên trình duyệt.
- Báo cáo Word (`report/bao-cao.docx`) và ảnh chụp đang dùng số liệu đến 13/11/2026; người dùng sẽ làm tiếp sau. Script dựng docx nằm ở thư mục nháp của phiên (docx npm), cần đưa vào `report/` nếu muốn giữ.

## 7. Việc tiếp theo

1. Đã chạy `--seconds 0` (22/11/2026, xem mục 5). **Đã chốt: danh mục nhập tay + mô phỏng**, vẫn giữ đường đọc tài khoản SSI thật để dùng số thật và đối chiếu khi tài khoản có danh mục.
2. **WebSocket đã kiểm tra 23/11/2026: chạy được** (xem mục 5 trong docs). Người dùng **chưa mua** cổ phiếu, khi nào mua: Trong giờ giao dịch (sau 9:15) chạy `check-access --seconds 60` (WebSocket), sau khi có lệnh khớp thì chạy `--seconds 0`. Đọc tên trường thật của `position` và `orderBook`, rồi viết bộ chuyển phản hồi `position` + `ppmmrAccount` sang `Portfolio` và đối chiếu R tự tính với `marginRatio`. Nếu có vay margin: kiểm chứng R_call = 1 − D/callLMV.
3. Lưu **khuôn phản hồi thật đã làm sạch** (tên trường, kiểu, định dạng số; số tài khoản và tiền thay bằng số giả) vào thư mục Git bỏ qua, làm cơ sở cho kiểm thử.
4. Dùng dữ liệu `ticks` trên giao diện (giá trong phiên cho danh mục) nếu cần; chạy `update` sau 15:30 để cập nhật kho ngày (kho hiện dừng ở 13/11/2026).
5. Trang Danh mục dùng giá trong phiên từ `ticks` khi hôm nay mới hơn phiên cuối trong kho (`data.live_prices`, có nhãn). Còn lại: ghi `record` đủ ≥ 5 phiên (đề khuyến nghị, ưu tiên 10) để có bằng chứng theo thời gian; chọn tên sản phẩm và logo; chèn ảnh chụp vào báo cáo và quay video.
6. README cho người chấm (cài đặt, chạy bằng `DATA_DIR=data_sample`, các trang), báo cáo đồ án, video demo. Nguồn arXiv 2604.10375 chưa kiểm chứng nên đã bỏ khỏi tài liệu; phần tách riêng/tương quan chỉ là phép tính đại số, không cần dẫn nguồn đó.
