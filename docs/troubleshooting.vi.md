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
* **Máy chặn exe lạ hoàn toàn** (Smart App Control/AppLocker): dùng gói "no-exe" - Task 25, **sẽ cập nhật sau khi hoàn thiện**.
* Nhật ký: Manager `%APPDATA%\TokenVault\Manager\manager.log`, Adapter `%LOCALAPPDATA%\TokenVault\Adapter\adapter.log` (xoay vòng 1 MB x 3, không chứa khoá).
