# Báo cáo cuối kỳ: Rủi ro ký quỹ

Công cụ kiểm soát rủi ro tài khoản ký quỹ cho nhà đầu tư cá nhân, xây trên SSI FastConnect V3.

Học phần Gói phần mềm ứng dụng cho tài chính 1, UEL. Bản thảo ngày 23/11/2026 (số liệu chốt theo kho dữ liệu đến phiên
13/11/2026 và lần ghi thời gian thực ngày 23/11/2026).

> Công cụ phân tích học thuật, không phải khuyến nghị đầu tư.

---

## 1. Tóm tắt điều hành

**Vấn đề.** Nhà đầu tư cá nhân dùng ký quỹ (margin) thường chỉ thấy một con số tỷ lệ ký quỹ ở hiện tại. Họ không biết
danh mục chịu được bao nhiêu phiên giảm sàn, và khi bị bán giải chấp thì mỗi phiên bán được bao nhiêu, vì cổ phiếu giảm
sàn thường thiếu người mua.

**Người dùng.** Nhà đầu tư cá nhân có tài khoản ký quỹ tại SSI.

**Giải pháp.** Ứng dụng web chạy trên máy người dùng (Dash), chỉ đọc dữ liệu, gồm bảy trang: Danh mục, Thị trường, Kịch bản
giảm giá, Thử lệnh, Rủi ro, Hiệu suất, Luật rủi ro. Lõi là **kịch bản giảm giá có ràng buộc thanh khoản**: mô phỏng từng phiên,
tính tỷ lệ ký quỹ, xác định phiên bị gọi ký quỹ và phiên bị bán giải chấp, và giới hạn khối lượng bán được mỗi phiên theo
thanh khoản lịch sử của chính các phiên giảm sàn.

**Dữ liệu SSI.** Ba nhóm: dữ liệu thị trường REST (1.517 mã, 3,79 triệu dòng giá ngày từ năm 2000, 28.403 sự kiện quyền,
giá trần/sàn/tham chiếu), dữ liệu tài khoản REST (chỉ đọc), và luồng WebSocket (khớp lệnh, ba bước giá, chỉ số) có lưu
trữ. Kho Parquet, truy vấn bằng DuckDB.

**Phương pháp.** Mô phỏng giảm giá theo biên độ và bước giá từng sàn; thanh khoản phiên sàn theo vị trí trong chuỗi sàn;
quy tắc bán giải chấp của SSI; phân rã rủi ro Euler; beta; phân rã Brinson, Hood & Beebower (1986); TWR, IRR; PGR/PLR
(Odean, 1998).

**Kết quả chính.** Công thức giá sàn khớp 96,8–100% giá sàn SSI công bố; hệ số điều chỉnh tự tính khớp 100% hệ số quan
sát từ dữ liệu SSI ở 6 mã kiểm tra; 78 kiểm thử tự động. Ví dụ danh mục mô phỏng (vay 1 tỷ, tỷ lệ ký quỹ 45,5%): nếu lặp
lại đường đi giá năm 2022, tài khoản bị gọi ký quỹ ngày 12/05/2022 và bị bán giải chấp ngày 16/05/2022.

## 2. Vấn đề và nhu cầu người dùng

Ở Việt Nam, giá cổ phiếu mỗi phiên chỉ được giảm tối đa 7% (HOSE), 10% (HNX), 15% (UPCoM). Khi thị trường giảm mạnh, nhiều
mã giảm sàn liên tiếp và **khóa sàn** (giá cao nhất bằng giá thấp nhất, gần như không có người mua). Số liệu từ kho
(phiên giảm sàn từ năm 2013 có đủ 20 phiên trước đó):

| Sàn | Số phiên sàn | Khối lượng khớp phiên sàn 1 → 2 → 3 → từ 4 (trung vị, so với bình quân 20 phiên trước) | Tỷ lệ khóa sàn phiên 1 |
|---|---|---|---|
| HOSE | 12.959 | 1,16 → 1,05 → 0,88 → 0,56 | 18,5% |
| HNX | 18.063 | 0,69 → 0,72 → 0,59 → 0,21 | 47,5% |
| UPCoM | 4.644 | 0,65 → 0,70 → 0,59 → 0,48 | 51,1% |

