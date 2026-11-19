"""Every employee-visible Adapter string, Vietnamese by default, English fallback.

Language: ``--lang en`` / ``TOKENVAULT_LANG=en``; anything else means Vietnamese.
Wire-field names themselves (``session_title`` ...) stay untranslated on purpose:
they are exactly what is sent.
"""

from __future__ import annotations

import os

LANGS = ("vi", "en")
DEFAULT_LANG = "vi"
_lang: str | None = None


def set_lang(lang: str | None) -> None:
    global _lang
    _lang = lang if lang in LANGS else None


def get_lang() -> str:
    if _lang is not None:
        return _lang
    env = os.environ.get("TOKENVAULT_LANG", "").strip().lower()
    return env if env in LANGS else DEFAULT_LANG


def t(key: str, **kw: object) -> str:
    text = STRINGS[key][get_lang()]
    return text.format(**kw) if kw else text


def field_note(name: str) -> str | None:
    note = FIELD_NOTES.get(name)
    return note[get_lang()] if note else None


_TOKENS = {"vi": "số token", "en": "token count"}

FIELD_NOTES: dict[str, dict[str, str]] = {
    "uuid": {"vi": "mã định danh của dòng nhật ký", "en": "id of the log line"},
    "session_id": {"vi": "mã phiên làm việc Claude Code", "en": "id of the Claude Code session"},
    "message_id": {"vi": "mã câu trả lời của Claude", "en": "id of the Claude response"},
    "timestamp": {"vi": "thời điểm câu trả lời được ghi", "en": "when the response was written"},
    "model": {"vi": "tên mô hình, ví dụ claude-sonnet", "en": "model name, e.g. claude-sonnet"},
    "reasoning_effort": {"vi": "mức độ suy luận (low / medium / high)",
                         "en": "effort setting (low / medium / high)"},
    "is_sidechain": {"vi": "câu trả lời có do tác tử phụ (sub-agent) tạo ra hay không",
                     "en": "whether a sub-agent produced it"},
    "input_tokens": _TOKENS,
    "output_tokens": _TOKENS,
    "cache_read_tokens": _TOKENS,
    "cache_create_5m_tokens": _TOKENS,
    "cache_create_1h_tokens": _TOKENS,
    "project": {
        "vi": "CHỈ tên thư mục cuối cùng của thư mục làm việc (không phải đường dẫn đầy đủ)",
        "en": "LAST folder name of the working folder only (not the full path)",
    },
    "session_title": {
        "vi": "tiêu đề ngắn của phiên do Claude Code tự tạo (xem lưu ý bên dưới)",
        "en": "the short session title Claude Code generates (see note below)",
    },
    "tool_names": {
        "vi": "tên các công cụ đã dùng (ví dụ Read, Bash), không bao giờ gửi dữ liệu vào/ra "
              "của chúng",
        "en": "names of the tools used (e.g. Read, Bash), never their inputs or outputs",
    },
    "claude_account_email": {"vi": "email tài khoản Claude của bạn (từ ~/.claude.json)",
                             "en": "your Claude account e-mail (from ~/.claude.json)"},
    "account_uuid": {"vi": "mã tài khoản Claude của bạn", "en": "your Claude account id"},
    "windows_user": {"vi": "tên người dùng Windows của bạn", "en": "your Windows user name"},
    "hostname": {"vi": "tên máy tính này", "en": "this computer's name"},
    "team": {"vi": "nhóm do quản lý của bạn chọn cho bộ cài đặt này",
             "en": "team chosen by your manager for this installer"},
    "device_id": {"vi": "tên máy tính + người dùng Windows", "en": "computer name + Windows user"},
}

