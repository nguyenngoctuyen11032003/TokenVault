# Hướng dẫn cho nhân viên (Adapter)

TokenVault Adapter là chương trình nhỏ chạy nền, gửi **số token đã dùng** của Claude Code về máy quản lý trong mạng công ty. Nó **không** đọc hay gửi nội dung hội thoại của bạn. Xem [privacy.md](privacy.md).

## 1. Cài đặt
1. Nhận file `TokenVault-Adapter-<Nhóm>.exe` từ người quản lý (thư mục chia sẻ hoặc USB).
2. Bấm đúp để chạy. **Windows SmartScreen** có thể hiện "Windows protected your PC" vì file chưa có chữ ký số trả phí: bấm **More info** -> **Run anyway**. (Nếu tải bằng trình duyệt: chuột phải file -> Properties -> tích **Unblock** -> OK.)
3. Màn hình đồng ý liệt kê chính xác dữ liệu được gửi. Đọc và bấm đồng ý.
4. Cài xong, biểu tượng TokenVault hiện ở khay hệ thống (góc phải thanh tác vụ) và tự chạy mỗi lần đăng nhập. Không cần quyền admin.

**Máy chặn hẳn file .exe lạ** (không có nút "Run anyway")? Báo người quản lý để nhận gói **"không cần exe"** (`TokenVault-Adapter-<Nhóm>-portable.zip`): giải nén toàn bộ ra một thư mục, bấm đúp `1-CAI-DAT.cmd` (không cần quyền admin, tự bỏ dấu "tải từ Internet"), đọc màn hình đồng ý rồi đồng ý. Gỡ: chạy `2-GO-CAI-DAT.cmd` trong thư mục đó.

**Máy Mac:** nhận `TokenVault-Adapter-<Nhóm>-mac.zip`, nhấp đúp để giải nén, chuột phải `Install.command` -> **Open** (macOS 15: nếu không có tuỳ chọn này, vào System Settings -> Privacy & Security -> "Open Anyway"). Màn hình đồng ý hiện trước khi cài; từ chối thì không có gì thay đổi. Cài xong, biểu tượng nằm trên **thanh menu** (bắt buộc, không ẩn được; nếu không hiện được biểu tượng, chương trình dừng thu thập và báo bằng thông báo), khoá bí mật nằm trong Keychain, tự chạy khi đăng nhập. Gỡ: chuột phải `Uninstall.command` -> Open. Bản Mac chưa được kiểm chứng trên máy Mac thật.

## 2. Dữ liệu nào được gửi?
Mỗi câu trả lời của Claude: số token (vào/ra/cache), mô hình, thời điểm, mã phiên, **chỉ tên thư mục cuối** của dự án, tiêu đề phiên, **tên** công cụ đã gọi, email tài khoản Claude, tên người dùng Windows, tên máy, nhóm. **Không bao giờ**: câu hỏi, câu trả lời, mã nguồn, nội dung tệp, lệnh, đường dẫn đầy đủ, mật khẩu/khoá. Danh sách đầy đủ: [privacy.md](privacy.md).

## 3. Tự kiểm tra
* Chuột phải biểu tượng khay -> **Dữ liệu gửi đi là gì?** hiện một sự kiện thật đang chờ gửi.
* Dòng đầu của menu cho biết trạng thái kết nối và số sự kiện đang chờ.
* Dòng lệnh: `TokenVaultAdapter.exe status --json` (thêm `--lang en` để xem tiếng Anh).
* Nhật ký: menu khay -> "Mở thư mục nhật ký" (`%LOCALAPPDATA%\TokenVault\Adapter\adapter.log`, không chứa khoá/bí mật).

Adapter chủ ý **không có nút tạm dừng hay ẩn**; nó luôn hiển thị.

## 4. Gỡ cài đặt
Bộ cài .exe: Cài đặt Windows -> **Ứng dụng** -> "TokenVault Adapter" -> Gỡ cài đặt. Gói không cần exe: `2-GO-CAI-DAT.cmd` (thêm `-RemoveData` để xoá cả dữ liệu). Mac: `Uninstall.command`. Adapter dừng ngay, Manager thấy thiết bị ngoại tuyến.

## 5. Hỏi đáp
* **Có làm chậm máy không?** Không: gần 0% CPU lúc rảnh, dưới 40 MB RAM, ưu tiên thấp, chỉ theo dõi thư mục `~/.claude/projects`.
* **Ra khỏi mạng công ty thì sao?** Dữ liệu xếp hàng trên máy và gửi khi về lại mạng công ty.
* **Quản lý có thấy nội dung tôi viết không?** Không.
* **Không có tài khoản (dùng API key)?** Hiển thị "Tài khoản không xác định (người dùng@máy)".
* **Lần đầu cài có gửi lịch sử cũ không?** Có: nhật ký đã có được đọc nền, giới hạn tốc độ và đánh dấu là lịch sử.
* **Mac: không thấy biểu tượng trên thanh menu?** Chương trình cố ý không thu thập khi không hiện được biểu tượng; báo người quản lý (trạng thái `no_indicator` trong `status`).
* **Không thấy biểu tượng khay?** Bấm mũi tên "Hiện biểu tượng ẩn" trên thanh tác vụ; vẫn không thấy thì báo người quản lý.
