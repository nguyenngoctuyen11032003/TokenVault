# Xử lý sự cố

Bắt đầu từ trang **Chẩn đoán** của Manager (nút "Kiểm tra kết nối": cổng, tường lửa, IP LAN, chứng chỉ) và menu khay của Adapter (dòng trạng thái + "Dữ liệu gửi đi là gì?").

## 1. Nhân viên không xuất hiện trong Manager
| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| Adapter báo ngoại tuyến, Manager không thấy kết nối | Tường lửa chặn | Mục 2 |
| Chỉ lỗi trên Wi-Fi hoặc VPN | Khác mạng con / VPN chặn LAN | Mục 3 |
| Máy Manager có nhiều card mạng | Bộ cài chứa sai IP | Mục 4 |
| Kết nối được lúc trước, giờ không | Manager đổi cổng/IP/chứng chỉ | Mục 5, 6 |
| Đã kết nối nhưng không có số liệu | Chưa dùng Claude Code, hoặc nhật ký ở thư mục khác (`CLAUDE_CONFIG_DIR`) | Dùng Claude Code một lượt rồi xem "Dữ liệu gửi đi là gì?" |

## 2. Tường lửa
Bộ cài Manager tạo các quy tắc `TokenVault-Manager-*` (TCP cổng ingest, UDP 47801). Kiểm tra:
```powershell
TokenVault-Manager.exe firewall --check      # in JSON, mã thoát 0 = đủ quy tắc
TokenVault-Manager.exe firewall --ensure     # tạo lại (hiện UAC)
netsh advfirewall firewall show rule name=all | findstr TokenVault
```
Lưu ý: nếu mạng Windows được đặt là **Public**, hãy đổi sang **Private**, hoặc bảo đảm quy tắc áp dụng cho cả profile Public. Phần mềm diệt virus/tường lửa bên thứ ba cần cho phép riêng `TokenVault-Manager.exe` và cổng 47800.

## 3. VPN / Wi-Fi so với LAN
* Máy nhân viên phải tới được IP của Manager trên TCP 47800. Nhiều router tách Wi-Fi và dây thành mạng con khác nhau hoặc bật "AP/Client isolation": thử `Test-NetConnection <IP-Manager> -Port 47800` từ máy nhân viên.
* UDP broadcast (khám phá, 47801) **không vượt qua mạng con hoặc VPN**. Nếu IP Manager đổi và nhân viên ở mạng con khác, Adapter không tự tìm lại được: đặt IP tĩnh cho Manager (khuyến nghị) hoặc sửa địa chỉ và phát lại bộ cài.
* VPN full-tunnel có thể làm Adapter đi đường VPN thay vì LAN: bật "cho phép truy cập mạng cục bộ" (split tunnel).
* Rời mạng công ty không phải sự cố: Adapter xếp hàng và gửi lại sau.

## 4. Nhiều card mạng (Ethernet + Wi-Fi + VPN + Hyper-V/WSL)
Bộ cài chứa danh sách địa chỉ của Manager. Trong Cài đặt, mục "Máy chủ": để trống = tự động (tất cả IP LAN phát hiện), hoặc nhập thủ công đúng IP mà nhân viên thấy; bỏ các IP ảo (172.x của Hyper-V/WSL, VPN). Sau khi sửa, **tạo lại bộ cài** vì địa chỉ nằm trong gói.

## 5. Cổng bị chiếm
Manager mở cổng 47800 ở chế độ độc quyền. Nếu chương trình khác đang giữ cổng, Manager dùng cổng kế tiếp (tối đa +20: 47801...47820) và hiển thị cổng thực tế ở Cài đặt/Chẩn đoán; bộ cài tạo sau đó mang cổng đó. Lưu ý 47801 UDP là cổng khám phá riêng (khác giao thức, không xung đột TCP). Bộ cài đã phát với cổng cũ cần được tạo lại, hoặc giải phóng cổng 47800 (`netstat -ano | findstr 47800`, đóng tiến trình đang giữ) rồi khởi động lại Manager.