STRINGS: dict[str, dict[str, str]] = {
    # ---------------------------------------------------------------- consent
    "consent.title": {"vi": "TokenVault Adapter – dữ liệu được thu thập và gửi đi",
                      "en": "TokenVault Adapter - what is collected and sent"},
    "consent.intro": {
        "vi": "Với mỗi câu trả lời Claude Code ghi trên máy tính này, chính xác các trường sau "
              "được gửi đi:",
        "en": "For every response Claude Code writes on this computer, exactly these fields "
              "are sent:",
    },
    "consent.not_sent": {
        "vi": "KHÔNG gửi: câu lệnh (prompt) của bạn, câu trả lời của Claude, dữ liệu vào/ra của "
              "công cụ, nội dung tệp, lệnh đã chạy, hay bất kỳ đường dẫn thư mục đầy đủ nào "
              "(chỉ tên thư mục cuối cùng).",
        "en": "NOT sent: your prompts, Claude's replies, tool inputs or outputs, file contents, "
              "commands, or any full folder path (only the last folder name).",
    },
    "consent.title_note": {
        "vi": "Lưu ý: session_title là tiêu đề ngắn mà chính Claude Code viết cho mỗi phiên "
              "(bản ghi \"ai-title\"). Tiêu đề này được tạo từ nội dung cuộc trò chuyện của bạn, "
              "và nó CÓ được gửi đi.",
        "en": "Note: session_title is the short title Claude Code itself writes for each session "
              "(the \"ai-title\" record). It is derived from your conversation text, and it IS "
              "sent.",
    },
    "consent.who": {
        "vi": "Ai xem được: chỉ TokenVault Manager tại {who}, qua mạng nội bộ của công ty (có mã "
              "hoá). Không có gì được gửi ra Internet hay dịch vụ đám mây.",
        "en": "Who sees it: only the TokenVault Manager at {who}, over the company network "
              "(encrypted). Nothing goes to the internet or any cloud.",
    },
    "consent.who_default": {"vi": "máy Manager của công ty bạn",
                            "en": "your company's Manager PC"},
    "consent.tray_note": {
        "vi": "Adapter chạy nền với một biểu tượng ở khay hệ thống. Mục \"Dữ liệu gửi đi là "
              "gì?\" trong menu khay hiển thị một sự kiện thật đang chờ gửi.",
        "en": "The Adapter runs in the background with a tray icon. 'What is sent?' in the tray "
              "menu shows a real queued event.",
    },
    "consent.remove": {
        "vi": "Gỡ cài đặt: Cài đặt Windows > Ứng dụng (Apps & Features) > TokenVault Adapter > "
              "Gỡ cài đặt.",
        "en": "To remove it: Windows Settings > Apps (Apps & Features) > TokenVault Adapter > "
              "Uninstall.",
    },
    "consent.window_title": {"vi": "TokenVault Adapter – đồng ý thu thập dữ liệu",
                             "en": "TokenVault Adapter - consent"},
    "consent.accept": {"vi": "Đồng ý (Alt+A)", "en": "Accept (Alt+A)"},
    "consent.decline": {"vi": "Từ chối (Alt+D)", "en": "Decline (Alt+D)"},
    # ------------------------------------------------------------------- tray
    "state.connected": {"vi": "Đã kết nối", "en": "Connected"},
    "state.offline": {"vi": "Mất kết nối", "en": "Offline"},
    "state.rejected": {"vi": "Bị từ chối", "en": "Rejected"},
    "state.connecting": {"vi": "Đang kết nối", "en": "Connecting"},
    "state.not_configured": {"vi": "Chưa cấu hình", "en": "Not configured"},
    "state.stopped": {"vi": "Đã dừng", "en": "Stopped"},
    "state.no_indicator": {"vi": "Không hiện được biểu tượng – đã dừng thu thập",
                           "en": "No indicator - collection stopped"},
    "state.crashed": {"vi": "Bị lỗi khi khởi động", "en": "Crashed at start"},
    "tray.status": {"vi": "{state} – {n} đang chờ gửi", "en": "{state} - {n} queued"},
    "tray.status_unavailable": {"vi": "Không đọc được trạng thái", "en": "Status unavailable"},
    "tray.what": {"vi": "Dữ liệu gửi đi là gì?", "en": "What is sent?"},
    "tray.open_log": {"vi": "Mở thư mục nhật ký", "en": "Open log folder"},
    "tray.about": {"vi": "Giới thiệu", "en": "About"},
    "tray.tooltip": {"vi": "TokenVault Adapter – {status}", "en": "TokenVault Adapter - {status}"},
    # -------------------------------------------------------- what is sent
    "wis.window_title": {"vi": "TokenVault – Dữ liệu gửi đi là gì?",
                         "en": "TokenVault - What is sent?"},
    "wis.none": {
        "vi": "Hiện không có sự kiện nào đang chờ gửi (mọi thứ đã được gửi, hoặc Claude Code chưa "
              "được dùng). Hãy chạy lại 'status --json' sau để xem một sự kiện.",
        "en": "No events are queued right now (everything was delivered, or Claude Code has not "
              "been used yet). Run 'status --json' later to see one.",
    },
    "wis.one": {"vi": "Một sự kiện đang chờ, đúng như khi được gửi đi:",
                "en": "One queued event, exactly as it will be sent:"},
    "common.close": {"vi": "Đóng", "en": "Close"},
    # ----------------------------------------------------------------- status
    "status.device": {"vi": "Mã thiết bị:", "en": "Device id:"},
    "status.account": {"vi": "Tài khoản:", "en": "Account:"},
    "status.data_dir": {"vi": "Thư mục dữ liệu:", "en": "Data dir:"},
    "status.team": {"vi": "Nhóm:", "en": "Team:"},
    "status.not_setup": {"vi": "(chưa cài đặt – hãy chạy bộ cài đặt)",
                         "en": "(not set up - run the installer)"},
    "status.manager": {"vi": "Manager:", "en": "Manager:"},
    "status.enrolled": {"vi": "Đăng ký:", "en": "Enrolled:"},
    "status.key_ok": {"vi": "đã đăng ký", "en": "yes"},
    "status.key_missing": {"vi": "chưa đăng ký", "en": "not yet"},
    "status.key_unreadable": {"vi": "chưa đăng ký (không đọc được khoá)",
                              "en": "not enrolled (key unreadable)"},
    "status.agent": {"vi": "Tác tử nền:", "en": "Agent:"},
    "status.running": {"vi": "đang chạy", "en": "running"},
    "status.not_running": {"vi": "không chạy", "en": "not running"},
    "status.connection": {"vi": "Kết nối:", "en": "Connection:"},
    "status.unknown": {"vi": "không rõ", "en": "unknown"},
    "status.outbox": {"vi": "Hàng đợi:", "en": "Outbox:"},
    "status.outbox_line": {"vi": "{queued} sự kiện, {delivered} đã gửi, {waiting} đang chờ",
                           "en": "{queued} queued, {delivered} delivered, {waiting} waiting"},
    "status.reset_needed": {
        "vi": "Máy này đã được đăng ký với Manager trước đó nhưng khoá thiết bị đã mất (ví dụ "
              "sau khi cài lại Windows). Hãy nhờ chủ Manager mở Cài đặt > Máy tính và chọn "
              "\"Đặt lại thiết bị\" cho máy này, rồi mở lại TokenVault Adapter.",
        "en": "This computer was enrolled with the Manager before but its device key is gone "
              "(for example after a Windows reinstall). Ask the Manager owner to open "
              "Settings > Devices and choose \"Reset device\" for this computer, then "
              "restart TokenVault Adapter.",
    },
    "status.hint": {"vi": "Chạy 'status --json' để xem một sự kiện đúng như khi được gửi đi.",
                    "en": "Run 'status --json' to see one queued event exactly as it is sent."},
    # ------------------------------------------------------------------ about
    "about.window_title": {"vi": "Giới thiệu TokenVault Adapter",
                           "en": "About TokenVault Adapter"},
    "about.title": {"vi": "TokenVault Adapter {version}", "en": "TokenVault Adapter {version}"},
    "about.desc": {
        "vi": "Báo cáo mức dùng token của Claude Code (chỉ số đếm và siêu dữ liệu) cho TokenVault "
              "Manager của công ty. Dùng \"Dữ liệu gửi đi là gì?\" để xem chính xác những gì rời "
              "khỏi máy này.",
        "en": "Reports Claude Code token usage (counts and metadata only) to your company's "
              "TokenVault Manager. Use 'What is sent?' to see exactly what leaves this PC.",
    },
    # ------------------------------------------------------------------ setup
    "setup.no_payload": {
        "vi": "'{name}' không chứa cấu hình TokenVault nên không thể cài đặt.\nHãy xin quản lý "
              "của bạn bộ cài đặt của nhóm (tạo bằng 'Create installer' trong TokenVault "
              "Manager) và chạy tệp đó.",
        "en": "'{name}' has no TokenVault configuration, so it cannot be set up.\nAsk your "
              "manager for your team's installer (made with 'Create installer' in the "
              "TokenVault Manager) and run that file.",
    },
    "setup.declined": {"vi": "Bạn đã từ chối: không có gì được cài đặt hoặc thay đổi.",
                       "en": "Declined: nothing was installed or changed."},
    "setup.no_ui": {
        "vi": "Không hiển thị được màn hình đồng ý (không mở được cửa sổ). Chưa có gì được cài "
              "đặt hoặc thay đổi. Hãy chạy lại bộ cài đặt trong phiên đăng nhập Windows bình "
              "thường.",
        "en": "Could not show the consent screen (no window could be opened). Nothing was "
              "installed or changed. Run the installer again from a normal Windows session.",
    },
    "setup.dpapi": {
        "vi": "Không bảo vệ được khoá bí mật bằng Windows DPAPI ({reason}). Chưa có gì được ghi.",
        "en": "Could not protect the secret with Windows DPAPI ({reason}). Nothing was written.",
    },
    "setup.old_running": {
        "vi": "Tác tử cũ chưa dừng sau {seconds} giây; cấu hình mới sẽ có hiệu lực khi nó dừng.",
        "en": "The old agent did not stop within {seconds} s; the new setup applies once it "
              "stops.",
    },
    "setup.done": {"vi": "TokenVault Adapter đã được cài đặt.",
                   "en": "TokenVault Adapter is set up."},
    "run.not_configured": {
        "vi": "Chưa có cấu hình hợp lệ trong {path}; hãy chạy bộ cài đặt.",
        "en": "No valid config in {path}; run the installer (setup).",
    },
    "run.no_indicator": {
        "vi": "TokenVault không hiện được biểu tượng trên thanh menu nên đã dừng "
              "và KHÔNG thu thập dữ liệu. Xem adapter.log.",
        "en": "TokenVault cannot show its menu-bar icon, so it stopped and is NOT "
              "collecting data. See adapter.log.",
    },
    "consent.view_full": {"vi": "Xem đầy đủ", "en": "View full text"},
    "consent.short_hint": {
        "vi": "Bấm “Xem đầy đủ” để đọc toàn bộ nội dung trước khi đồng ý.",
        "en": "Click “View full text” to read everything before you accept.",
    },
    "run.crashed": {"vi": "Tác tử bị lỗi khi khởi động: {reason}. Xem adapter.log.",
                    "en": "The agent failed to start: {reason}. See adapter.log."},
}
