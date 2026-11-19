"""Vietnamese report strings and number/date formatting (one place, so English can follow)."""

from __future__ import annotations

from datetime import date, datetime

WORDMARK = "TOKENVAULT"
TITLE = "Báo cáo sử dụng token Claude"
PDF_SUBJECT = "Báo cáo sử dụng token Claude (chi phí ước tính theo giá API)"

SCOPE_ALL = "Toàn bộ nhân viên"
SCOPE_TEAM = "Nhóm"
SCOPE_EMPLOYEES = "{n} nhân viên"
SCOPE_FILE = {"all": "TatCa", "employee": "NhanVien", "team": "Nhom"}

RANGE_TODAY = "hôm nay"
RANGE_LAST = "{n} ngày gần nhất"
RANGE_CUSTOM = "tuỳ chọn"

LBL_SCOPE = "Phạm vi"
LBL_RANGE = "Khoảng thời gian"
LBL_GENERATED = "Tạo lúc"
LBL_MANAGER = "Người quản lý"

KPI_TOKENS = "Tổng token"
KPI_COST = "Chi phí ước tính"
KPI_SESSIONS = "Phiên làm việc"
KPI_EMPLOYEES = "Nhân viên hoạt động"
KPI_BUSIEST = "Ngày cao điểm"
KPI_TOP_PROJECT = "Dự án nhiều token nhất"
KPI_DEVICES = "Thiết bị"
BREAKDOWN = "Vào {i} · Ra {o} · Cache đọc {cr} · Cache ghi {cw}"
COST_DISCLAIMER = "Chi phí ước tính theo giá API, không phải hoá đơn thực tế"
COST_UNKNOWN = "KHÔNG RÕ"
COST_PARTIAL = "một phần"
COST_PARTIAL_NOTE = "Chưa có giá cho: {models}"
COST_UNKNOWN_NOTE = "Không có model nào có giá: {models}"
SESSIONS_SUB = "{events} lượt phản hồi"
EMPLOYEES_SUB = "trên {n} tài khoản"
ONLINE_SUB = "{n} người đang trực tuyến"
NONE = "—"

SEC_OVERVIEW = "Tổng quan"
SEC_CHARTS = "Biểu đồ"
SEC_EMPLOYEES = "Xếp hạng nhân viên"
SEC_PROJECTS = "Dự án"
SEC_SESSIONS = "Phiên nhiều token nhất"
SEC_EMPLOYEE_DETAIL = "Chi tiết nhân viên"
SEC_ABOUT = "Về báo cáo này"

CH_DAILY = "Token theo ngày"
CH_PROJECT = "Token theo dự án"
CH_MODEL = "Token theo model"
CH_TOOLS = "Công cụ dùng nhiều nhất (số lượt)"
CH_HOURS = "Token theo giờ · ngày cao điểm {day}"
CH_HOURS_EMPTY = "Token theo giờ trong ngày"
CH_EMPLOYEES = "Token theo nhân viên (top 10)"
CH_STACKED = "Token theo ngày, theo nhân viên"
OTHER = "Khác"
EMPTY = "Chưa có dữ liệu trong khoảng này"
NO_DATA_SCOPE = "Không có dữ liệu trong khoảng thời gian này cho phạm vi đã chọn."

COL_ACCOUNT = "Tài khoản Claude"
COL_TEAM = "Nhóm"
COL_TOKENS = "Token"
COL_COST = "Chi phí ước tính"
COL_COST_SHORT = "Chi phí"
COL_SESSIONS = "Phiên"
COL_SHARE = "Tỉ trọng"
COL_PROJECT = "Dự án"
COL_EMPLOYEES = "Nhân viên"
COL_TITLE = "Tiêu đề"
COL_EMPLOYEE = "Nhân viên"
COL_MODEL = "Model"
COL_STARTED = "Bắt đầu"
TOOLS_APPROX = "Thống kê công cụ mang tính gần đúng (gộp từ top công cụ của từng nhân viên)."
SESSIONS_WINDOW = "trong {n} phiên gần nhất"
MORE_PEOPLE = "và {n} người khác"
MORE_ROWS = "và {n} mục khác"
TITLES_HIDDEN = "Tiêu đề phiên đã được ẩn theo lựa chọn khi xuất báo cáo."