Phiên giảm sàn của UPCoM xác định theo giá tham chiếu là giá bình quân phiên trước (quy tắc của sàn, đã đối chiếu dữ
liệu SSI). Phiên khóa sàn ở HOSE chỉ khớp khoảng 26% khối lượng bình quân. Công ty chứng khoán bán giải chấp ở giá sàn nhưng không
bán được hết, phần còn lại dồn sang phiên sau, khi giá đã thấp hơn. Các công cụ hiện có hiển thị tỷ lệ ký quỹ tức thời,
không trả lời được: "giảm thêm bao nhiêu thì bị gọi ký quỹ", "bị bán thì bán được bao nhiêu mỗi phiên", "nếu tôi mua thêm
lệnh này thì sao".

**Giá trị kỳ vọng:** người dùng thấy trước ngưỡng nguy hiểm của chính tài khoản mình, thử lệnh trước khi đặt, và đo được
hiệu suất thật của mình. Công cụ **không** dự báo giá, không phát hiện thao túng, không khuyến nghị mua bán.

## 3. Thiết kế sản phẩm

**Kiến trúc** (theo khung của đề, mỗi lớp là một mô-đun):

```
SSI FastConnect V3
   │  REST (chỉ đọc, 2 lượt/giây)          WebSocket (cùng phiên đăng nhập)
   ▼                                        ▼
ssi.py ─ pipeline.py ─ access.py          recorder.py ─ tick_quality.py      ← Thu thập
   │  data/raw (JSON gốc, jsonl.gz)        │
   ▼                                        ▼
store.py: kho Parquet data/lake (daily theo năm, ticks theo ngày/topic) ← Chuẩn hóa / lưu trữ
   ▼
exchange.py, portfolio.py, market.py                                        ← Logic tài chính cơ sở
   ▼
stress.py, whatif.py, risk.py, performance.py, rules.py                     ← Phân tích / mô hình
   ▼
app/ (Dash, 6 trang)                                                        ← Giao diện → Người dùng
```

**Lựa chọn công nghệ.**
- *Parquet + DuckDB*: dữ liệu thời gian thực tăng nhanh; Parquet nén zstd giảm kho từ 298 MB (DuckDB) xuống 119 MB, đọc theo
  cột, chia thư mục theo năm/ngày. DuckDB truy vấn thẳng tệp Parquet (mở kho chỉ đọc 0,04 giây). Ghi theo kiểu thay thư mục
  nguyên tử, khóa tệp chống hai tiến trình cùng ghi, tự khôi phục khi lần ghi trước bị dừng giữa chừng.
- *Dash + AG Grid + Plotly*: bảng sửa trực tiếp, biểu đồ tương tác, chạy cục bộ trên 127.0.0.1.
- *Giữ JSON gốc* (`data/raw`) để dựng lại kho mà không gọi lại SSI (`python main.py rebuild`).

**Chức năng chính.** Danh mục (nhập tay hoặc mô phỏng; đọc tài khoản SSI khi có danh mục); Thị trường (độ rộng thị
trường, biến động danh mục trong phiên tách theo thị trường/ngành/riêng mã, ra-đa khối ngoại); Kịch bản giảm giá (1/3/5 phiên
sàn, đợt giảm 2022, tự nhập theo mã/ngành); Thử lệnh; Rủi ro (Euler, beta, Brinson); Hiệu suất (nhật ký, TWR, IRR,
PGR/PLR); Luật rủi ro tự đặt.

## 4. Dữ liệu SSI FastConnect

| Nhóm | Endpoint / luồng | Trường chính dùng | Tần suất |
|---|---|---|---|
| Thị trường | `securitiesByBoard` | mã, sàn, ngành ICB, số cổ phiếu niêm yết | mỗi lần cập nhật |
| Thị trường | `securitiesSummary` (mã, chỉ số) | mở/cao/thấp/đóng, bình quân, khối lượng, giá trị, khối ngoại | sau 15:30 mỗi phiên |
| Thị trường | `corporateActions` | ngày không hưởng quyền, hệ số điều chỉnh, tiêu đề (cổ tức, thưởng) | cửa sổ 5 ngày |
| Thị trường | `masterdata`, `indexSummary` | trần/sàn/tham chiếu; độ rộng thị trường | mỗi phiên |
| Tài khoản | `account/info`, `position`, `accountBalance`, `ppmmrAccount`, `orderBook` | tỷ lệ ký quỹ, `callLMV`, `forceLMV`, dư nợ, sức mua, lịch sử lệnh | khi người dùng yêu cầu |
| WebSocket | `trade`, `quote`, `room`, `indexsummary` | giá, khối lượng khớp, ba bước giá, chỉ số | thời gian thực |

