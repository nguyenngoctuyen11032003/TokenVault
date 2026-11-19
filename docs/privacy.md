# TokenVault - Tuyên bố quyền riêng tư / Privacy statement

Tài liệu này mô tả chính xác dữ liệu mà TokenVault Adapter gửi về máy Manager. Danh sách trường bên dưới được
kiểm tra tự động (`tests/unit/test_privacy_doc.py`) so với cấu trúc `UsageEvent` trong mã nguồn: nếu mã gửi
thêm một trường mà tài liệu này không liệt kê, bài kiểm tra sẽ thất bại.

This document states exactly what the TokenVault Adapter sends to the Manager PC. The field table is checked by
an automated test against the `UsageEvent` data class.

## Dữ liệu được thu thập / What is collected

Mỗi câu trả lời của Claude Code tạo một bản ghi (khoá theo `message_id`, không trùng lặp).

<!-- PRIVACY-FIELDS:BEGIN -->
| Trường | Ý nghĩa |
|---|---|
| `uuid` | mã định danh của dòng nhật ký |
| `session_id` | mã phiên làm việc Claude Code |
| `message_id` | mã câu trả lời của Claude |
| `timestamp` | thời điểm câu trả lời được ghi |
| `model` | tên mô hình, ví dụ claude-sonnet |
| `reasoning_effort` | mức độ suy luận (low / medium / high) |
| `is_sidechain` | câu trả lời do tác tử phụ (sub-agent) tạo ra hay không |
| `input_tokens` | số token đầu vào |
| `output_tokens` | số token đầu ra |
| `cache_read_tokens` | số token đọc từ cache |
| `cache_create_5m_tokens` | số token ghi cache (5 phút) |
| `cache_create_1h_tokens` | số token ghi cache (1 giờ) |
| `project` | CHỈ tên thư mục cuối cùng của thư mục làm việc, không phải đường dẫn đầy đủ |
| `session_title` | tiêu đề phiên do Claude Code tự đặt (`ai-title`) |
| `tool_names` | CHỈ tên các công cụ được gọi, không kèm tham số |
| `claude_account_email` | email tài khoản Claude đang đăng nhập (từ `~/.claude.json`) |
| `account_uuid` | mã tài khoản Claude |
| `windows_user` | tên người dùng Windows |
| `hostname` | tên máy tính |
| `team` | tên nhóm ghi trong gói cài đặt |
| `device_id` | mã thiết bị (máy + người dùng Windows) |
<!-- PRIVACY-FIELDS:END -->

Ghi chú: `session_title` có thể chứa vài từ mô tả công việc do Claude Code tóm tắt; đây là trường duy nhất có
nội dung dạng văn bản tự do. Nếu không có tài khoản đăng nhập (API key/Bedrock), email để trống và Manager hiển
thị "Tài khoản không xác định (người dùng@máy)".

## Không bao giờ thu thập / Never collected

<!-- PRIVACY-NEVER:BEGIN -->
- Nội dung câu hỏi (prompt) của bạn / your prompts
- Nội dung câu trả lời của Claude / Claude's replies
- Nội dung tệp, mã nguồn / file contents and source code
- Lệnh đã chạy, tham số và kết quả của công cụ / commands, tool inputs and outputs
- Đường dẫn thư mục đầy đủ (chỉ tên thư mục cuối) / full directory paths
- Mật khẩu, khoá API, token đăng nhập Claude / passwords, API keys, Claude login tokens
- Ảnh chụp màn hình, thao tác bàn phím, lịch sử trình duyệt / screenshots, keystrokes, browsing history
- Hoạt động ngoài Claude Code (Claude.ai web, Claude Desktop chat) / anything outside Claude Code
<!-- PRIVACY-NEVER:END -->

## Cách hoạt động minh bạch / Transparency

- Màn hình đồng ý (consent) liệt kê đúng các trường trên trước khi cài.
- Biểu tượng khay hệ thống luôn hiển thị; không có nút tạm dừng hay ẩn. Menu "Dữ liệu gửi đi là gì?" hiển thị
  một sự kiện thật đang chờ gửi. Lệnh `TokenVaultAdapter.exe status --json` in cùng nội dung.
- Dữ liệu chỉ gửi tới máy Manager trong mạng nội bộ (mặc định cổng 47800, TLS, ghim vân tay chứng chỉ). Không có
  máy chủ đám mây, không có bên thứ ba.
- Khoá thiết bị và mã đăng ký được bảo vệ bằng Windows DPAPI trên máy nhân viên.
- Chi phí là "ước tính theo giá API" tính tại Manager từ số token; không gửi thông tin thanh toán.
- Gỡ cài đặt: Cài đặt Windows -> Ứng dụng; Adapter dừng gửi ngay và Manager hiển thị thiết bị ngoại tuyến.

Dữ liệu lưu trên máy Manager (`%APPDATA%\TokenVault\Manager`) và chỉ người quản lý đó xem được.