DEV_ONLINE = "đang trực tuyến"
DEV_LAST_SEEN = "lần cuối {when}"
DEV_NEVER = "chưa kết nối"
DEVICES_NONE = "Chưa có thiết bị"

FOOTER = "TokenVault · Báo cáo tạo lúc {when} · Trang {page}/{total}"

ABOUT_LINES = (
    "Báo cáo chỉ chứa số liệu sử dụng: số token (vào, ra, cache), model, tên dự án "
    "(tên thư mục cuối), tên công cụ, thời điểm và tài khoản Claude của từng nhân viên.",
    "Báo cáo KHÔNG chứa nội dung câu lệnh (prompt), câu trả lời của Claude, nội dung tệp "
    "hay đường dẫn đầy đủ.",
    "Tiêu đề phiên do Claude Code tự đặt từ nội dung hội thoại; chúng chỉ xuất hiện khi "
    "người xuất báo cáo chọn đưa vào. {titles}",
    "Chi phí là ước tính theo bảng giá API công khai, không phải hoá đơn thực tế. "
    "“KHÔNG RÕ” nghĩa là model chưa có giá; dấu “+” nghĩa là chỉ tính "
    "được một phần.",
)
TITLES_INCLUDED = "Báo cáo này CÓ đưa tiêu đề phiên vào."
TITLES_EXCLUDED = "Báo cáo này KHÔNG đưa tiêu đề phiên vào."


def fmt_int(n: float | int) -> str:
    """Full number with Vietnamese thousands dots: 1234567 -> '1.234.567'."""
    return f"{round(n):,}".replace(",", ".")


def fmt_compact(n: float | int) -> str:
    """Abbreviated number like the UI: 1234567 -> '1.23M', 340000 -> '340K', 999999 -> '1M'."""
    units = ((1e3, "K"), (1e6, "M"), (1e9, "B"))
    if abs(n) < 1e3 and round(abs(n)) < 1000:
        return str(round(n))
    for idx, (size, suffix) in enumerate(units):
        if abs(n) >= size * 1000 and idx + 1 < len(units):
            continue
        scaled = n / size
        digits = 0 if abs(scaled) >= 100 else 1 if abs(scaled) >= 10 else 2
        if abs(round(scaled, digits)) >= 1000 and idx + 1 < len(units):
            continue  # 999.999K rounds to 1000K: roll over to the next unit
        text = f"{scaled:.{digits}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text + suffix
    return str(round(n))  # unreachable


def fmt_cost(cost: float | None, partial: bool = False) -> str:
    """USD estimate like the UI: '$12.34', '<$0.01', '$1.2K'; None -> 'KHÔNG RÕ'; '+' if partial."""
    if cost is None:
        return COST_UNKNOWN
    if abs(round(cost, 2)) >= 1000:  # 999.999 must not print as $1000.00
        text = f"${fmt_compact(cost)}"
    elif 0 < cost < 0.01:
        text = "<$0.01"
    else:
        text = f"${cost:.2f}"
    return text + "+" if partial else text


def fmt_pct(fraction: float) -> str:
    p = fraction * 100
    if p <= 0:
        return "0%"
    if p < 1:
        return "<1%"
    return f"{p:.1f}%".replace(".", ",") if p < 10 else f"{round(p)}%"


def fmt_day(day: str | date) -> str:
    """'2026-11-19' -> '19/11/2026'."""
    d = date.fromisoformat(day) if isinstance(day, str) else day
    return d.strftime("%d/%m/%Y")


def fmt_day_short(day: str) -> str:
    """'2026-11-19' -> '19/11'."""
    return date.fromisoformat(day).strftime("%d/%m")


def fmt_dt(value: datetime | str | None, short: bool = False) -> str:
    """Datetime or ISO string (already local) -> '19/11/2026 19:05' ('19/11 19:05' if short)."""
    if value is None or value == "":
        return NONE
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    return dt.strftime("%d/%m %H:%M" if short else "%d/%m/%Y %H:%M")
