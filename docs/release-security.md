# Release security: code signing, SmartScreen, Smart App Control

Sources were read on **2026-11-20** (accessed 2026-11). Anything not confirmed by a source is
marked **unverified**. Build tooling: [BUILD.md](../BUILD.md). Product security:
[SECURITY.md](../SECURITY.md).

## Tóm tắt (Tiếng Việt)

* **Nguyên nhân chính xác:** mọi file `.exe` do TokenVault tạo ra (Manager, Adapter, bộ cài
  Setup, bộ cài theo nhóm) **chưa ký số Authenticode** và là file mới, Microsoft chưa biết.
  Trên Windows 11 khi **Smart App Control (SAC) đang bật**, file như vậy bị **chặn cứng**
  (lỗi 4551, không có "Run anyway"). Khi SAC tắt, chỉ còn **SmartScreen** cảnh báo mềm
  ("Windows protected your PC" -> More info -> Run anyway) với file tải từ Internet.
* SAC kiểm tra **mọi** exe/DLL được nạp, nên 16 file `.pyd/.dll` bên thứ ba không chữ ký
  trong gói Manager cũng có thể bị chặn dù exe chính đã ký.
* Ký số **không bảo đảm** qua ngay SmartScreen (danh tiếng vẫn phải tích luỹ, EV không còn ưu
  tiên từ 2024). Với SAC, chữ ký từ CA thuộc Microsoft Trusted Root Program **là** điều kiện
  để chạy khi dịch vụ đám mây không có kết luận - nhưng file bị đánh giá độc hại vẫn bị chặn.
* **Microsoft Artifact Signing** (rẻ nhất, ~9,99 USD/tháng) **chỉ** cho tổ chức ở Mỹ,
  Canada, EU, Anh và cá nhân ở Mỹ/Canada - **công ty ở Việt Nam hiện không đăng ký được**.
  Lựa chọn khả thi: chứng chỉ **OV** của CA truyền thống (~150-300 USD/năm, khoá trên
  HSM/token) hoặc, với máy công ty có quản lý, **App Control for Business/Intune cho phép theo
  hash/publisher (miễn phí)**. Gói **no-exe** (python.exe do PSF ký) vẫn là đường miễn phí.
* Bộ cài theo nhóm luôn **không ký được** theo thiết kế hiện tại (Manager nối payload vào
  cuối file). Muốn ký phải đổi thiết kế: bộ cài chung đã ký + file cấu hình riêng.

## Summary (English)

The exact cause of the block is: unsigned, never-seen-before PE files. Smart App Control (SAC)
hard-blocks them; SmartScreen only warns. Signing with a publicly trusted certificate is the
documented way to run under SAC, but it does not buy instant SmartScreen reputation. Microsoft
Artifact Signing is not available to Vietnamese organisations today; an OV certificate (HSM
key) or a managed-fleet allow-list are the realistic paths; the no-exe package remains the
free route. Per-team installers cannot be signed with the current payload-append design.

## 1. Inventory of the real build

Built with `packaging\build.ps1 -DistDir ...\release-t31` (PyInstaller 6.22.3, Python
3.12.10, Inno Setup 6.7.3) on 2026-11-20, then inspected with `Get-AuthenticodeSignature`,
`signtool verify /pa /v`, the version resources and `mt.exe` (manifests). 292 files, 52 PE
files (5 `.exe`, 18 `.dll`, 29 `.pyd`). The same inventory is produced automatically by
`packaging\release\verify-release.ps1` (section "PE inventory" of `release-report.md`).

| Signer | Files | Notes |
|---|---|---|
| **none (NotSigned)** | 21 | all 5 first-party exe + 16 redistributed binaries |
| Python Software Foundation | 24 | `python312.dll`, `python3.dll`, stdlib `.pyd`, `libcrypto-3`, `libssl-3`, `libffi-8`, `sqlite3.dll` - valid, timestamped |
| Microsoft Corporation | 5 | WebView2 `Core`/`WinForms` + 3x `WebView2Loader.dll` |
| Microsoft Windows Software Compatibility Publisher | 2 | `VCRUNTIME140.dll`, `VCRUNTIME140_1.dll` |

First-party executables (all unsigned):

