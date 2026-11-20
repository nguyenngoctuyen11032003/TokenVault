# Build và phát hành TokenVault (Windows)

Tài liệu này mô tả cách build, ký số (tuỳ chọn), kiểm tra và đóng gói một bản phát hành.
Phân tích về chữ ký số, SmartScreen, Smart App Control và chi phí:
[docs/release-security.md](docs/release-security.md). Bảo mật sản phẩm: [SECURITY.md](SECURITY.md).
Chi tiết các loại gói (exe / no-exe / macOS): [docs/packaging.md](docs/packaging.md).

## 1. Chuẩn bị

| Thành phần | Bắt buộc | Ghi chú |
|---|---|---|
| Windows 10/11 x64, PowerShell 5.1 | có | |
| Python 3.11+ trong `.venv` với `pip install -e .[dev]` | có | gồm PyInstaller |
| Inno Setup 6.3+ (`ISCC.exe`) | có cho bộ cài | `winget install --id JRSoftware.InnoSetup -e` |
| Windows SDK `signtool.exe` | chỉ khi ký | thành phần "Windows SDK Signing Tools for Desktop Apps" hoặc gói NuGet `Microsoft.Windows.SDK.BuildTools`; hoặc đặt `TOKENVAULT_SIGNTOOL` |
| Chứng chỉ ký số | chỉ cho Production | xem mục 4 |
| `pip-audit` | không | chỉ dùng với `-Online` |

## 2. Lệnh

```powershell
# Bản phát triển (mặc định, cho phép chưa ký):
powershell -File packaging\release\release.ps1

# Bản chính thức (bắt buộc có chứng chỉ, thất bại nếu thiếu hoặc chữ ký không hợp lệ):
powershell -File packaging\release\release.ps1 -Mode Production

# Chỉ build như trước (không ký, không báo cáo):
powershell -File packaging\build.ps1 [-DistDir D:\tmp\dist]

# Xem trước file nào sẽ được ký (không cần chứng chỉ):
powershell -File packaging\release\sign.ps1 -Path dist -WhatIf

# Kiểm tra một thư mục bất kỳ và tạo báo cáo:
powershell -File packaging\release\verify-release.ps1 -Path dist -OutDir dist\report
```

Tham số `release.ps1`: `-Mode Development|Production`, `-Python <exe>`, `-Online` (chạy
pip-audit nếu đã cài), `-KeepStaging` (giữ `build\release-staging` để soi), `-SkipWebView2Download`.

## 3. Pipeline `release.ps1`

1. **Preflight:** đọc phiên bản từ `pyproject.toml`; `sign.ps1 -CheckCredentials`
   (Production thiếu chứng chỉ -> dừng ngay, exit 2); ghi phiên bản công cụ.
2. **Dọn sạch:** `build\pyi-*`, `build\release-staging`, `release\vX.Y.Z`.
3. **Kiểm tra phụ thuộc:** `pip list --format json` so với `pyproject.toml`, `pip check`,
   `pip-audit` (chỉ với `-Online`, không bắt buộc mạng).
4. **Build** `packaging\build.ps1 -Sign -SignMode <Mode>`: PyInstaller adapter -> **ký
   adapter** -> Inno template (không ký, theo thiết kế) -> PyInstaller Manager -> **ký thư mục
   Manager** -> Inno Manager Setup, **ISCC ký Setup và uninstaller** qua `/Stvsign=`.
5. **Kiểm tra gói:** các package trong `_internal`, phụ thuộc ngoài danh sách khai báo,
   10 file nhị phân lớn nhất, cảnh báo của PyInstaller.
6. **Thư mục phát hành:** chép `TokenVault-Manager-Setup.exe` vào `release\vX.Y.Z\`, chạy lại
   `sign.ps1` (idempotent - bỏ qua file đã ký cùng chứng chỉ).
7. **Xác minh:** `verify-release.ps1` trên thư mục phát hành + thư mục Manager đã đóng gói +
   adapter -> `release-report.md`, `release-report.json`, `SHA256SUMS.txt`.
8. **Xoá staging** (trừ khi `-KeepStaging`).

Kết quả: `release\vX.Y.Z\` chỉ gồm `TokenVault-Manager-Setup.exe`, `SHA256SUMS.txt`,
`release-report.md`, `release-report.json`, `build.log`. Exit: 0 PASS/WARN, 1 có FAIL,
2 thiếu chứng chỉ (Production), 3 build lỗi.

Tái lập (reproducible): `SOURCE_DATE_EPOCH` = thời điểm commit cuối và `PYTHONHASHSEED=0`
được đặt; Python dùng cho `.pyc`, còn PyInstaller/Inno có dùng hay không thì **chưa kiểm
chứng**. Phiên bản Python, PyInstaller, pip, Inno, signtool, commit được ghi trong báo cáo.

## 4. Ký số: cấu hình chứng chỉ

Chỉ chọn **một** nguồn (biến môi trường; tham số cùng tên của `sign.ps1` ghi đè):

| Chế độ | Biến | Ghi chú |
|---|---|---|
| Chứng chỉ trong kho Windows (kể cả khoá trên token/HSM) | `TOKENVAULT_SIGN_THUMBPRINT`, `TOKENVAULT_SIGN_CERT_STORE` (`CurrentUser`/`LocalMachine`), `TOKENVAULT_SIGN_TIMESTAMP_URL` | cách khuyến nghị cho chứng chỉ OV/EV (khoá trên HSM bắt buộc từ 6/2023) |
| File PFX | `TOKENVAULT_SIGN_PFX`, `TOKENVAULT_SIGN_PFX_PASSWORD`, `TOKENVAULT_SIGN_TIMESTAMP_URL` | mật khẩu **chỉ** từ biến môi trường, được che `********` trong log; signtool vẫn nhận mật khẩu qua dòng lệnh của tiến trình con. Chỉ dành cho CA nội bộ. |
| Microsoft Artifact Signing | `TOKENVAULT_SIGN_ARTIFACT_DLIB` (`...\x64\Azure.CodeSigning.Dlib.dll`), `TOKENVAULT_SIGN_ARTIFACT_METADATA` (`metadata.json`), tuỳ chọn `TOKENVAULT_SIGN_EXPECTED_SUBJECT` | cài: `winget install -e --id Microsoft.Azure.ArtifactSigningClientTools`; đăng nhập Azure qua `DefaultAzureCredential` (ví dụ `az login` hoặc `AZURE_TENANT_ID`/`AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`). Timestamp mặc định `http://timestamp.acs.microsoft.com`. |

