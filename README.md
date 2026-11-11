# Area Wrongways

Web đấu bot Wrongway 1 chọi 1, mỗi trận 2 hiệp đổi bên, xem trực tiếp từng nước đi trên một terminal ở giữa màn hình.

Đây là **web tĩnh, không có máy chủ riêng**. Luật chơi và bot chạy trong trình duyệt, nên đưa lên GitHub Pages hoặc Vercel là dùng được, miễn phí và luôn bật. Các máy nói chuyện với nhau qua một broker MQTT công cộng.

## Cách hoạt động

```
Trình duyệt A (chủ phòng)                       Trình duyệt B
┌────────────────────────────┐   broker MQTT   ┌─────────────────────┐
│ Host (host.js)             │◄───────────────►│ app.js              │
│  ├ luật chơi: environment.py│   WebSocket     │ bot.js              │
│  │   (Pyodide, worker)      │   (mã hóa)      │  └ bot của B        │
│  └ bot của A (Pyodide)      │                 │     (Pyodide, worker)│
└────────────────────────────┘                 └─────────────────────┘
```

- **Phòng chơi:** chủ phòng chọn mã 4 số rồi giữ chỗ trên broker MQTT công cộng (thử lần lượt EMQX, HiveMQ, Mosquitto). Người kia nhập mã để vào cùng phòng. Mã đã có người dùng thì tự chọn mã khác. Kết nối đi qua WebSocket/HTTPS nên không bị ảnh hưởng bởi NAT hay tường lửa ở nhà như WebRTC.
- **Luật chơi:** vẫn là `wrongway/environment.py` nguyên bản, chạy bằng Pyodide (Python trong trình duyệt) trên máy chủ phòng.
- **Bot:** chạy bằng Pyodide trong Web Worker **trên máy của chính người viết bot**. Mỗi lượt chủ phòng gửi quan sát, bot trả về nước đi. Code bot không bao giờ rời khỏi máy của người viết. Chỉ có bàn cờ và nước đi được truyền qua broker, và đã mã hóa AES-GCM bằng khóa sinh từ mã phòng.
- **Quá giờ:** bot nghĩ quá 2 giây thì worker bị dừng, lượt đó bị bỏ, lượt sau nạp lại code (giống bản Python gốc). Giới hạn là 2 giây thay vì 1 giây vì Python trong trình duyệt chậm hơn.

Bot dùng được thư viện chuẩn của Python và các gói Pyodide có sẵn như `numpy` (tự tải khi `import`).

## Cách chơi

1. Người thứ nhất bấm **Tạo phòng**, nhập Tên và MSSV, nhận mã phòng 4 số. Nút "Sao chép link" cho ra link dạng `?room=0482`.
2. Người thứ hai bấm **Vào phòng**, nhập Tên, MSSV và mã phòng.
3. Mỗi người bấm **ADD BOT**, dán code (theo `wrongway/submissions/TEMPLATE.py`) hoặc tải file `.py`, rồi bấm **Sẵn sàng**. Cú pháp và hàm `choose_action` được kiểm tra ngay.
4. Khi cả hai sẵn sàng: đếm ngược 3-2-1, hiệp 1 chạy với chủ phòng là P0 (góc dưới trái), người còn lại là P1 (góc trên phải).
5. Hết hiệp 1, hai bên đổi chỗ, đếm ngược rồi chạy hiệp 2.
6. Bảng tổng kết hiện kết quả từng hiệp và người thắng chung cuộc. Điểm: thắng 3, hòa 1, thua 0. Bằng điểm thì hòa.

## Lưu ý khi dùng

- **Chủ phòng phải giữ nguyên tab.** Chủ phòng đóng tab hoặc mất mạng thì phòng đóng. Người vào phòng thoát giữa trận thì trận bị hủy.
- Lần đầu vào phòng, trình duyệt tải Python (khoảng 10 MB từ CDN, sau đó được cache), nên có thể mất vài giây.
- Web phụ thuộc vào các broker MQTT công cộng miễn phí. Nếu cả ba broker cùng sập thì không tạo hoặc vào phòng được (hiếm gặp). Tên và MSSV có đi qua broker, nhưng đã mã hóa bằng khóa sinh từ mã 4 số nên chỉ chống được người tò mò, không chống được người cố tình dò.
- Kết quả trận do trình duyệt của chủ phòng tính. Đây là web để đấu giao hữu, không phải hệ thống chấm điểm chống gian lận.

## Chạy thử trên máy

Chỉ cần một web server tĩnh bất kỳ:

```bash
python -m http.server 8000
```

Rồi mở http://localhost:8000. Mở thêm một tab (hoặc trình duyệt khác) để đóng vai người chơi thứ hai.

## Đưa lên mạng

Không cần build, không cần biến môi trường.

- **GitHub Pages:** Settings → Pages → Source: `Deploy from a branch`, Branch: `main`, Folder: `/ (root)`.
- **Vercel:** Add New Project → chọn repo → Framework Preset: `Other`, để trống Build Command và Output Directory.

## Cấu trúc thư mục

```
├── index.html              # sảnh + phòng đấu
├── web/
│   ├── style.css
│   ├── app.js              # giao diện, kết nối MQTT, terminal, ADD BOT, kết quả
│   ├── host.js             # "máy chủ" phòng chạy trong trình duyệt chủ phòng
│   ├── bot.js              # bọc Web Worker chạy bot, giới hạn thời gian
│   ├── worker_bot.js       # Pyodide chạy bot của người chơi
│   ├── worker_engine.js    # Pyodide chạy luật chơi
│   └── py/
│       ├── bot_harness.py  # kiểm tra code, nạp module, gọi choose_action
│       └── engine_glue.py  # bọc environment.py cho worker
└── wrongway/               # bộ mã gốc (CLI arena.py, luật chơi, bài mẫu)
```

Muốn chạy bot trên máy bằng dòng lệnh (không cần web), xem `wrongway/STUDENT_GUIDE.md`.
