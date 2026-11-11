# Wrongway Online

Web đấu bot Wrongway 1 chọi 1, mỗi trận 2 hiệp đổi bên, xem trực tiếp từng nước đi trên một terminal ở giữa màn hình. Máy chủ gọi thẳng `arena.run_match` trong thư mục `wrongway`, nên luật chơi, giới hạn 1 giây/nước và cách tính lượt giống hệt khi chạy `arena.py` trên máy.

## Cài đặt và chạy

Cần Python 3.9 trở lên.

**Windows**

```bat
cd wrongway-web
py -3 -m pip install -r requirements.txt
py -3 app.py
```

Hoặc bấm đúp `run_windows.bat`.

**macOS / Linux**

```bash
cd wrongway-web
python3 -m pip install -r requirements.txt
python3 app.py
```

Mở `http://localhost:8000`. Người khác trong cùng mạng LAN/Wi-Fi vào bằng `http://<IP-máy-chạy-server>:8000` (xem IP bằng `ipconfig` trên Windows). Lần đầu chạy, Windows có thể hỏi quyền tường lửa cho Python; cần bấm Allow thì máy khác mới vào được.

Muốn chạy kiểu dev có tự tải lại khi sửa code:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

## Cách chơi

1. Người thứ nhất bấm **Tạo phòng**, nhập Tên và MSSV, nhận mã phòng 4 số. Nút "Sao chép link" cho ra link dạng `/?room=0482`, người nhận mở link là form Vào phòng đã điền sẵn mã.
2. Người thứ hai bấm **Vào phòng**, nhập Tên, MSSV và mã phòng.
3. Mỗi người bấm **ADD BOT** trong cột phải, dán code (theo `TEMPLATE.py`) hoặc tải file `.py`, rồi bấm **Sẵn sàng**. Server kiểm tra cú pháp và hàm `choose_action` ngay lúc này, có lỗi thì báo dưới khung code.
4. Khi cả hai đã sẵn sàng: đếm ngược 3‑2‑1, hiệp 1 chạy với chủ phòng là P0 (góc dưới trái), người còn lại là P1 (góc trên phải).
5. Hết hiệp 1, hai thẻ người chơi đổi chỗ cho nhau, đếm ngược lần nữa rồi chạy hiệp 2.
6. Xong hai hiệp, bảng tổng kết hiện kết quả từng hiệp và người thắng chung cuộc. Điểm tính như mùa giải: thắng 3, hòa 1, thua 0. Hai bên bằng điểm thì xử hòa.
7. "Chơi lại (giữ bot)" đánh dấu sẵn sàng với code cũ. "Đổi code bot" mở lại khung soạn thảo.

## Các quy tắc phòng đã cài

- Tối đa 2 người mỗi phòng. Hai người trong cùng phòng không được trùng MSSV.
- Mã phòng 4 số được chọn ngẫu nhiên trong các mã chưa dùng, nên hai phòng đang hoạt động không bao giờ trùng mã.
- Phòng hết người thì bị xóa khỏi RAM và mã được trả lại ngay, phòng mới sau đó có thể nhận lại mã này.
- Chủ phòng thoát (bấm Rời phòng, đóng tab hay mất mạng) thì người còn lại lên làm chủ phòng.
- Có người thoát giữa trận thì trận bị hủy, các tiến trình bot bị dừng, phòng quay về trạng thái chờ.
- Code của đối thủ không bao giờ được gửi về trình duyệt của bạn, bạn chỉ thấy họ đã sẵn sàng hay chưa.
- Hai hiệp dùng chung một seed, nên bot dùng `random` cũng được đối xử như nhau ở hai bên.

## Cấu hình

Đặt biến môi trường trước khi chạy (ví dụ `set WW_TURN_DELAY=0.1` trên Windows CMD):

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `WW_TIMEOUT` | `1.0` | Giây cho mỗi lần gọi `choose_action` |
| `WW_STARTUP_TIMEOUT` | `5.0` | Giây cho phép import file bot |
| `WW_MAX_TURNS` | `500` | Giới hạn tổng số lượt, quá thì hòa |
| `WW_TURN_DELAY` | `0.25` | Giây nghỉ giữa hai lượt để người xem kịp nhìn. Không tính vào giờ của bot |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Địa chỉ lắng nghe |

Một ván hòa 500 lượt với delay 0.25 giây kéo dài khoảng 2 phút. Nếu thấy chậm, hạ `WW_TURN_DELAY` xuống 0.1.

## Cấu trúc thư mục

```
wrongway-web/
├── app.py              # FastAPI + WebSocket: phòng, ID, đổi chủ phòng, chạy 2 hiệp
├── requirements.txt
├── run_windows.bat
├── static/
│   ├── index.html      # sảnh + phòng đấu
│   ├── style.css
│   └── app.js          # client WebSocket, terminal, ADD BOT, đếm ngược, bảng kết quả
├── wrongway/           # bộ mã gốc, giữ nguyên không sửa
└── runtime/            # tự tạo khi chạy: nơi lưu tạm file bot của từng phòng
```

## Luồng dữ liệu

```
Trình duyệt ──WebSocket /ws──► app.py ──asyncio.to_thread──► arena.run_match()
     ▲                           │                               │ on_turn(record)
     └──── room / countdown / ───┘◄── call_soon_threadsafe ──────┘
           round_start / turn / round_end / match_end
```

Mỗi lượt, server vẽ bàn cờ bằng `visualizer.render_board` (đúng chuỗi ASCII mà `arena.py --render` in ra) và gửi xuống. Trình duyệt chỉ tô màu cho chuỗi đó: quân 0 xanh ngọc, quân 1 cam, tường tím.

Tin client gửi lên: `create`, `join`, `leave`, `ready` (kèm `code`), `unready`.

## Lưu ý bảo mật

`agent_runner.py` chỉ cô lập lỗi chứ không cô lập quyền truy cập: bot chạy dưới quyền của người chạy server, đọc/ghi được file và vào được mạng. Chỉ nên mở server cho nhóm bạn cùng lớp tin tưởng nhau. Nếu cần đưa lên Internet, hãy chạy trong container (Docker) hoặc máy ảo không chứa dữ liệu quan trọng.

Toàn bộ phòng nằm trong RAM. Tắt server là mất hết phòng, đó là hành vi mong muốn cho một buổi đấu trên lớp.
