# Thiết kế: bộ kiểm soát rủi ro cá nhân cho tài khoản chứng khoán

Bản 22/11/2026. Tên sản phẩm chưa đặt; gói Python tạm tên `vnmkt`.

> Công cụ phân tích học thuật, không phải khuyến nghị đầu tư.

## 1. Vấn đề và phạm vi

Nhà đầu tư cá nhân dùng margin thường chỉ thấy một con số tỷ lệ ký quỹ ở thời điểm hiện tại. Họ không biết danh mục chịu được bao nhiêu phiên giảm sàn, và khi bị bán giải chấp thì bán được bao nhiêu mỗi phiên. Công cụ trả lời các câu hỏi đó bằng **kiểm tra sức chịu đựng** trên danh mục của chính người dùng, cộng với đo lường rủi ro và hiệu suất.

**Không làm:** dự báo giá, phát hiện thao túng, khuyến nghị mua bán, đặt lệnh.

**Nguồn danh mục** (chốt 22/11/2026):

| Nguồn | Khi nào dùng | Ghi chú |
|---|---|---|
| Nhập tay | Mặc định | Mã, khối lượng, giá vốn, tiền mặt, dư nợ, ngưỡng ký quỹ. Lưu trong `data/` (Git bỏ qua). |
| Mô phỏng | Dữ liệu mẫu cho người chấm | Danh mục giả, giá thật từ kho. Ghi rõ "mô phỏng" trên mọi màn hình. |
| Tài khoản SSI | Nguồn chính (`sync-account`, nút "Đồng bộ từ SSI") | Chỉ đọc. Danh mục, ký quỹ và lệnh đã khớp; nạp/rút và phí từng lệnh API không có nên người dùng tự ghi. Đã chạy thật 25/11/2026 (mục 3.2). |

Cả ba nguồn quy về cùng một cấu trúc `Portfolio` (mục 4), nên các phần tính toán không phụ thuộc nguồn.

## 2. Kiến trúc

```
SSI FastConnect V3 ──(chỉ đọc, 2 lượt/giây)──▶ ssi.MarketData ──▶ RawLog (data/raw, JSON gốc)
                                                     │
                                                     ▼
                                          store.Warehouse (DuckDB)
                                                     │
 Nhập tay / mô phỏng / tài khoản SSI ──▶ Portfolio ──┤
                                                     ▼
                     risk/: stress (giảm sàn) · whatif (thử lệnh) · euler · performance · rules
                                                     │
                                                     ▼
                             explain (mẫu câu từ số liệu) ──▶ giao diện Dash
```

- **Một tiến trình, một token** cho cả REST và WebSocket, vì mỗi khóa chỉ có một phiên đăng nhập. Token hết hạn thì đăng nhập lại bằng khóa; không dùng refresh token.
- `READ_ONLY_PATHS` trong `ssi.py` chặn mọi endpoint không có trong danh sách. Kiểm thử khẳng định các đường dẫn đặt, sửa, hủy lệnh bị chặn.
- Mọi phép tính đều là **hàm thuần** nhận `Portfolio` và bảng giá, không gọi mạng. Nhờ vậy kiểm thử chạy được trên `data_sample/`.

## 3. Dữ liệu

### 3.1 Kho (đã có)

Lưu bằng **Parquet** (nén zstd) trong `data/lake/`, truy vấn bằng DuckDB trong bộ nhớ (`store.Warehouse`). Mỗi bảng một thư mục; `daily` chia theo năm (`daily/year=YYYY/`). Dữ liệu thời gian thực ghi theo lô vào `ticks/date=YYYY-MM-DD/topic=<topic>/` và gộp lại cuối ngày (`store.TickWriter`, `store.compact_ticks`). Chuyển từ DuckDB ngày 22/11/2026: 3,79 triệu dòng `daily`, từ 298 MB còn 119 MB; đối chiếu từng dòng khớp 100%. Ghi an toàn: mỗi bảng được ghi vào thư mục tạm rồi mới thay thư mục cũ; khóa tệp không cho hai tiến trình cùng ghi; lần ghi bị dừng giữa chừng được khôi phục ở lần mở sau.