**Quy trình.** Lấy theo đoạn ≤ 1.000 ngày (đã kiểm tra: `securitiesSummary` phân trang sai với khoảng dài); chỉ tải phần
còn thiếu; phát hiện SSI điều chỉnh lùi lịch sử (so giá ngày trùng) thì tải lại toàn bộ mã đó. Làm sạch: chuỗi số → số,
ô trống → thiếu (không gán 0), giá không dương → thiếu, dữ liệu đặt lệnh bằng 0 khi có khớp lệnh → thiếu, bỏ trùng theo
(mã, ngày). Mọi thời điểm theo giờ Việt Nam (UTC+7).

**Phát hiện từ phản hồi thật** (không theo tài liệu): ngày `orderBook` phải dạng `YYYY/MM/DD`; SDK đã bóc lớp `data`;
`ppmmrAccount` trả 74 trường đều là chuỗi; giá ở topic `quote` trả về tính bằng đồng (tài liệu ghi nghìn đồng); giá tham
chiếu UPCoM là giá bình quân phiên trước (đối chiếu `masterdata`: khớp 99,7%), HOSE/HNX là giá đóng cửa; nhiều sự kiện
quyền cùng ngày dùng chung một hệ số (907 trường hợp).

**Thời gian thực.** Lần ghi thật 23/11/2026 14:14–14:19: 1.631 thông điệp (462 khớp lệnh, 1.109 ba bước giá, 60 chỉ số),
kiểm tra chất lượng: 0 trùng, 0 sai thứ tự, 0 đến muộn, 0 bất thường, 0 khoảng mất tín hiệu.

Từ điển dữ liệu chi tiết: Phụ lục A.

## 5. Phương pháp tài chính

**5.1 Tỷ lệ ký quỹ.** R = (L − D)/L, L là giá trị thị trường danh mục, D là dư nợ trừ tiền mặt. Ngưỡng do người dùng nhập,
mặc định theo dải SSI hiển thị trên trang Margin Loan (an toàn trên 35%, bán giải chấp dưới 25%).

**5.2 Kịch bản giảm giá có ràng buộc thanh khoản.** Mỗi phiên: giá mới theo kịch bản (giá sàn = tham chiếu × (1 − biên độ),
làm tròn lên tới bước giá); nếu R dưới ngưỡng bán giải chấp, hoặc dưới ngưỡng gọi ký quỹ đủ 3 phiên liên tiếp (quy tắc
SSI công bố), thì bán giá trị S để đưa R về ngưỡng gọi ký quỹ R\*: S ≥ (R\*·L − E)/(R\* − c), c là thuế phí. Mỗi mã chỉ bán
được tối đa *tham gia × m(sàn, vị trí phiên sàn) × hệ số riêng của mã × khối lượng bình quân 20 phiên*, làm tròn lô 100;
phần chưa bán dồn sang phiên sau. Tham gia mặc định 10% (giả định, người dùng sửa được). Kịch bản 2022 dùng lợi suất và
khối lượng thật từng phiên từ đỉnh đến đáy của VN-Index năm 2022.

**5.3 Thử lệnh.** Áp lệnh lên bản sao danh mục (mua: dùng tiền mặt trước, thiếu thì thành dư nợ; bán: trừ thuế 0,1% và
phí, trả nợ). So sánh trước/sau: R, mức giảm giá còn chịu được L − D/(1 − R_gọi), số phiên sàn tới khi bị gọi ký quỹ,
tỷ trọng, độ biến động năm, luật bị vi phạm.

**5.4 Rủi ro.** Độ biến động năm từ hiệp phương sai lợi suất ngày (250 phiên). Đóng góp Euler RCᵢ = wᵢ(Σw)ᵢ/σp, ΣRCᵢ = σp,
tách phần riêng wᵢ²σᵢ²/σp và phần tương quan. Beta với VN-Index; phần phương sai do thị trường. Phân rã Brinson theo 19
nhóm ngành ICB cấp 2 so với *Chỉ số vốn hóa HOSE* (chỉ số ứng dụng tự tính từ mọi cổ phiếu HOSE theo số cổ phiếu niêm
yết hiện tại; API không có thành phần VN-Index; không đem so với VN-Index).

**5.5 Hiệu suất.** Giá trong kho đã điều chỉnh theo mọi sự kiện quyền. Một lệnh khớp q cổ giá thật ngày d quy thành
q/F(d) đơn vị, F(d) = tích hệ số điều chỉnh các sự kiện sau d, nên lợi suất là lợi suất tổng (gồm cổ tức, cổ phiếu
thưởng) mà không ghi trùng cổ tức. TWR: rₜ = Vₜ/(Vₜ₋₁ + dòng tiền) − 1; IRR năm giải bằng chia đôi; so với VN-Index và với
"không giao dịch gì thêm". PGR/PLR theo Odean (1998).

