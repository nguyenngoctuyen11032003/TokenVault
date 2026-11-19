# Hướng dẫn cho nhân viên (Adapter)

TokenVault Adapter là chương trình nhỏ chạy nền, gửi **số token đã dùng** của Claude Code về máy quản lý trong mạng công ty. Nó **không** đọc hay gửi nội dung hội thoại của bạn. Xem [privacy.md](privacy.md).

## 1. Cài đặt
1. Nhận file `TokenVault-Adapter-<Nhóm>.exe` từ người quản lý (thư mục chia sẻ hoặc USB).
2. Bấm đúp để chạy. **Windows SmartScreen** có thể hiện "Windows protected your PC" vì file chưa có chữ ký số trả phí: bấm **More info** -> **Run anyway**. (Nếu tải bằng trình duyệt: chuột phải file -> Properties -> tích **Unblock** -> OK.)
3. Màn hình đồng ý liệt kê chính xác dữ liệu được gửi. Đọc và bấm đồng ý.
4. Cài xong, biểu tượng TokenVault hiện ở khay hệ thống (góc phải thanh tác vụ) và tự chạy mỗi lần đăng nhập. Không cần quyền admin.

Máy bị chặn chạy file .exe lạ? Báo người quản lý để nhận gói "no-exe" (**đang hoàn thiện, sẽ cập nhật sau**). Mac: gói ZIP có `Install.command` (**đang hoàn thiện, sẽ cập nhật sau**; lần đầu macOS có thể yêu cầu System Settings -> Privacy & Security -> "Open Anyway").

## 2. Dữ liệu nào được gửi?
Mỗi câu trả lời của Claude: số token (vào/ra/cache), mô hình, thời điểm, mã phiên, **chỉ tên thư mục cuối** của dự án, tiêu đề phiên, **tên** công cụ đã gọi, email tài khoản Claude, tên người dùng Windows, tên máy, nhóm. **Không bao giờ**: câu hỏi, câu trả lời, mã nguồn, nội dung tệp, lệnh, đường dẫn đầy đủ, mật khẩu/khoá. Danh sách đầy đủ: [privacy.md](privacy.md).

## 3. Tự kiểm tra
* Chuột phải biểu tượng khay -> **Dữ liệu gửi đi là gì?** hiện một sự kiện thật đang chờ gửi.
* Dòng đầu của menu cho biết trạng thái kết nối và số sự kiện đang chờ.
* Dòng lệnh: `TokenVaultAdapter.exe status --json` (thêm `--lang en` để xem tiếng Anh).
* Nhật ký: menu khay -> "Mở thư mục nhật ký" (`%LOCALAPPDATA%\TokenVault\Adapter\adapter.log`, không chứa khoá/bí mật).

Adapter chủ ý **không có nút tạm dừng hay ẩn**; nó luôn hiển thị.

## 4. Gỡ cài đặt
Cài đặt Windows -> **Ứng dụng** -> "TokenVault Adapter" -> Gỡ cài đặt. Adapter dừng ngay, Manager thấy thiết bị ngoại tuyến.

## 5. Hỏi đáp
* **Có làm chậm máy không?** Không: gần 0% CPU lúc rảnh, dưới 40 MB RAM, ưu tiên thấp, chỉ theo dõi thư mục `~/.claude/projects`.
* **Ra khỏi mạng công ty thì sao?** Dữ liệu xếp hàng trên máy và gửi khi về lại mạng công ty.
* **Quản lý có thấy nội dung tôi viết không?** Không.
* **Không có tài khoản (dùng API key)?** Hiển thị "Tài khoản không xác định (người dùng@máy)".
* **Lần đầu cài có gửi lịch sử cũ không?** Có: nhật ký đã có được đọc nền, giới hạn tốc độ và đánh dấu là lịch sử.
* **Không thấy biểu tượng khay?** Bấm mũi tên "Hiện biểu tượng ẩn" trên thanh tác vụ; vẫn không thấy thì báo người quản lý.