## 6. Tạo lại chứng chỉ
Adapter ghim vân tay SHA-256 của chứng chỉ Manager; không khớp = từ chối kết nối (cố ý, chống giả mạo). Chứng chỉ chỉ đổi khi chủ ý dùng "Tạo lại chứng chỉ" (file cũ được giữ dạng `*.bak-<thời gian>`), hoặc khi mất thư mục `%APPDATA%\TokenVault\Manager\certs` / chuyển sang tài khoản Windows khác (DPAPI không giải mã được khoá). Hậu quả: **mọi Adapter đã cài mất kết nối**; phải tạo và phát lại bộ cài mới. Đổi IP không làm đổi chứng chỉ. Chứng chỉ hết hạn sau 10 năm; Adapter bỏ qua ngày hết hạn.

## 7. Hàng đợi ngoại tuyến của Adapter
Khi không gửi được, sự kiện nằm trong `outbox.db` (SQLite bền vững, `%LOCALAPPDATA%\TokenVault\Adapter`) và được gửi lại khi có kết nối (thử lại với giãn cách tăng dần tới 60 s, có jitter). Biên nhận từ Manager mới xoá sự kiện, nên không mất và không trùng (Manager upsert theo `device_id + message_id`). Xem số sự kiện chờ ở menu khay hoặc `TokenVaultAdapter.exe status`. Hàng đợi tăng mãi = vẫn mất kết nối: quay lại mục 2-6. Không xoá `outbox.db` khi còn sự kiện chưa gửi.

## 8. Khác
* **Bị thu hồi** (Manager đóng phiên, lý do `revoked`): gói/thiết bị đã bị thu hồi; cần bộ cài mới.
* **Máy chặn exe lạ hoàn toàn** (Smart App Control / Application Control: không có "Run anyway" hoặc exe không chạy): tạo gói "Windows - gói không cần exe" ở trang Tạo bộ cài, nhân viên giải nén và chạy `Install.cmd`. Nếu trang báo thiếu runtime: chạy `build\build-portable.ps1` rồi thử lại (script tạo `templates\portable-runtime`). Nếu `Install.cmd` vẫn báo lỗi (mã 10) thì chính sách chặn cả DLL/.pyd chưa ký trong thư viện Python; cần IT cho phép (chưa kiểm chứng trên máy bị chặn thật).
* **Gói portable cài rất chậm lần đầu:** `pythonw.exe` mới chép có thể mất tới ~2 phút ở lần chạy đầu do quét thời gian thực; hãy đợi, không bấm lại.
* **SmartScreen vẫn chặn file nhận qua trình duyệt/email:** Mark-of-the-Web. Gửi lại qua chia sẻ mạng/USB, hoặc Properties -> Unblock / `Unblock-File`. Gói portable tự gỡ dấu khi chạy `Install.cmd`.
* **Mac: trang Tạo bộ cài báo thiếu bộ mẫu macOS:** build `adapter-mac.zip` trên một máy Mac (`build/build-mac.sh`) rồi chép vào `%APPDATA%\TokenVault\Manager\templates\adapter-mac.zip` trên máy Manager.
* **Mac: macOS không cho mở `Install.command`:** chuột phải -> Open; macOS 15 thì System Settings -> Privacy & Security -> "Open Anyway".
* **Mac: không có biểu tượng thanh menu / thiết bị không gửi dữ liệu:** Adapter cố ý dừng thu thập khi không hiện được biểu tượng (`status` báo `no_indicator`, thoát mã 0 nên launchd không chạy lại). Đăng nhập lại hoặc cài lại; xem `~/Library/Logs/TokenVault/adapter.log`.
* **Mac: kiểm tra/gỡ thủ công:** `launchctl print gui/$(id -u)/vn.ics.tokenvault.adapter`; gỡ bằng `Uninstall.command`. Phần Mac chưa được kiểm chứng trên Mac thật ([e2e-checklist.md](e2e-checklist.md)).
* Nhật ký: Manager `%APPDATA%\TokenVault\Manager\manager.log`, Adapter `%LOCALAPPDATA%\TokenVault\Adapter\adapter.log` (xoay vòng 1 MB x 3, không chứa khoá).
