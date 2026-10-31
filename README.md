# Lott Lab

Phần mềm thử nghiệm phân tích kết quả **Oz Lotto, Powerball và Saturday Lotto** của The Lott (Úc).
Mỗi kỳ, hệ thống tạo **50 dòng số** và tự chốt vào **17:00 (giờ Sydney) ngày trước kỳ quay**.

## Hệ thống làm gì

| Việc | Khi nào |
|---|---|
| Lấy kết quả mới (chỉ khi đã đến giờ có kết quả) | Mỗi giờ từ khoảng 16:20 đến 00:20 giờ Sydney, và một lần buổi sáng |
| Tạo bản xem trước 50 dòng cho kỳ tiếp theo | Ngay khi có kết quả kỳ trước |
| **Chốt** 50 dòng (không đổi nữa) | 17:00 giờ Sydney, ngày trước kỳ quay |
| So bộ số đã chốt với kết quả, ghi lại giải trúng | Ngay khi có kết quả |
| Thử nghiệm các chiến lược trên dữ liệu quá khứ | Mỗi Chủ Nhật |

| Trò chơi | Ngày quay | Ngày chốt số |
|---|---|---|
| Oz Lotto (7/47 + 3 số phụ) | Thứ Ba | Thứ Hai, 17:00 |
| Powerball (7/35 + PB 1/20) | Thứ Năm | Thứ Tư, 17:00 |
| Saturday Lotto (6/45 + 2 số phụ) | Thứ Bảy | Thứ Sáu, 17:00 |

## Cách chọn 50 dòng số (sự thật về xác suất)

Mỗi kỳ quay là ngẫu nhiên và độc lập, nên **không phương pháp nào làm một dòng dễ trúng hơn**.
Trang web tự kiểm chứng điều này: kiểm định chi bình phương, kiểm định độc lập, và bảng thử nghiệm
so sánh "số nóng", "số lạnh" với chọn ngẫu nhiên.

Chiến lược mặc định `balanced` chỉ tối ưu những gì tối ưu được:

1. **Phủ đều**: 50 dòng trùng nhau ít nhất có thể, dùng đủ mọi số (Powerball: đủ cả 20 số PB).
   Số dòng trúng *trung bình* không đổi, nhưng **khả năng có ít nhất 1 dòng trúng giải mỗi kỳ** tăng
   (mô phỏng: Oz Lotto 63% → 70%, Powerball 65–68% → 77%, Saturday Lotto 45% → 50%).
2. **Tránh bộ số phổ biến**: ngày sinh (toàn số ≤ 31), dãy liên tiếp, cấp số cộng, cùng hàng chục,
   cùng chữ số cuối, bội số của 5 hoặc 7, lặp lại số kỳ trước, bộ số đã từng ra.
   Nếu trúng thì ít người phải chia giải hơn.

## Quy tắc "chỉ chơi khi jackpot lớn"

`config.json` → `min_jackpot`, riêng cho từng trò: Oz Lotto ≥ $50 triệu, Powerball ≥ $50 triệu,
Saturday Lotto ≥ $12 triệu. Các ngưỡng này được chọn bằng `python -m lottlab.optimize` (chạy lại mỗi tuần):
mức nhận lại mỗi $1 cao nhất mà vẫn chơi ít nhất 4 kỳ mỗi năm. Mỗi kỳ trang web hiện **NÊN CHƠI** hoặc **CHỜ** theo jackpot
kỳ đó (lấy từ lotteryguru.com). Quyết định được chốt cùng bộ số lúc 17:00 ngày trước kỳ quay.
Phần theo dõi tính lãi/lỗ riêng cho các kỳ đã chơi.

Nghiên cứu trên jackpot và lượng vé thật (`python -m lottlab.study`) cho thấy quy tắc này giảm lỗ
(giá trị mỗi $1 tăng từ ~$0,46 lên ~$0,59 với Oz Lotto, và số kỳ phải chơi giảm còn ~1/3), nhưng
không ngưỡng nào cho giá trị ≥ $1: jackpot càng lớn càng nhiều người mua và chia giải.

Đổi chiến lược trong `config.json` (`balanced`, `hot`, `cold`, `random`) và số dòng (`lines`).
Điền giá vé vào `ticket_price` để trang web tính chi phí.