| File | Kind | Version resource | Manifest |
|---|---|---|---|
| `TokenVault-Manager.exe` | PyInstaller 6 onedir bootloader, windowed, no UPX | CompanyName `TokenVault`, FileDescription `TokenVault Manager`, ProductVersion `0.1.0.0` | asInvoker, longPathAware, Win7-11 supportedOS |
| `TokenVaultAdapter.exe` | PyInstaller onefile, windowed, no UPX | `TokenVault Adapter`, `0.1.0.0` | same |
| `TokenVault-Manager-Setup.exe` | Inno Setup 6.7.3, per-user (`PrivilegesRequired=lowest`), lzma2 | `TokenVault Manager Setup`, ProductVersion `0.1.0` | asInvoker, dpiAware |
| `adapter-setup-template.exe` | Inno Setup per-user template | `TokenVault Adapter Setup` | asInvoker |
| team installer (`TokenVault-Adapter-<team>.exe`) | template + appended `TVLTCFG1` payload, made at run time by the Manager | as template | as template |

Unsigned redistributed binaries (16): `cryptography\..\_rust.pyd`, Pillow `_imaging`,
`_imagingft`, `_imagingcms`, `_imagingtk`, `_webp`, `_avif` (7.9 MB), `_cffi_backend`,
`charset_normalizer` `cd`/`md`, `websockets\speedups`, `pythonnet\Python.Runtime.dll`,
`clr_loader` `ClrLoader.dll` (x64+x86), pywebview `WebBrowserInterop.x64/x86.dll`.

Icon: `assets\logo\tokenvault.ico` in both PyInstaller exes and both Inno setups.
Metadata fixed in this task: `packaging\version_*.txt` gained `InternalName`,
`OriginalFilename` (used by App Control *FileName/FilePublisher* rules) and `LegalCopyright`.
**The owner must replace `TokenVault` with the legal company name** in CompanyName /
LegalCopyright / Inno `AppPublisher` once a certificate is bought: they should match the
certificate subject `O=`. Still missing in the `.iss` files (owned by the packaging task):
`AppPublisherURL`, `AppSupportURL`, `VersionInfoCopyright`. Found but harmless:
`webview\lib\pywebview-android.jar` (Android-only file shipped on Windows) and the Pillow AVIF
plugin; both are candidates for a `.spec` exclude, **not changed** because the frozen Manager
`--selftest` cannot be executed on this SAC-enabled build machine to prove it safe.

## 2. Four different mechanisms

| Mechanism | What it decides | Unsigned TokenVault exe | Signed (trusted CA) |
|---|---|---|---|
| **Authenticode** | A signature (hash + certificate chain + timestamp) proves *who* published the file and that it is unmodified. It is an input to the others, not a verdict. | no publisher, "Unknown publisher" | publisher name shown; tamper evident |
| **SmartScreen** (Microsoft Defender SmartScreen, app reputation) | Reputation of the file hash and of the signing certificate, for files with the Mark-of-the-Web. **Soft** warning; users may choose *Run anyway* unless policy forbids it. | "Windows protected your PC" until the hash has reputation; every new build starts at zero | still "unrecognized" for new files until reputation accumulates; reputation can carry over across releases signed with the same identity |
| **Smart App Control** (Windows 11) | Code integrity policy: runs only what Microsoft's cloud app intelligence predicts safe, or what is signed by a CA in the **Microsoft Trusted Root Program**. Applies to **every** executable/DLL, not only downloads. No per-app exception. | **Blocked** (error 4551, no override) | allowed when the cloud has no verdict; still blocked if judged malicious/PUA |
| **Microsoft Defender Antivirus** | Malware detection (signatures, ML, cloud). False positives are possible for packers/bootloaders; PyInstaller bootloaders are a known source. | scanned | scanned the same way (signing does not exempt) |

Which machines are affected: Windows 11 PCs with SAC **On** (clean installs where the
evaluation period ended in enforcement). SAC turns itself off during evaluation on many
developer/corporate machines. Since recent updates SAC can be re-enabled without a clean
install, except when optional diagnostic data is off ([MS Support][sac-support]).
Enterprise devices may instead run App Control for Business / AppLocker, which block unsigned
code by IT policy; SmartScreen behaviour can also be locked by policy.

## 3. What this task delivers (and what it cannot)

* `packaging\release\sign.ps1` signs with SHA-256 + RFC 3161 timestamp using a store
  certificate (incl. HSM/token keys), a PFX (internal CA only) or Microsoft Artifact Signing;
  it signs first-party files and unsigned redistributed binaries, never re-signs vendor-signed
  files, and verifies every signature. No credentials: Development builds stay unsigned with
  a banner; Production fails.