`metadata.json` cho Artifact Signing (theo tài liệu Microsoft; `Endpoint` phải đúng vùng
của tài khoản, ví dụ `https://weu.codesigning.azure.net`):

```json
{
  "Endpoint": "https://<region>.codesigning.azure.net",
  "CodeSigningAccountName": "<account>",
  "CertificateProfileName": "<certificate profile>"
}
```

`TOKENVAULT_SIGN_TIMESTAMP_URL`: dùng URL RFC 3161 mà CA của bạn công bố (script không tự
đoán). Mọi chữ ký: `signtool sign /fd SHA256 /tr <url> /td SHA256`, sau đó
`signtool verify /pa /v` + `Get-AuthenticodeSignature` (trạng thái Valid, có timestamp,
digest SHA-256).

**File nào được ký** (`packaging\release\signing.json`): file của TokenVault
(`TokenVault-Manager.exe`, `TokenVaultAdapter.exe`, `TokenVault-Manager-Setup.exe`,
uninstaller của Inno); file nhị phân bên thứ ba **chưa có chữ ký** (Smart App Control kiểm
tra cả DLL/.pyd); **không bao giờ** ký lại file đã có chữ ký hợp lệ của nhà cung cấp (Python
Software Foundation, Microsoft). `adapter-setup-template.exe` và mọi bộ cài theo nhóm **không
ký** - Manager nối dữ liệu đăng ký vào cuối file, chữ ký sẽ hỏng.

Mã thoát `sign.ps1`: 0 ổn / chưa ký ở Development, 2 thiếu hoặc sai chứng chỉ, 3 không có
signtool, 4 ký lỗi, 5 xác minh lỗi, 10 (`-CheckCredentials`) chưa cấu hình ở Development.

## 5. Kiểm tra bản tải về

```powershell
Get-FileHash .\TokenVault-Manager-Setup.exe -Algorithm SHA256   # so với SHA256SUMS.txt
Get-AuthenticodeSignature .\TokenVault-Manager-Setup.exe | Format-List Status, SignerCertificate, TimeStamperCertificate
```

## 6. Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| Banner `UNSIGNED build` | Bình thường ở Development. Muốn ký: cấu hình mục 4, chạy `-Mode Production`. |
| `signtool.exe was not found` | Cài Windows SDK (Signing Tools) hoặc đặt `TOKENVAULT_SIGNTOOL`. |
| `several credential sources configured` | Chỉ để lại một bộ biến `TOKENVAULT_SIGN_*`. |
| `certificate not found` / `no private key` | Sai thumbprint, sai kho, hoặc token chưa cắm/chưa mở khoá. |
| `VERIFY FAIL ... status UnknownError/NotTrusted` | Chuỗi chứng chỉ không về CA tin cậy (chứng chỉ tự ký, thiếu chứng chỉ trung gian). |
| `no timestamp` | Máy chủ timestamp không truy cập được; kiểm tra mạng/proxy và URL. |
| Artifact Signing 403 | `Endpoint` sai vùng, thiếu vai trò *Certificate Profile Signer*, tên account/profile sai. |
| `Sign Tool command failed` trong ISCC | Xem dòng `sign.ps1` ngay trước đó trong `build.log`. |
| Build exit 3 | Không tìm thấy Inno Setup. |
| Máy build bật Smart App Control | Không chạy được exe mới chưa ký; kiểm tra chỉ ở dạng tĩnh (báo cáo). |

## English summary

* `packaging\release\release.ps1 [-Mode Development|Production]` builds from a clean state,
  audits dependencies, signs (when credentials exist), builds the installer, verifies it
  statically and writes `release\vX.Y.Z\` with the installer, `SHA256SUMS.txt` and the
  Release Security Report (`release-report.md/.json`).
* Development (default) allows unsigned output and prints an UNSIGNED banner; Production
  requires a signing credential and fails on any unsigned/untrusted/untimestamped binary.
* Credentials: store thumbprint (HSM/token keys), PFX with the password from an environment
  variable (internal CA only), or Microsoft Artifact Signing (dlib + metadata.json). SHA-256
  file digest, RFC 3161 timestamp, verification after signing, idempotent.
* The adapter template and per-team installers stay unsigned by design (payload appended).
* Plain `packaging\build.ps1` without `-Sign` behaves exactly as before.