| Bảng | Nội dung | Dùng cho |
|---|---|---|
| `securities` | 1.517 mã, sàn, ngành ICB, số cổ phiếu niêm yết | biên độ sàn theo sàn; nhóm ngành cho Brinson |
| `daily` | OHLC, khối lượng, giá trị theo ngày, từ 2000 | lợi suất, thanh khoản phiên giảm sàn |
| `index_daily` | VN-Index (2007), VN30 (2012), VN100 (2018) | so sánh hiệu suất, beta |
| `corporate_actions` | 28.403 sự kiện quyền, hệ số `ratio` | điều chỉnh giá và khối lượng |
| `master` | trần, sàn, tham chiếu của phiên cuối | kiểm chứng cách tính giá sàn |

Cần bổ sung `recordDate` và `issueDate` vào `corporate_actions` (có trong phản hồi gốc, bảng chưa lưu).

### 3.2 API tài khoản (phản hồi thật 22/11 và 25/11/2026)

- SDK đã bóc lớp `data`. Cổ phiếu: `{"equity": {...}, "receivedTime"}`; phái sinh: `{"derivative": {...}}`.
- **Chất lượng dữ liệu thời gian thực** (`tick_quality.py`, lệnh `tick-quality`): trùng (cùng mã, thời điểm, giá, khối lượng, tổng khối lượng), sai thứ tự (tổng khối lượng lũy kế giảm), đến muộn (> 5 giây), bất thường (giá không dương, ngoài trần–sàn từ tham chiếu phiên trước, giá mua > giá bán), mất tín hiệu (> 60 giây trong giờ khớp lệnh liên tục). Không sửa dữ liệu gốc; `clean_trades` trả bản đã lọc. Lần ghi 23/11/2026: không phát hiện lỗi.
- **Phiên không giao dịch**: SSI không trả dòng cho phiên mã không có giao dịch (ví dụ PNC: 6/60 phiên gần nhất). Khối lượng bình quân 20 phiên tính theo lịch phiên của VN-Index, phiên thiếu là 0; lợi suất ngày (rủi ro, kịch bản 2022) giữ giá đứng yên ở phiên thiếu; phân rã Brinson dùng giá gần nhất tại hoặc trước ngày đầu/cuối. Riêng thống kê tỷ lệ khớp phiên sàn (`floor_liquidity`) vẫn lấy bình quân theo các phiên có giao dịch, nên với mã ít thanh khoản kết quả thận trọng hơn (ước lượng thấp khả năng bán).
- **Phiên chưa công bố đủ**: `update` chỉ lấy đến phiên đã có VN-Index; mã bị tải lại toàn bộ cũng không vượt quá phiên đó, và bảng `checked` ("đã hỏi SSI tới ngày nào") chỉ ghi đến phiên VN-Index đã có, nên lần chạy sau sẽ hỏi lại phiên mới. Lỗi gặp ngày 23/11/2026: chạy lúc 15:31 khi SSI chưa công bố, `checked` ghi nhầm "đã hỏi tới 23/11" cho mọi mã nên lần chạy sau bỏ qua; đã sửa và có kiểm thử.
- **Giá trong phiên**: trang Danh mục định giá bằng giá khớp gần nhất hôm nay trong `ticks` (đã bỏ trùng, giá không dương) khi hôm nay mới hơn phiên cuối trong kho; có nhãn "Giá trong phiên đến hh:mm". Các trang phân tích dùng giá đóng cửa ngày.
- **Giá tham chiếu**: UPCoM là giá bình quân phiên trước, HOSE/HNX là giá đóng cửa (đối chiếu `masterdata` 13/11/2026: UPCoM khớp giá bình quân 99,7%). `market.quotes` trả cột `ref`; kịch bản, thử lệnh và thống kê phiên sàn dùng cột này.
- **WebSocket** (phản hồi thật 23/11/2026, trong giờ giao dịch): token không OTP nhận được dữ liệu; đăng ký trade, quote, room, indexsummary đều được xác nhận. SDK trả `TradeMessage`, `QuoteMessage` với **giá tính bằng đồng** (kể cả quote, khác tài liệu ghi nghìn đồng), `IndexSummaryMessage` giữ dict gốc với giá trị là chuỗi; topic room trả `ForeignRoomMessage` (room còn lại, lũy kế mua/bán của khối ngoại; ghi thật 25/11/2026: 1.904 thông điệp cho 14 mã). `recorder.normalize` theo đúng các dạng này; mẫu thật lưu ở `tests/fixtures/ws_messages.json`. `python main.py record` ghi vào `ticks/date=/topic=/` (trade, quote, index, room).
- `ppmmrAccount` trả 74 trường, mọi giá trị đều là chuỗi. Có các ngưỡng `callLMV`, `forceLMV`, `callMargin`, `callForcesell`. Đây là **giá trị thị trường** tại đó bị gọi ký quỹ hoặc bán giải chấp, không phải tỷ lệ. Khi tài khoản không có dư nợ, các trường này bằng 0. Khi D > 0 có thể suy tỷ lệ R_call = 1 − D/callLMV (chưa kiểm chứng).
- `orderBook`: tham số `from`, `to` dạng `YYYY/MM/DD`, `pageIndex`, `pageSize`. Phản hồi có `accountNo`, `orderList`, `totalRecord`. **Khoảng ngày dài hơn 365 ngày trả 0 lệnh mà không báo lỗi** (thử 366, 400, 500 ngày và từ năm 2000), nên `account.fetch_orders` đọc từng cửa sổ 180 ngày từ 2015, theo trang 100 lệnh, bỏ trùng theo `orderId`. Mỗi lệnh: `side` B/S, `orderStatus` FF (khớp hết), FFPC (khớp một phần rồi hủy), EX (hết hiệu lực), CL (hủy), `filledQty`, `avgPrice` (đồng), `inputTime`, `modifiedTime`. Có lệnh lô lẻ.
- **Quy đổi** (`account.py`): `position` → `Holding` (khối lượng = `quantity` + `t1BuyQuantity` + `t2BuyQuantity`, giá vốn `costPrice`); `ppmmrAccount` → tiền mặt = `accountBalance`, dư nợ = `liability` (gồm phí còn nợ), ngưỡng gọi/bán giải chấp = 1 − D/`callLMV`, 1 − D/`forceLMV` khi có dư nợ (không thì giữ ngưỡng đang dùng); lệnh có `filledQty` > 0 → giao dịch mua/bán theo `avgPrice`, ngày khớp, thuế bán 0,1%. Nhật ký giữ các dòng nạp/rút tự ghi.
- **Đối chiếu khi đồng bộ** (`account.cross_check`): tài sản ròng = tổng tài sản − nợ; tiền mặt + LMV = tổng tài sản; số cổ phiếu theo `position` = số dựng lại từ lệnh khớp và sự kiện quyền. Chạy thật 25/11/2026 (tiểu khoản Margin): 52 lệnh, 40 khớp từ 29/07/2025 đến 10/11/2026, cả ba phép đối chiếu khớp (28 cổ phiếu CII bán mà không có lệnh mua là cổ tức bằng cổ phiếu). Khuôn phản hồi đã làm sạch: `tests/fixtures/ssi_account.json`.
- **Chưa kiểm chứng** (tài khoản thử không còn mã và không vay): khối lượng `position` khi đang giữ mã (đối chiếu thứ ba sẽ báo lệch nếu công thức sai), cách SSI tính `marginRatio` (tài khoản không nợ trả 1), R_call = 1 − D/callLMV.