## Cài đặt (một lần)

1. Đưa code này lên một repository GitHub (công khai, để dùng GitHub Pages miễn phí).
2. **Settings → Pages**: Source = *Deploy from a branch*, Branch = `main`, thư mục `/docs`.
   Trang web có địa chỉ `https://<tên-tài-khoản>.github.io/<tên-repo>/`, mở được trên mọi máy.
3. **Settings → Actions → General → Workflow permissions**: chọn *Read and write permissions*.
4. **Actions → Cập nhật kết quả → Run workflow** để chạy lần đầu (tự tải toàn bộ lịch sử).
5. **Actions → Thử nghiệm chiến lược → Run workflow** để có bảng thử nghiệm ngay.

## Nguồn dữ liệu

| Nguồn | Dùng để | Ghi chú |
|---|---|---|
| [australia.national-lottery.com](https://australia.national-lottery.com/oz-lotto/results-archive-2025) | Toàn bộ lịch sử, mỗi năm 1 trang (Oz Lotto từ 1994, Powerball từ 1996, Saturday Lotto từ 1985) | Tải một lần, sau đó chỉ trang năm hiện tại |
| [lotteryextreme.com](https://www.lotteryextreme.com/australia/ozlotto-results) | 20 kỳ gần nhất, bảng tiền thưởng, và lịch sử (mỗi kỳ 1 trang, tải lùi tối đa 300 trang/lần, 2 lần/ngày) | Nguồn chính cho kỳ mới |

Mỗi kỳ được đối chiếu giữa 2 nguồn; nếu số khác nhau, hệ thống giữ số cũ và hiện cảnh báo.
Phần mềm tự giới thiệu là công cụ tự động (User-Agent `LottLab/1.0`), đọc robots.txt trước khi tải,
nghỉ 3 giây giữa các lần tải, và chỉ tải khi đã đến giờ có kết quả mới (khoảng 3 trang mỗi kỳ quay).
The Lott chặn truy cập từ ngoài nước Úc nên không được dùng làm nguồn tự động.

## Nếu các nguồn không có kết quả

Trang web sẽ hiện cảnh báo màu vàng. Khi đó vào **Actions → Nhập kết quả thủ công → Run workflow**
và nhập số kỳ, ngày, các số chính, số phụ hoặc Powerball (làm được ngay trên điện thoại bằng app GitHub).
Có thể nhập cả lịch sử bằng CSV: `python -m lottlab import-csv oz file.csv`
(cột `draw,date,main,supp`, các số cách nhau bằng dấu cách).

## Chạy trên máy cá nhân

```bash
pip install -r requirements.txt
python -m lottlab update        # lấy kết quả, chốt số, dựng dữ liệu web
python -m lottlab backtest      # thử nghiệm chiến lược
python -m http.server -d docs   # mở http://localhost:8000
python -m pytest -q             # kiểm thử
```

## Cấu trúc

```
lottlab/games.py      luật chơi, hạng giải, xác suất chính xác
lottlab/sources.py    đọc kết quả từ các trang công bố (tôn trọng robots.txt)
lottlab/generator.py  tạo 50 dòng (phủ đều + tránh số phổ biến)
lottlab/analysis.py   thống kê, kiểm định ngẫu nhiên, mô phỏng, thử nghiệm
lottlab/cli.py        cập nhật, chốt, đối chiếu, dựng dữ liệu web
lottlab/value.py      giá trị kỳ vọng mỗi dòng theo jackpot
lottlab/study.py      nghiên cứu quy tắc ngưỡng jackpot (thực tế + mô phỏng)
lottlab/models.py     kiểm chứng các phương pháp dự đoán (LSTM-like, Random Forest, Markov...)
lottlab/replay.py     chạy lùi theo ngân sách
data/<trò chơi>/      kết quả (draws.json) và các bộ số đã chốt (picks/)
docs/                 trang web tĩnh (GitHub Pages)
```

---

Chơi xổ số luôn có kỳ vọng âm. Hãy đặt ngân sách cố định.
Hỗ trợ tại Úc: Gambling Help Online, 1800 858 858.
