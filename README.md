# WeChat OA Collector

Project độc lập để thu thập bài đăng WeChat Official Account vào máy local, không dùng OpenAI API và không lưu mật khẩu WeChat.

## Khả năng hiện có

- Nhận callback `publicMsg` từ sidecar WeChat tương thích `wxbot`.
- Đọc XML bài đơn hoặc gói nhiều bài, chuẩn hóa URL và chống trùng.
- Nhập một hoặc nhiều link `mp.weixin.qq.com` để backfill bài lịch sử.
- Đọc tiêu đề, tài khoản OA, mô tả, ảnh bìa, ngày và nội dung bài khi trang cho phép.
- Lưu SQLite tại `data/wechat-oa.db`.
- Tìm kiếm trên giao diện và xuất CSV UTF-8.
- Chỉ bind `127.0.0.1` theo mặc định.

Lượt đọc/thích không nằm trong HTML công khai của mọi bài. Collector chỉ lưu hai chỉ số này khi payload sidecar thực sự cung cấp, không tự suy đoán.

## Chạy project

Trong PowerShell:

```powershell
cd D:\GQC\wechat-oa-collector
.\start.ps1
```

Sau đó mở:

```text
http://127.0.0.1:8107/
```

Project dùng Python standard library, không cần `pip install`.

## Backfill bài cũ không cần sidecar

1. Trong WeChat mở bài của `榴莲产业网`.
2. Chọn sao chép liên kết.
3. Dán link vào mục **Nhập bài lịch sử**.
4. Có thể dán nhiều link, mỗi link một dòng.

Đây là cách ổn định nhất để nhập bài cũ vì trạng thái "đã follow" không cung cấp cho chương trình một API lịch sử ổn định.

## Tự động thu thập bài mới qua RSS feed

Cách nhận bài mới tự động **không cần sidecar/hook**:

1. Chuẩn bị một RSS feed chứa bài WeChat OA (ví dụ từ WeWe RSS, RSSHub hoặc dịch vụ tương đương).
2. Mở giao diện collector tại `http://127.0.0.1:8107/`.
3. Tại mục **Theo dõi RSS Feed**, dán URL feed và bấm **Thêm feed**.
4. Poller tự động kiểm tra bài mới mỗi 10 phút (mặc định). Bài WeChat (`mp.weixin.qq.com`) sẽ được import tự động.

Thay đổi interval poll:

```powershell
python app.py --poll-interval 300
```

Hoặc qua biến môi trường:

```powershell
$env:WECHAT_POLL_INTERVAL = "300"
.\start.ps1
```

## Kết nối sidecar để nhận bài mới

Collector tương thích payload callback phổ biến có dạng:

```json
{
  "wxid": "wxid_xxx",
  "total": 1,
  "data": [
    {
      "Type": "49",
      "StrTalker": "gh_xxx",
      "CreateTime": "1789750800",
      "StrContent": "<msg>...</msg>"
    }
  ]
}
```

Callback URL:

```text
http://127.0.0.1:8107/api/wechat/callback
```

Nếu sidecar cung cấp endpoint `POST /api/syncurl`, nhập địa chỉ sidecar trên giao diện rồi bấm **Đăng ký publicMsg**. Yêu cầu được gửi tương đương:

```json
{
  "url": "http://127.0.0.1:8107/api/wechat/callback",
  "timeout": 10000,
  "type": "public-msg"
}
```

### Tình trạng tương thích trên máy này

Ngày dựng project, máy đang chạy WeChat/Weixin Windows `4.1.11.24`.

Các sidecar/hook công khai thường khóa theo phiên bản cụ thể. Không dùng binary dành cho WeChat `3.9.x` hoặc `4.1.10.27` với bản `4.1.11.24`. Collector đã sẵn sàng nhận callback, nhưng chưa tự cài/inject DLL vì thao tác đó có rủi ro tài khoản và an toàn máy tính.

Khi có sidecar tương thích `4.1.11.24`, chỉ cần:

1. Cho sidecar listen trên localhost, ví dụ `http://127.0.0.1:8080`.
2. Mở giao diện collector.
3. Bấm **Đăng ký publicMsg**.

## API local

- `GET /api/health` — kiểm tra dịch vụ.
- `GET /api/status` — thống kê và callback URL.
- `GET /api/articles?q=...` — danh sách/tìm bài.
- `GET /api/articles.csv` — tải toàn bộ bài dưới dạng CSV.
- `POST /api/wechat/import` — body `{"urls": "https://mp.weixin.qq.com/s?..."}`.
- `POST /api/wechat/callback` — nhận payload `publicMsg`.
- `POST /api/sidecar/register` — body `{"base_url": "http://127.0.0.1:8080"}`.
- `GET /api/feeds` — danh sách RSS feed đang theo dõi và trạng thái poller.
- `POST /api/feeds` — body `{"url": "https://...", "name": "Tên"}` thêm feed mới.
- `POST /api/feeds/poll` — kích hoạt poll tất cả feed ngay lập tức.
- `POST /api/feeds/delete` — body `{"id": 1}` xóa feed.
- `POST /api/feeds/toggle` — body `{"id": 1, "active": false}` tạm dừng/bật feed.

## Bảo mật

- Không mở port collector ra LAN/Internet.
- Không nhập cookie, mật khẩu hoặc dữ liệu đăng nhập WeChat vào giao diện.
- Nên dùng tài khoản phụ nếu sử dụng sidecar/hook không chính thức.
- Có thể đặt biến `WECHAT_COLLECTOR_TOKEN`; khi đó callback phải gửi header `X-Collector-Token` tương ứng.

## Kiểm thử

```powershell
python -m unittest discover -s tests -v
```