**5.6 Bối cảnh thị trường.** Lợi suất mỗi mã trong phiên = βᵢ·r_m (beta 250 phiên trước) + (lợi suất nhóm ngành − β
ngành·r_m) + phần riêng; ba phần cộng đúng bằng lợi suất. Khối ngoại: mua ròng 5 và 20 phiên, z của tổng 5 phiên so với
120 phiên trước (đánh dấu khi |z| > 2), tỷ lệ room đã dùng; phiên thiếu số liệu bỏ qua, không coi là 0.

**5.7 Luật rủi ro.** Tỷ trọng tối đa một mã, một nhóm ngành, tỷ lệ ký quỹ tối thiểu, số phiên sàn tối đa để bán hết. Mặc
định chỉ là điểm bắt đầu; người dùng tự đặt theo khẩu vị.

## 6. Triển khai sản phẩm

Chạy `python main.py run`, mở http://127.0.0.1:8061. Không có kho thật thì tự dùng bộ mẫu `data_sample` (dữ liệu SSI thật
và danh mục, nhật ký **mô phỏng**, có nhãn trên giao diện).

*Ảnh chụp màn hình cần chèn khi xuất bản cuối:* Danh mục; Thị trường; Kịch bản giảm giá (5 phiên sàn, có bán giải chấp);
Kịch bản 2022; Thử lệnh (mua 20.000 HPG, có cảnh báo luật); Rủi ro; Hiệu suất; Luật rủi ro.

**Trạng thái dữ liệu và lỗi.** Thanh bên hiện phiên dữ liệu cuối, kết quả lần cập nhật cuối (đủ/thiếu/lỗi và thời điểm),
lần ghi thời gian thực cuối, và báo khi không đọc được kho. Danh mục trống, mã không có giá, ô nhập sai (kể cả giá trị
ngoài khoảng mà Dash 4 gửi thành rỗng), giai đoạn không đủ dữ liệu đều có thông báo bằng lời, không để trang lỗi.

**Thiết kế.** Nền trung tính, một màu nhấn; bốn màu trạng thái chỉ dùng cho trạng thái ký quỹ và luôn kèm chữ; số định dạng
Việt Nam; sáng/tối theo hệ điều hành; dùng được trên điện thoại. Câu tóm tắt mỗi trang sinh từ số liệu bằng mẫu câu (không
dùng mô hình ngôn ngữ), mỗi con số có trong bảng chi tiết.

## 7. Kiểm thử và kết quả

78 kiểm thử tự động (`python -m pytest -q -p no:dash`), gồm:

| Nhóm | Kiểm chứng |
|---|---|
| Đối chiếu SSI | Giá sàn tự tính so với `masterdata`: HNX 100%, UPCoM 99,9%, HOSE 96,8% (lệch tối đa 2 bước giá sát mốc 10.000/50.000); giá trần HOSE 100%. Hệ số điều chỉnh tự tính so với hệ số quan sát từ cột thay đổi giá gốc: 100% ở HPG, VCB, SSI, PVS, FPT; 99,9% ở MWG |
| Bất biến tài chính | Bán giải chấp đưa R về đúng ngưỡng; thiếu thanh khoản thì còn phần chưa bán; tài sản ròng giảm đúng bằng lỗ giá cộng thuế phí; ΣRCᵢ = σp; Brinson cộng đúng bằng chênh lệch; TWR không đổi khi nạp thêm tiền ở giá đóng cửa; IRR 10% cho trường hợp biết trước |
| Kho dữ liệu | Chuyển DuckDB → Parquet khớp 100% từng dòng; ghi lại, upsert, thay lịch sử mã; khóa ghi; khôi phục sau gián đoạn |
| Thời gian thực | Chuẩn hóa từ mẫu phản hồi thật; phát hiện trùng, sai thứ tự, đến muộn, ngoài biên độ, giá mua > giá bán, mất tín hiệu |
| An toàn | Mọi đường dẫn ngoài danh sách chỉ đọc bị chặn; tên topic không thoát được thư mục; danh mục/luật/nhật ký nhập sai bị từ chối |