## 4. Mô hình danh mục

```
Holding(symbol, quantity, cost_price)
Portfolio(holdings, cash, debt, call_ratio, force_ratio, simulated, label, notes)
```

- Giá tính bằng **đồng**. Dữ liệu giá từ kho và từ topic `trade` (WebSocket) đều theo đồng. Topic `quote`, `put`, `market` theo nghìn đồng, nên phải đổi ngay khi đọc.
- Tài sản tính ký quỹ L = Σ qᵢ·pᵢ (chỉ mã nằm trong danh mục ký quỹ). Tỷ lệ ký quỹ R = (L − D)/L, với D = dư nợ − tiền mặt (không âm).
- **Ngưỡng do người dùng nhập.** Giá trị mặc định lấy theo dải SSI hiển thị trên trang Margin Loan: an toàn > 0,35; cảnh báo 0,25–0,35; bán giải chấp < 0,25 ([ssi.com.vn](https://www.ssi.com.vn/en/individual-customer/margin-loan)). Trang tiếng Việt chỉ ghi "TLKQ duy trì quy định", không ghi con số, và ghi SSI ép bán khi nợ quá hạn ≥ 3 ngày làm việc hoặc tài khoản vi phạm TLKQ duy trì ≥ 3 ngày làm việc liên tục ([ssi.com.vn](https://www.ssi.com.vn/khach-hang-ca-nhan/giao-dich-ky-quy)). Danh mục mã được ký quỹ do SSI đăng dạng PDF, cập nhật nhiều lần mỗi tháng; người dùng tự đối chiếu mã của mình. Khi đọc được tài khoản thật, dùng `callLMV`, `forceLMV`, `marginRatio` của SSI thay cho giá trị mặc định.

## 5. Phương pháp

### 5.1 Kịch bản giảm sàn liên tiếp kèm ràng buộc thanh khoản (lõi số 1)

Đây là kiểm tra sức chịu đựng, không phải dự báo, và không gọi là "tình huống xấu nhất".

**Giá mỗi phiên.** pᵢ,t = sàn(pᵢ,t−1, bᵢ), với biên độ bᵢ = 7% (HOSE), 10% (HNX), 15% (UPCoM): ref × (1 − b), làm tròn lên tới bước giá (HOSE 10/50/100 đồng theo mức giá; HNX, UPCoM 100 đồng). Đối chiếu với giá sàn SSI phiên 13/11/2026: khớp 100% HNX, 99,9% UPCoM (trừ mã có biên độ đặc biệt), 96,8% HOSE; chỗ lệch ở HOSE tối đa 2 bước giá, chỉ khi giá ngay dưới mốc 10.000 hoặc 50.000 đồng (`tests/test_exchange.py`).

**Thanh khoản khi bán giải chấp.** Mỗi phiên chỉ bán được tối đa Vᵢ cổ phiếu ở giá sàn:

- Vᵢ,t = tham gia × m(sàn, vị trí) × sᵢ × khối lượng bình quân 20 phiên gần nhất của mã, làm tròn xuống lô 100.
- m(sàn, vị trí) là trung vị của (khối lượng khớp phiên sàn / bình quân 20 phiên trước) theo vị trí phiên trong chuỗi sàn (1, 2, 3, từ 4), tính từ kho từ năm 2013 (`market.floor_liquidity`). Phiên sàn là phiên có giá đóng cửa bằng giá sàn tính từ giá đóng cửa phiên trước (cùng công thức trên), bỏ phiên giảm quá biên độ thường. Kết quả: HOSE 1,16 → 1,05 → 0,88 → 0,56; HNX 0,69 → 0,72 → 0,59 → 0,21; UPCoM 0,65 → 0,70 → 0,59 → 0,48 (phiên sàn UPCoM tính từ giá tham chiếu là giá bình quân phiên trước; bản đầu dùng giá đóng cửa nên đếm nhầm thành 32.207 phiên và trung vị về 0, đã sửa 23/11/2026). Phiên khóa sàn (giá cao nhất = thấp nhất) ở HOSE chỉ khớp trung vị 0,26 lần bình quân.
- sᵢ là hệ số riêng của mã (trung vị của mã / trung vị của sàn, giới hạn 0,25–4) khi mã có ít nhất 5 phiên sàn; còn lại sᵢ = 1.
- Tỷ lệ tham gia: phần khối lượng khớp mà lệnh bán giải chấp chiếm được, mặc định 10%, người dùng sửa được. Đây là giả định, không có nguồn số liệu.
- Kịch bản đợt giảm 2022 dùng khối lượng khớp thật của từng phiên (so với bình quân 20 phiên trước đó) thay cho m.

**Bán giải chấp.** Bị bán khi R < ngưỡng bán giải chấp, hoặc R < ngưỡng gọi ký quỹ đủ 3 phiên liên tiếp (theo trang Giao dịch ký quỹ của SSI; số phiên là tham số). Cần bán S để đưa R về ngưỡng gọi ký quỹ R\*. Bán giá trị S (thuế phí tỷ lệ c) thì L giảm S, D giảm S(1 − c):

(E − cS)/(L − S) ≥ R\*  ⇒  S ≥ (R\*·L − E)/(R\* − c),  với E = L − D

Bán mã có thanh khoản (Vᵢ × giá) cao trước. Phần không bán được do thiếu thanh khoản chuyển sang phiên sau. Bán xong mà R về lại R\* thì đếm phiên vi phạm bắt đầu lại. Giả định đơn giản hóa: tiền bán trừ ngay vào nợ, không chờ T+2; không nộp thêm tiền; không tính lãi vay phát sinh.

**Kết quả.** Phiên đầu tiên bị gọi ký quỹ; phiên đầu tiên bị bán giải chấp; giá trị bán được và chưa bán được theo phiên; tài sản ròng còn lại.

**Thang mức độ.**

1. Một phiên giảm sàn.
2. Hai đến ba phiên giảm sàn (16,6% chuỗi sàn ở HOSE dài từ 2 phiên, 4,8% dài từ 3 phiên; dài nhất 29 phiên).
3. Đường đi giá thật của đợt giảm năm 2022, áp lên danh mục hiện tại.
4. Kịch bản người dùng tự nhập: giảm x% mỗi phiên trong n phiên, sửa riêng từng mã (bảng có cột nhóm ngành để sửa theo ngành); không giảm quá giá sàn.

### 5.2 Thử lệnh trước khi đặt

Người dùng nhập một lệnh mua hoặc bán (mã, khối lượng, giá; giá gợi ý là giá đóng cửa gần nhất, kèm biên độ phiên kế tiếp). `whatif.evaluate` áp lệnh lên bản sao `Portfolio` (lệnh không được gửi đi, không theo dõi tiếp):

- Mua: tiền trả = giá trị × (1 + phí), lấy từ tiền mặt trước, thiếu thì thành dư nợ ký quỹ; giá vốn bình quân gia quyền gồm phí.
- Bán: tiền nhận = giá trị × (1 − phí − thuế 0,1%), trừ ngay vào dư nợ khi xét tỷ lệ ký quỹ (tiền bán chờ về T+2 vẫn được tính vào tài sản). Không bán quá số đang có.

So sánh trước và sau: giá trị danh mục, dư nợ ròng, tài sản ròng, tỷ lệ ký quỹ, mức giảm giá còn chịu được trước khi bị gọi ký quỹ (L − D/(1 − ngưỡng gọi), tính theo % giá trị danh mục), số phiên giảm sàn liên tiếp tới khi bị gọi ký quỹ (chạy kịch bản 5.1, tối đa 10 phiên), tỷ trọng. Cảnh báo khi giá ngoài biên độ phiên kế tiếp (`exchange.ceiling_price`, đối chiếu giá trần SSI khớp trên 98%), khối lượng lẻ lô, lệnh làm tỷ lệ ký quỹ xuống dưới ngưỡng gọi ký quỹ, cổ phiếu mua về T+2. Người dùng có thể ghi lệnh vào danh mục (sau khi đặt lệnh thật), ô khối lượng được xóa để không ghi trùng. Phần đóng góp rủi ro (5.3) và luật bị vi phạm (5.5) thêm vào khi làm xong các phần đó. Nếu đọc được tài khoản thật thì có thể dùng thêm `trading/maxBuySell` (chỉ đọc) để kiểm tra sức mua.

### 5.3 Phân rã rủi ro theo Euler

σₚ = √(wᵀΣw); RCᵢ = wᵢ·(Σw)ᵢ/σₚ; ΣRCᵢ = σₚ. Mỗi RCᵢ tách thành phần do mã tự biến động, wᵢ²σᵢ²/σₚ, và phần do tương quan với các mã còn lại (RCᵢ trừ phần riêng). Σ ước lượng từ lợi suất ngày (giá đã điều chỉnh theo mọi sự kiện quyền kể cả cổ tức tiền mặt, nên là lợi suất tổng), năm hóa theo 250 phiên, cửa sổ 3 tháng / 6 tháng / 1 năm / 3 năm (mặc định 1 năm); chỉ dùng phiên mọi mã đều có giá, cần ít nhất 60 phiên (`risk.returns`, `risk.euler`, `risk.risk_report`). Kèm beta từng mã và của danh mục với VN-Index, phần phương sai do thị trường βₚ²σₘ²/σₚ², và biến động trên tài sản ròng = σₚ × L/E. Đây là đo lường quá khứ, không phải dự báo. Ví dụ danh mục mẫu (1 năm đến 13/11/2026): σₚ 22,6%, beta 0,86, MWG chiếm 23,9% giá trị nhưng góp 28,5% biến động.

### 5.4 Hiệu suất

- **Đã làm** (`performance.py`, trang Hiệu suất) từ nhật ký giao dịch nhập tay (`data/journal.json`; mẫu mô phỏng `data_sample/journal.json`): mỗi lệnh khớp q cổ giá thật ngày d quy thành q/F(d) đơn vị điều chỉnh, F(d) = tích hệ số `ratio` các sự kiện sau d (một hệ số mỗi ngày: SSI ghi cùng hệ số cho mọi sự kiện cùng ngày). Đối chiếu F với (thay đổi giá điều chỉnh)/(thay đổi giá gốc) của SSI: khớp 100% HPG, VCB, SSI, PVS, FPT, 99,9% MWG (không dùng được cho UPCoM vì tham chiếu là giá bình quân). Nhờ vậy lợi suất là lợi suất tổng, nhật ký không ghi cổ tức. Quyền mua được tính như đã thực hiện theo giá lý thuyết. Số cổ phiếu thật đang giữ chỉ cộng cổ phiếu thưởng/cổ tức bằng cổ phiếu (đọc "tỷ lệ x%" trong tiêu đề sự kiện).
- TWR theo ngày (tiền vào tính đầu phiên, tiền ra tính cuối phiên) so với VN-Index; MWR (IRR năm, giải bằng chia đôi) theo dòng tiền nạp và rút. Nhật ký đọc từ SSI không có nạp/rút: coi tiền mua là nạp vào, tiền bán là rút ra, lợi suất khi đó là của phần cổ phiếu (có ghi chú trên trang). Bán hết mà còn phần lẻ dưới một cổ phiếu (hệ số điều chỉnh gộp cả cổ tức tiền mặt) thì lệnh bán đó gánh toàn bộ giá vốn còn lại; tổng lãi/lỗ đã thực hiện bằng đúng tiền bán ròng trừ tiền mua (kiểm thử và đối chiếu trên 40 lệnh thật).
- Brinson, Hood & Beebower (1986, *Financial Analysts Journal* 42(4)) theo nhóm ngành ICB cấp 2 (2 chữ số đầu mã ICB của SSI, 19 nhóm): phân bổ (wpₛ − wbₛ)Rbₛ + chọn mã wbₛ(Rpₛ − Rbₛ) + tương tác (wpₛ − wbₛ)(Rpₛ − Rbₛ) = Rp − Rb (`risk.attribution`, đã làm). Hiện giả định giữ nguyên khối lượng hiện tại suốt giai đoạn (chưa có lịch sử lệnh). Mốc là **Chỉ số vốn hóa HOSE** do ứng dụng tự tính: mọi cổ phiếu HOSE, trọng số = số cổ phiếu niêm yết hiện tại × giá đầu kỳ (API SSI V3 không có danh sách thành phần hay tỷ trọng của VN-Index). Chỉ số này chỉ dùng làm mốc cho phân rã theo ngành, không phải VN-Index và không đem so với VN-Index.
- PGR/PLR (Odean 1998, *Journal of Finance* 53(5)) từ lịch sử lệnh. Phần này chỉ chạy khi có lệnh, nhập tay hoặc từ `orderBook`.
- Chi phí: phí giao dịch và thuế bán 0,1%. Mức phí là tham số người dùng nhập.
- So với kịch bản "không giao dịch gì": giữ nguyên danh mục đầu kỳ suốt cả giai đoạn.

### 5.5 Bộ luật rủi ro tự đặt

Bốn luật (`rules.py`, lưu ở `data/rules.json`; để trống là tắt): tỷ trọng tối đa một mã; tỷ trọng tối đa một nhóm ngành ICB cấp 2; tỷ lệ ký quỹ tối thiểu (chỉ xét khi có dư nợ); số phiên giảm sàn tối đa để bán hết một mã (khối lượng nắm giữ chia cho Vᵢ ở 5.1, phiên sàn đầu). Mặc định 30% / 50% / 45% / 5 phiên chỉ là điểm bắt đầu để người dùng sửa. Trang Luật rủi ro hiện trạng thái từng luật; trang Thử lệnh báo các luật đang đạt mà lệnh làm vi phạm.

### 5.6 Bối cảnh (đã làm: `context.py`, trang Thị trường)

Tổng quan thị trường kèm tác động lên danh mục, tách theo mô hình chỉ số đơn thành phần thị trường, ngành và riêng mã. Ra-đa khối ngoại cho mã đang giữ. Lời giải thích sinh từ **mẫu câu điền số liệu**, không dùng AI tạo sinh; mỗi con số kèm nguồn (bảng, ngày, công thức).

## 6. Giao diện (Dash)

`python main.py run` mở tại `http://127.0.0.1:8061` (chỉ trên máy này). Đã có bốn trang: **Danh mục** (bảng sửa trực tiếp, tài khoản, tỷ trọng, tỷ lệ ký quỹ kèm thanh ngưỡng), **Kịch bản giảm giá** (chọn kịch bản, câu tóm tắt sinh từ số liệu, bốn chỉ số, biểu đồ tỷ lệ ký quỹ, biểu đồ bán giải chấp, chi tiết theo phiên, giả định), **Thử lệnh** (ô lệnh, câu tóm tắt, bảng trước/sau kèm thay đổi và độ biến động năm, biểu đồ tỷ trọng trước/sau, cảnh báo) và **Rủi ro và hiệu suất** (chọn giai đoạn, bốn chỉ số, biểu đồ tỷ trọng so với đóng góp rủi ro, bảng Euler theo mã, bảng Brinson theo nhóm ngành, phần cách tính). và **Luật rủi ro** (đặt giới hạn, xem trạng thái từng luật).

Thiết kế: nền trung tính, một màu nhấn; bốn màu trạng thái (an toàn, gần ngưỡng, gọi ký quỹ, bán giải chấp) chỉ dùng cho trạng thái và luôn kèm chữ; số dạng Việt Nam (1.234,5) với chữ số đều cột; chế độ sáng và tối theo hệ điều hành; dùng được trên điện thoại. Câu miễn trừ trách nhiệm nằm ở chân trang, một lần mỗi màn hình. Nhãn "Dữ liệu mô phỏng" chỉ hiện khi danh mục là mô phỏng. Số tài khoản (khi đọc từ SSI) luôn được che.

## 7. Kiểm chứng (kiểm thử tự động)

| Kiểm tra | Cách |
|---|---|
| Giá sàn | Tính từ giá tham chiếu, so với `master.floor` của mọi mã ở phiên cuối |
| Bất biến bán giải chấp | Sau khi bán S, R = R\* (sai số làm tròn); khi thanh khoản thiếu thì R < R\* và phần còn lại chuyển sang phiên sau |
| Euler | ΣRCᵢ = σₚ; tổng phần riêng + phần tương quan = RCᵢ |
| Brinson | Phân bổ + chọn mã + tương tác = lợi suất danh mục − lợi suất chỉ số |
| TWR | Danh mục chỉ gồm chỉ số thì TWR = lợi suất chỉ số |
| Sự kiện quyền | Giá điều chỉnh liên tục qua ngày giao dịch không hưởng quyền |
| Chỉ đọc | Mọi đường dẫn ngoài `READ_ONLY_PATHS` bị chặn |
| Đối chiếu SSI | Mỗi lần đồng bộ: tài sản ròng, tổng tài sản, số cổ phiếu dựng lại từ lệnh so với `position`; giao diện hiện `marginRatio` SSI cạnh R tự tính |
| Đọc lệnh | Giả lập orderBook trả 0 khi khoảng > 365 ngày: đọc đủ lệnh, đủ trang, bỏ trùng |
| Thời gian thực | Trùng, sai thứ tự, đến muộn (trừ độ lệch đồng hồ máy), bất thường, mất tín hiệu; tệp tick ghi nguyên tử |

## 8. Giới hạn

- Không có hạn đăng ký và nộp tiền quyền mua (API không cung cấp).
- Không xem được khớp lệnh của các phiên đã qua; chỉ có dữ liệu từ khi bắt đầu ghi luồng. Lệnh khớp lúc mở lại phiên chiều và kết quả ATC (14:45) đến chậm 10–26 giây theo cách sàn công bố.
- API không có lịch sử nạp/rút tiền và phí từng lệnh. Mỗi khóa chỉ một phiên đăng nhập: giao diện không cho đồng bộ khi đang ghi thời gian thực hoặc đang cập nhật kho.
- Không có các mã đã hủy niêm yết (sai lệch sống sót).
- Ngưỡng ký quỹ mặc định là dải hiển thị chung của SSI. Mỗi mã có thể có tỷ lệ riêng, nên cần nhập theo hợp đồng của người dùng.
- Mô hình thanh khoản dựa trên lịch sử. Thị trường có thể kém thanh khoản hơn mức đã từng xảy ra.

## Tài liệu tham khảo

- Brinson, G. P., Hood, L. R., & Beebower, G. L. (1986). Determinants of portfolio performance. *Financial Analysts Journal*, 42(4), 39–44.
- Odean, T. (1998). Are investors reluctant to realize their losses? *Journal of Finance*, 53(5), 1775–1798.
- SSI, trang Margin Loan và Giao dịch ký quỹ (truy cập 22/11/2026).
