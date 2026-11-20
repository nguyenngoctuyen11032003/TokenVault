# Bảo mật TokenVault / Security

Tài liệu này tóm tắt các cơ chế bảo mật của TokenVault, mô hình ký số và cách báo cáo lỗ
hổng. Chi tiết kỹ thuật: [docs/architecture.md](docs/architecture.md) (mô hình bảo mật),
[docs/privacy.md](docs/privacy.md) (dữ liệu thu thập), [docs/release-security.md](docs/release-security.md)
(chữ ký số, SmartScreen, Smart App Control), [BUILD.md](BUILD.md) (build và phát hành).

## 1. Kết nối Adapter -> Manager

* **Chỉ trong mạng nội bộ.** Adapter gửi dữ liệu tới máy Manager qua WebSocket trên **TLS
  1.2+** (TCP 47800, có thể +20 khi cổng bận). Không có máy chủ đám mây, không có bên thứ ba.
* **Ghim chứng chỉ (certificate pinning).** Manager tự tạo chứng chỉ ECDSA P-256 khi chạy lần
  đầu. Bộ cài mang **vân tay SHA-256** của chứng chỉ này; Adapter kiểm tra vân tay (so sánh
  hằng thời gian), không dựa vào tên máy hay CA. Gói tin tìm Manager (UDP 47801) chỉ được chấp
  nhận khi khớp nonce, mã Manager và vân tay; sau đó TLS kiểm tra vân tay một lần nữa.
* **Đăng ký thiết bị (enrollment).** Mỗi gói cài có một **mã đăng ký** ngẫu nhiên
  (`secrets.token_urlsafe(32)`); lần kết nối đầu, thiết bị nhận một **khoá thiết bị** riêng.
  Manager chỉ lưu SHA-256 của mã/khoá. Có thể **thu hồi** gói hoặc thiết bị (phiên đang mở bị
  đóng ngay). Thiết bị đã có khoá không thể bị "chiếm" bằng cách đăng ký lại.
* **Giới hạn phía máy chủ:** giới hạn số kết nối theo IP và tổng, thời gian chờ bắt tay và
  `hello`, tối đa 4 KiB trước xác thực, khung tối đa 256 KiB, kiểm tra chặt từng trường.

**Bộ cài theo nhóm là một thông tin xác thực**: ai có file đó có thể đăng ký một thiết bị vào
nhóm. Hãy phát qua kênh nội bộ và thu hồi gói khi bị lộ.

## 2. Dữ liệu và quyền riêng tư

* Chỉ **siêu dữ liệu sử dụng** (số token, mô hình, thời điểm, tên thư mục cuối, tên công cụ,
  tài khoản/máy) - **không** gửi nội dung câu hỏi, câu trả lời, mã nguồn, lệnh, tham số công
  cụ, đường dẫn đầy đủ hay mật khẩu. Danh sách trường được kiểm tra tự động với mã nguồn:
  [docs/privacy.md](docs/privacy.md).
* **Minh bạch:** màn hình đồng ý trước khi cài; biểu tượng khay luôn hiển thị (không có nút
  tạm dừng/ẩn); menu "Dữ liệu gửi đi là gì?" cho xem sự kiện thật đang chờ gửi.
* Dữ liệu chỉ lưu trên máy Manager (`%APPDATA%\TokenVault\Manager`).

## 3. Bảo vệ bí mật trên máy

* **Windows - DPAPI (phạm vi người dùng):** khoá thiết bị và mã đăng ký của Adapter, mật khẩu
  khoá riêng TLS của Manager (khoá riêng lưu dạng PKCS#8 mã hoá). Lỗi giải mã -> dừng (fail
  closed), không quay về dạng rõ.
* **macOS - Keychain** (service `vn.ics.tokenvault.adapter`); file cấu hình chỉ chứa dấu
  tham chiếu. Lưu ý: mục Keychain tạo bằng công cụ `security` đọc được bởi tiến trình cùng
  người dùng (tương đương DPAPI phạm vi người dùng).

## 4. Hành vi bộ cài

* **Adapter:** cài theo người dùng, **không cần quyền admin** (`%LOCALAPPDATA%\Programs`,
  khởi động cùng Windows qua `HKCU\...\Run`). Hỏi đồng ý **trước** khi đụng tới bản cài cũ.
  Gỡ bằng Settings -> Apps; dữ liệu chỉ bị xoá khi người dùng đồng ý.