Kiểm tra giao diện bằng trình duyệt (thêm/sửa/xóa mã, lưu, các kịch bản, thử lệnh mua/bán và ghi vào danh mục, sửa luật,
dữ liệu sai). Lỗi tìm ra khi kiểm tra và đã sửa: ô số Dash 4 coi 1.000 là không hợp lệ khi đặt bước 100 từ 1; trạng thái
phiên 0 khi tài khoản đã dưới ngưỡng; bộ đếm vi phạm không đặt lại sau bán giải chấp; hệ số sự kiện cùng ngày bị nhân hai
lần; giá tham chiếu UPCoM.

## 8. Hạn chế và sử dụng có trách nhiệm

- Kịch bản là kiểm tra sức chịu đựng, **không phải dự báo** và không phải "tình huống xấu nhất"; thanh khoản tương lai có
  thể kém hơn lịch sử. Tỷ lệ tham gia khớp lệnh là giả định.
- Bán giải chấp giả định tiền bán trừ ngay vào nợ (không chờ T+2), không nộp thêm tiền, không tính lãi phát sinh.
- Ngưỡng ký quỹ mặc định là dải hiển thị chung của SSI; mỗi mã và mỗi hợp đồng có thể khác.
- Chỉ số vốn hóa HOSE dùng số cổ phiếu niêm yết hiện tại, không có free-float.
- Hiệu suất coi quyền mua như đã thực hiện theo giá lý thuyết (do giá điều chỉnh); không có mã đã hủy niêm yết.
- Chưa kiểm chứng được tên trường `position` và `callLMV` khi tài khoản có danh mục (tài khoản dùng để kiểm tra đang trống).
- Dữ liệu tài khoản chỉ lưu trên máy; số tài khoản luôn được che; khóa chỉ nằm trong `.env`; không gọi endpoint đặt, sửa,
  hủy lệnh. Không dùng công cụ để quyết định mua bán thay người dùng.

## 9. Hướng phát triển

Đọc danh mục và lịch sử lệnh thật từ SSI (`position`, `orderBook`) để thay nhập tay và đối chiếu `marginRatio`; dùng giá
trong phiên từ luồng thời gian thực cho trang Danh mục; danh sách mã được ký quỹ và tỷ lệ riêng từng mã; lãi vay ký quỹ
trong kịch bản; ra-đa khối ngoại cho mã đang giữ.

## 10. Kết luận

Sản phẩm biến dữ liệu SSI thành câu trả lời cụ thể cho rủi ro lớn nhất của nhà đầu tư dùng ký quỹ: khi nào bị gọi ký quỹ,
khi nào bị bán, bán được bao nhiêu. Bài học kỹ thuật lớn nhất là phải dựa trên phản hồi thật: năm chỗ phản hồi SSI khác
tài liệu hoặc khác giả định ban đầu đều được phát hiện nhờ đối chiếu số liệu, và đều có kiểm thử giữ lại.

## Tài liệu tham khảo

- Brinson, G. P., Hood, L. R., & Beebower, G. L. (1986). Determinants of portfolio performance. *Financial Analysts
  Journal*, 42(4), 39–44.
- Odean, T. (1998). Are investors reluctant to realize their losses? *Journal of Finance*, 53(5), 1775–1798.
- SSI. Margin Loan; Giao dịch ký quỹ (ssi.com.vn, truy cập 22/11/2026).
- SSI FastConnect API (developers.ssi.com.vn); thư viện ssi-sdk 3.3.2.

## Phụ lục A. Từ điển dữ liệu (bảng chính)

| Bảng (data/lake) | Khóa | Cột chính |
|---|---|---|
| `securities` | symbol | name, board, icb_code, icb_name, listed_share, stock_type |
| `daily` (chia theo năm) | symbol, date | open, high, low, close, avg (giá điều chỉnh, đồng), change, pct, match_vol, match_val, buy/sell orders và volume, khối ngoại mua/bán (khối lượng, giá trị), room |
| `index_daily` | symbol, date | như `daily` cho VNINDEX, VN30, VN100 |
| `corporate_actions` | symbol, exright_date, title | ratio (hệ số điều chỉnh giá), cash_dividend |
| `master` | symbol, date | ceiling, floor, ref |
| `ticks/trade` | — | symbol, time, price, quantity, side, total_volume, open, high, low, avg, received_at |
| `ticks/quote` | — | symbol, time, bid1–3, ask1–3 và khối lượng, received_at |
| `ticks/index` | — | index, time, value, change, change_pct, advances, declines, unchanged, ceiling, floor, giá trị/khối lượng |

## Phụ lục B. Cài đặt

Xem README.md: `pip install -r requirements.txt`, `python main.py run`; với tài khoản thật: `.env`, `update`,
`check-access`, `record`, `tick-quality`.
