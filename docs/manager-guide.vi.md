# Hướng dẫn cho người quản lý (Manager)

## 1. Yêu cầu
* Một máy Windows 10 21H2+/11 bật suốt giờ làm việc. Nếu tắt, Adapter tự xếp hàng dữ liệu và gửi lại khi Manager sẵn sàng.
* WebView2 (bộ cài tự kiểm tra và cài bootstrapper).
* Cùng mạng nội bộ với máy nhân viên. Không hỗ trợ truy cập từ xa/Internet.

## 2. Cài đặt
1. Chạy `TokenVault-Manager-Setup.exe` (cài theo người dùng; chỉ bước tường lửa cần UAC). Bộ cài **không cài exe chưa ký** của Manager: nó chép Python đã ký số của Python Software Foundation (`runtime\`) cùng mã TokenVault và tạo lối tắt chạy `runtime\pythonw.exe -m tokenvault.manager` (Start menu, Desktop, khởi động cùng Windows), nên Smart App Control / Application Control không chặn Manager. SmartScreen chỉ cảnh báo file Setup: "More info" -> "Run anyway". Nếu máy chặn hẳn cả file Setup (lỗi "An Application Control policy has blocked this file"): giải nén `TokenVault-Manager-<version>.zip` (tạo bằng `build\build-portable.ps1`), chạy `1-CHAY-MANAGER.cmd`, hoặc `Install-Manager.ps1` để có lối tắt và quy tắc tường lửa (một lần UAC). Xem [packaging.md](packaging.md).
   * **Tường lửa:** Setup mở hộp thoại UAC để tạo 2 quy tắc (chạy `python.exe` đã ký, không phải exe lạ). Nếu bạn từ chối hoặc bị chặn, Setup vẫn hoàn tất và hiện lệnh cần chạy; bấm lối tắt **"Mở cổng tường lửa cho TokenVault (cần quyền admin)"** trong Start menu để thử lại bằng một cú nhấp, hoặc chạy trong cửa sổ quản trị: `"<thư mục cài>\runtime\python.exe" -m tokenvault.manager firewall --ensure --port 47800`. Kiểm tra: `... firewall --check`; xem trước không thay đổi gì: `... firewall --ensure --dry-run`.
2. Lần chạy đầu Manager tự tạo chứng chỉ TLS tự ký (không cần openssl) và cơ sở dữ liệu trong `%APPDATA%\TokenVault\Manager`. Khoá bí mật được bảo vệ bằng DPAPI.
3. Cổng mặc định **47800** (TCP, WebSocket qua TLS). Nếu bận, Manager dùng cổng kế tiếp (tối đa +20); cổng thực tế hiện ở Cài đặt/Chẩn đoán. Cổng khám phá **47801/UDP**.
4. Đóng cửa sổ chỉ ẩn vào khay hệ thống, ingest vẫn chạy. Có tuỳ chọn khởi động cùng Windows trong Cài đặt.

## 3. IP tĩnh (khuyến nghị)
Bộ cài nhân viên chứa IP/hostname của Manager. Hãy đặt IP tĩnh hoặc "DHCP reservation" trên router cho máy Manager. Nếu IP đổi, Adapter vẫn tự tìm lại bằng UDP broadcast trong cùng mạng con; khác mạng con thì phải sửa địa chỉ (Cài đặt -> Máy chủ) và tạo lại bộ cài.

## 4. Tạo bộ cài cho nhân viên
Trang **Tạo bộ cài**: nhập tên nhóm và nhãn tuỳ chọn -> `TokenVault-Adapter-<Nhóm>-portable.zip` (mặc định; hoặc `.exe` nếu bạn chọn bộ cài .exe; thư mục mặc định trong `%APPDATA%\TokenVault\Manager\installers`). Gói chứa **mã đăng ký (enrollment secret)** - coi như mật khẩu, chỉ gửi cho đúng nhóm. Lộ thì thu hồi gói ở trang Cài đặt/Nhân viên.
Chọn **Hệ điều hành của nhân viên** (xem [packaging.md](packaging.md)):

| Lựa chọn | Tệp tạo ra | Khi nào dùng | Chuẩn bị một lần |
|---|---|---|---|
| **Windows – gói không cần exe** (mặc định, đứng đầu) | `TokenVault-Adapter-<Nhóm>-portable.zip` | mọi máy Windows; bắt buộc với máy chặn hẳn exe chưa ký (Smart App Control / Application Control) | có sẵn khi cài Manager bằng bộ cài chuẩn; chạy từ mã nguồn thì chạy `build\build-portable.ps1` (cần Internet) một lần |
| Windows – bộ cài .exe (có thể bị Windows chặn nếu máy bật Smart App Control) | `TokenVault-Adapter-<Nhóm>.exe` | máy Windows chỉ có SmartScreen ("More info" -> "Run anyway") | đã có sẵn khi cài Manager |
| macOS (tệp .zip) | `TokenVault-Adapter-<Nhóm>-mac.zip` | máy Mac | build `adapter-mac.zip` trên một máy Mac bằng `build/build-mac.sh`, chép vào `%APPDATA%\TokenVault\Manager\templates\adapter-mac.zip` |

Gói portable: nhân viên chạy `1-CAI-DAT.cmd` (không cần admin). Gói Mac: `Install.command` (chuột phải -> Open). Trang kết quả hiện hướng dẫn gửi kèm đúng loại gói. macOS chưa được kiểm chứng trên Mac thật.

**Chọn gói nào?** Không chắc -> portable (mặc định). SmartScreen chỉ cảnh báo -> exe dùng được. Bị chặn hẳn (không có "Run anyway", lỗi 4551) -> portable. Mac -> mac. SmartScreen và Smart App Control khác nhau: xem [troubleshooting.vi.md](troubleshooting.vi.md) mục 9. Gửi qua chia sẻ mạng/USB để tránh Mark-of-the-Web.

## 5. Phân phối khi không có chứng chỉ ký số
* Gửi qua **thư mục chia sẻ mạng hoặc USB**: file không mang Mark-of-the-Web nên SmartScreen không chặn.
* File tải qua trình duyệt/email: chuột phải -> Properties -> **Unblock**, hoặc `Unblock-File .\TokenVault-Adapter-Dev.exe`.
* Tuỳ chọn cho IT: tạo chứng chỉ ký mã tự ký, phân phối bằng **GPO -> Trusted Publishers** (kèm Trusted Root) rồi ký bộ cài. Miễn phí nhưng không bắt buộc. Không ký file template (sẽ bị Manager sửa); chỉ ký file bộ cài nhóm đã tạo xong. Cách này chưa được dự án kiểm thử.

## 6. Dùng bảng điều khiển
Trang chủ (đồng hồ token/chi phí/phiên hôm nay, bảng nhân viên), Tổng quan, Nhân viên, Dự án, Phiên, Kỹ năng, Điều phối (agent chính và phụ), Tạo bộ cài, Cài đặt, Chẩn đoán.
* Bộ lọc: nhóm, nhân viên (theo email Claude), dự án, mô hình, khoảng ngày.
* Nhân viên được định danh theo **email tài khoản Claude**; nhiều máy của một người được gộp. Không có tài khoản -> "Tài khoản không xác định".
* **Chi phí** là "ước tính theo giá API" từ `data/pricing.json`; mô hình chưa có giá hiển thị UNKNOWN, không đoán.
* Đổi nhóm của thiết bị áp dụng cả cho lịch sử.
* **Xuất PDF** từ các trang Nhân viên/Tổng quan (vào `Documents\TokenVault Reports`); tiêu đề phiên không bao giờ được xuất.
* **Chẩn đoán**: thiết bị đang kết nối, sự kiện cuối, hàng đợi, sự kiện bị từ chối, quy tắc tường lửa, hạn chứng chỉ, nút kiểm tra kết nối.

## 7. Chứng chỉ và thu hồi
* Vân tay SHA-256 của chứng chỉ được ghim trong mọi bộ cài. **Tạo lại chứng chỉ** (thao tác tường minh) làm mọi Adapter đã cài mất kết nối; phải phát lại bộ cài mới.
* Thu hồi thiết bị: đóng phiên đang mở và không cho kết nối lại.

## 8. Dữ liệu và sao lưu
`manager.db` (sự kiện), `enrollment.db` (gói + khoá thiết bị), `certs\`. Sao lưu cả `%APPDATA%\TokenVault\Manager`. Khoá chỉ giải mã được bởi đúng tài khoản Windows (DPAPI), nên khôi phục sang máy/tài khoản khác cần tạo lại chứng chỉ và bộ cài.

Sự cố: [troubleshooting.vi.md](troubleshooting.vi.md). Quyền riêng tư: [privacy.md](privacy.md).