* `packaging\release\release.ps1` + `verify-release.ps1` produce `release\vX.Y.Z\` with
  `SHA256SUMS.txt` and the **Release Security Report** (UNSIGNED section, PASS/WARN/FAIL).
* The **adapter template and every per-team installer stay unsigned**: the Manager appends
  the payload to the file, which would invalidate a signature. Signing it anyway and appending
  data after the signature relies on a verification weakness that Windows can be configured
  to reject; we do not do that. A signable design is: ship one signed, generic adapter
  installer and hand the team configuration (`tokenvault-config.bin`) out as a separate file,
  as the portable and macOS packages already do. That is a product change for a later task.
* Nothing here bypasses SmartScreen, SAC or Defender. No registry/GPO tricks, no self-signed
  certificates presented as a fix, no packers.

## 4. Research (accessed 2026-11)

**Microsoft Artifact Signing (formerly Trusted Signing).** Renamed, same service. Pricing:
Basic ~9.99 USD/month (5,000 signatures, 1 certificate profile of each type), Premium
100,000 signatures / 10 profiles ([Learn: code signing options][cso];
[Azure pricing page][as-price] - the page itself did not render the amounts, so the 9.99 figure
comes from Learn; Premium price **unverified**). Eligibility: organisations in the **USA,
Canada, EU and UK**; individuals only in the **USA and Canada** ([cso]). Identity validation by
Microsoft, a few business days; a **paid** Azure subscription is required (no free/trial);
the certificate CN/O is the validated legal name, no custom CN/O; no EV certificates
([Artifact Signing FAQ][as-faq]). A minimum company age of three years is quoted in Microsoft
Q&A answers but is not in the current FAQ: **unverified**. Certificates are issued by
Microsoft, live **three days**, so timestamping is mandatory; recommended TSA `http://timestamp.acs.microsoft.com`.
SignTool integration: SignTool >= 10.0.22621.755, .NET 8 runtime, the
`Microsoft.ArtifactSigning.Client` dlib (`Azure.CodeSigning.Dlib.dll`) and a `metadata.json`
with `Endpoint` (regional `https://<region>.codesigning.azure.net`), `CodeSigningAccountName`,
`CertificateProfileName`; auth through `DefaultAzureCredential`; installer
`winget install -e --id Microsoft.Azure.ArtifactSigningClientTools`
([Learn: signing integrations][as-int]). SmartScreen: reputation builds over time, initial
warnings expected ([cso]). For SAC the relevant fact is that it chains to a Microsoft CA in
the Trusted Root Program, which SAC accepts ([SAC overview][sac]).
**For a company registered in Vietnam this service is currently not available.**

**OV vs EV certificates.** OV 150-300 USD/year, EV 400+ USD/year (DigiCert, Sectigo,
GlobalSign and others) ([cso]); vendor list prices **unverified**. Since **June 2023** the
CA/Browser Forum requires the private key on an HSM or hardware token (USB token or a CA cloud
HSM/signing service) ([cso]) - a plain PFX file is no longer issued for public code signing.
**EV no longer gives instant SmartScreen reputation (removed in 2024)**; EV behaves like OV
([cso]; [SmartScreen reputation][ss]).