* **Manager:** cài theo người dùng; **một** bước nâng quyền (UAC) duy nhất để tạo luật Windows
  Firewall cho cổng nhận dữ liệu và UDP tìm kiếm; gỡ cài đặt xoá các luật đó.
* **Gói no-exe (portable):** không có exe chưa ký; dùng `python.exe`/`pythonw.exe` do Python
  Software Foundation ký, script kiểm tra chữ ký đó trước khi cài.
* Không có cơ chế tự cập nhật, không tải mã từ Internet khi chạy (trừ bộ cài WebView2 của
  Microsoft khi build, được kiểm tra chữ ký Microsoft trước khi đóng gói).

## 5. Mô hình ký số

* Bản **Development** (mặc định) **chưa ký**: Windows 11 bật **Smart App Control sẽ chặn**
  các file exe; nơi khác SmartScreen cảnh báo "Windows protected your PC". Dùng gói no-exe cho
  máy bật Smart App Control.
* Bản **Production** được ký Authenticode SHA-256 + timestamp RFC 3161 bằng chứng chỉ của
  công ty (OV trên HSM/token hoặc Microsoft Artifact Signing nếu đủ điều kiện), gồm file của
  TokenVault và các DLL/.pyd bên thứ ba chưa có chữ ký; file đã có chữ ký của nhà cung cấp giữ
  nguyên.
* Bộ cài theo nhóm (`TokenVault-Adapter-<nhóm>.exe`) **luôn chưa ký** vì Manager nối cấu hình
  vào cuối file. Ký số không bảo đảm vượt qua SmartScreen ngay lập tức.
* Chúng tôi **không** khuyến nghị tắt Smart App Control/Defender, sửa registry/GPO để né
  kiểm tra, hay dùng chứng chỉ tự ký như một "cách sửa".

## 6. Kiểm tra file tải về

```powershell
# 1. So SHA-256 với SHA256SUMS.txt đi kèm bản phát hành
Get-FileHash .\TokenVault-Manager-Setup.exe -Algorithm SHA256
# 2. Bản Production: chữ ký phải Valid, đúng tên công ty, có timestamp
Get-AuthenticodeSignature .\TokenVault-Manager-Setup.exe | Format-List Status, SignerCertificate, TimeStamperCertificate
```

Hoặc chuột phải -> Properties -> Digital Signatures. Bản Development hiển thị `NotSigned`: chỉ
tin khi hash khớp và file đến từ kênh nội bộ đáng tin. `release-report.md` liệt kê hash và
trạng thái chữ ký của từng file bên trong gói.

## 7. Báo cáo lỗ hổng

* Liên hệ: **security-contact@example.invalid** (*placeholder - chủ dự án cần thay bằng địa
  chỉ thật trước khi phát hành*).
* Vui lòng gửi: phiên bản (`release-report.md`), mô tả, bước tái hiện, ảnh hưởng. Không công
  khai trước khi có bản sửa. Chúng tôi phản hồi trong khả năng của nhóm; chưa có chương trình
  thưởng.
* Phạm vi: Adapter, Manager, bộ cài, script build/phát hành trong kho này.

## English summary

* **Transport:** LAN only, WebSocket over TLS 1.2+, self-signed Manager certificate pinned by
  SHA-256 fingerprint in every installer; discovery answers must match nonce, manager id and
  fingerprint.
* **Enrollment:** random per-package secret + per-device key (only SHA-256 stored, constant-time
  compare), revocable packages/devices. A team installer is a credential.
* **Privacy:** metadata only (token counts, model, time, folder leaf name, tool names,
  account/host); never prompts, replies, code, commands or full paths. Consent screen and an
  always-visible tray icon.
* **Secrets at rest:** DPAPI (user scope) on Windows, Keychain on macOS; fail closed.
* **Installers:** per-user, no admin for the Adapter; one elevated step for the Manager's
  firewall rules; data removed only with consent.
* **Signing:** Development builds are unsigned (Smart App Control blocks them; SmartScreen
  warns); Production builds are Authenticode-signed (SHA-256, RFC 3161). Per-team installers
  stay unsigned by design. Signing does not guarantee passing SmartScreen immediately.
* **Verify a download:** compare `Get-FileHash` with `SHA256SUMS.txt`; for Production check
  `Get-AuthenticodeSignature` is `Valid` with the company name and a timestamp.
* **Report vulnerabilities** to the placeholder contact above (to be replaced by the owner).
