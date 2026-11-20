TOKENVAULT ADAPTER - GÓI CÀI ĐẶT KHÔNG CẦN FILE .EXE
======================================================

CÁCH CÀI (3 bước, không cần quyền Administrator):

  1. Chuột phải file .zip  ->  "Giải nén tất cả..." (Extract All) ra một thư mục thường
     (ví dụ Documents\TokenVault). KHÔNG chạy trực tiếp bên trong WinRAR / trình xem ZIP.
  2. Mở thư mục vừa giải nén, nháy đúp file  Install.cmd
  3. Đọc cửa sổ "Đồng ý" (liệt kê chính xác dữ liệu được gửi) rồi bấm "Đồng ý".

Xong: Adapter chạy nền, tự khởi động cùng Windows. Biểu tượng TokenVault nằm ở khay hệ
thống (góc phải thanh tác vụ). Bấm vào đó để xem "Dữ liệu gửi đi là gì?".

GỠ CÀI: nháy đúp Uninstall.cmd (thêm -RemoveData nếu muốn xóa cả dữ liệu đã thu thập).

DỮ LIỆU ĐƯỢC GỬI: chỉ số token, tên model, tên thư mục dự án (không phải đường dẫn đầy đủ),
tiêu đề phiên, tên công cụ, email tài khoản Claude, tên máy và tên người dùng Windows.
KHÔNG gửi nội dung hội thoại, câu lệnh, nội dung file hay đường dẫn đầy đủ.

Vì sao không có file .exe: gói chạy bằng Python chính thức (python.exe / pythonw.exe do
Python Software Foundation ký số) cùng mã nguồn TokenVault (đọc được trong runtime\app),
nên máy bật Smart App Control / Application Control vẫn cho chạy.

LƯU Ý
  * Gửi gói qua thư mục mạng chung, USB hoặc chat. File tải bằng trình duyệt bị gắn dấu
    "Mark of the Web"; Install.cmd tự gỡ dấu này.
  * Gói này chứa mã đăng ký của MỘT nhóm: chỉ chia sẻ cho đúng nhóm đó. Mã chỉ dùng được
    với Manager của công ty và có thể bị thu hồi ngay trên Manager.

--------------------------------------------------------------------------------------
ENGLISH SUMMARY
  1. Right-click the ZIP -> "Extract All..." (do not run it from inside a ZIP viewer).
  2. Double-click Install.cmd (no administrator rights).
  3. Read the consent screen and press Accept.
Uninstall: Uninstall.cmd. The package contains no unsigned program: it runs the official
Python runtime (signed by the Python Software Foundation) and TokenVault's Python source.