**SmartScreen reputation.** Two signals: publisher (certificate) reputation and file-hash
reputation. Signed new files may still warn; no threshold is published ("can take several
weeks and hundreds of clean installs"); there is no submission mechanism for consumer
SmartScreen reputation; enterprise admins may submit files to the Microsoft Security
Intelligence portal to accelerate trust for managed deployments ([ss]).

**Smart App Control.** Blocks malware, PUA and unknown unsigned code; allows what the app
intelligence predicts safe or what is signed by a Trusted Root Program CA; evaluation vs
enforcement mode; no per-app bypass ([sac]; [sac-support]). Developers can test with the SAC
audit policy ([SAC testing][sac-test]).

**Microsoft Defender false positives.** File submission portal
<https://www.microsoft.com/en-us/wdsi/filesubmission>: roles home user / enterprise customer /
software developer, categories include "incorrect malware detection (false positive)", max
50 MB per file, Microsoft account recommended ([wdsi]). Submit the exact file hashes of a
release if Defender flags them; it does not create SmartScreen or SAC reputation.

**Microsoft Store (MSIX).** Developer registration is now **free** for individuals and
companies via storedeveloper.microsoft.com ([Store account][store]). For MSIX the Store
re-signs the package with a Microsoft certificate: no SmartScreen warning, and the result is
Microsoft-signed, which SAC accepts ([cso]; [ss]). MSI/EXE submitted to the Store must already
be signed by a Trusted Root Program CA. Fit for TokenVault: poor without redesign - each team
installer carries a credential, the Manager needs firewall rules and runs a LAN server, and
Store listings are public (private/hidden distribution options **unverified** here).

**App Control for Business (WDAC) / AppLocker for managed fleets.** IT can allow files by
**Hash**, **FilePath** (admin-writable paths only), **FileName/OriginalFileName**,
**Publisher/FilePublisher/SignedVersion** (signed files), with *Managed Installer* and
*Intelligent Security Graph* options ([App Control rules][wdac]). Hash rules use the
Authenticode/PE hash, so a hash allow-list keeps working for an unsigned build until the
next rebuild. Free, deployable with Intune/Group Policy, but IT owns it. Note: signer rules
need RSA certificates. SAC is a consumer feature; on managed PCs the company policy is what
counts. Self-signed/internal-CA certificates are legitimate **only** inside such a managed
fleet where IT deploys the trust ([cso], "Enterprise internal distribution") - never as a
fix for unmanaged PCs.

## 5. Cost / benefit

| Option | Cost (2026-11) | Who can get it | SAC (unmanaged Win 11) | SmartScreen | Effort |
|---|---|---|---|---|---|
| No-exe portable package (PSF-signed python.exe) | free | anyone | runs, **but** unsigned wheel `.pyd` files may still be blocked (not verified on a real SAC block) | no exe to warn about | exists today |
| Hand files over via share/USB (no Mark-of-the-Web) | free | anyone | no effect | no prompt | process only |
| App Control / Intune allow-list by hash or path | free (needs Intune/AD) | company-managed PCs | n/a (company policy replaces SAC) | can be governed by policy | IT; update per release |
| OV certificate (HSM/token) | ~150-300 USD/yr + token/cloud HSM | any registered company (VN OK) | **runs** unless judged malicious | warnings until reputation builds | buy, validate, CI token |
| EV certificate | 400+ USD/yr | companies | same as OV | same as OV since 2024 | higher validation |
| Microsoft Artifact Signing | ~9.99 USD/month | orgs US/CA/EU/UK, individuals US/CA (not VN) | runs unless judged malicious | reputation builds | Azure tenant, identity validation |
| Microsoft Store (MSIX) | free account | worldwide | runs (Microsoft-signed) | no warning | large redesign; public listing |

## 6. Recommendation ladder (company deploying to its employees)

1. **Today, free:** Development releases (unsigned), distributed over a share/USB; on SAC
   machines use the **no-exe** package. Verify downloads with `SHA256SUMS.txt`.
2. **Company-managed PCs:** ask IT to allow the release by **hash** (or a FilePath rule for an
   admin-writable install folder) through App Control for Business/Intune or AppLocker. Free,
   no certificate; must be refreshed for every release (the report lists every hash).
3. **Mixed/unmanaged Windows 11 PCs with SAC on:** buy an **OV code-signing certificate**
   (HSM/cloud key), run `release.ps1 -Mode Production`, sign every first-party file and every
   unsigned redistributed binary (the policy already does both). Expect SmartScreen prompts
   for the first releases; keep the same signing identity so reputation can build.
4. **If the company is (or has a legal entity) in the US/Canada/EU/UK:**
   prefer Microsoft Artifact Signing over OV (cheaper, no token, CI-friendly).
5. **Before signing team installers:** change the adapter installer to *signed generic
   installer + separate config file*; until then per-team exe installers stay unsigned.
6. **If Defender flags a release:** submit the files at the WDSI portal as a software
   developer; never work around it.

None of these options guarantees that a new release passes SmartScreen or SAC immediately;
the free no-exe route remains available.

[sac]: https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/overview
[sac-test]: https://learn.microsoft.com/windows/apps/develop/smart-app-control/test-your-app-with-smart-app-control
[sac-support]: https://support.microsoft.com/en-us/topic/what-is-smart-app-control-285ea03d-fa88-4d56-882e-6698afdb7003
[cso]: https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options
[ss]: https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation
[as-int]: https://learn.microsoft.com/en-us/azure/artifact-signing/how-to-signing-integrations
[as-faq]: https://learn.microsoft.com/en-us/azure/artifact-signing/faq
[as-price]: https://azure.microsoft.com/en-us/pricing/details/artifact-signing/
[wdsi]: https://www.microsoft.com/en-us/wdsi/filesubmission
[store]: https://learn.microsoft.com/en-us/windows/apps/publish/partner-center/open-a-developer-account
[wdac]: https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/design/select-types-of-rules-to-create
