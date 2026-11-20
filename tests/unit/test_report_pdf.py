from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from pypdf import PdfReader
from report_testkit import demo_dataset, email_of, ev, make_agg, make_store, put, write_pricing

from tokenvault.manager.aggregates import Aggregates
from tokenvault.manager.reports import (
    PAGE_CAP,
    ReportEmptyScopeError,
    ReportError,
    ReportFontError,
    ReportSpec,
    ReportSpecError,
    build_report,
    fonts,
    sections,
)
from tokenvault.manager.store import Store

NOW = datetime(2026, 11, 19, 19, 5)
DISCLAIMER = "Chi phí ước tính theo giá API, không phải hoá đơn thực tế"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = make_store(tmp_path)
    yield s
    s.close()


@pytest.fixture
def agg(store: Store, tmp_path: Path) -> Aggregates:
    return make_agg(store, write_pricing(tmp_path))


def text_of(path: Path) -> list[str]:
    return [p.extract_text() or "" for p in PdfReader(str(path)).pages]


def flat(pages: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(pages))


def build(agg: Aggregates, spec: ReportSpec, out: Path, **kw: object):  # type: ignore[no-untyped-def]
    return build_report(agg, spec, out, now=NOW, manager_name="Trần Quản Lý", **kw)  # type: ignore[arg-type]


def test_all_scope_report_text_footer_and_metadata(
    store: Store, agg: Aggregates, tmp_path: Path
) -> None:
    demo_dataset(store, employees=5, days=10)
    out = tmp_path / "deep" / "folder" / "r.pdf"
    res = build(agg, ReportSpec(range="7d"), out)
    assert res.path == out and out.is_file() and res.bytes == out.stat().st_size
    pages = text_of(out)
    assert res.pages == len(pages) >= 1
    body = flat(pages)
    for s in (
        "Báo cáo sử dụng token Claude",
        "Toàn bộ nhân viên",
        DISCLAIMER,
        email_of(0),
        "13/11/2026 – 19/11/2026",
        "Trần Quản Lý",
        "Xếp hạng nhân viên",
        "Về báo cáo này",
    ):
        assert s in body, s
    assert f"Trang 1/{res.pages}" in pages[0]
    assert f"Trang {res.pages}/{res.pages}" in pages[-1]
    assert "TokenVault · Báo cáo tạo lúc 19/11/2026 19:05" in pages[0]
    assert "không chứa".upper() in body.upper() and "prompt" in body  # privacy note, last page
    assert "Về báo cáo này" in pages[-1]
    meta = PdfReader(str(out)).metadata
    assert meta is not None and meta.author == "TokenVault"
    assert meta.get("/Producer") == "TokenVault" and meta.get("/Creator") == "TokenVault"
    assert str(meta.get("/CreationDate")).startswith("D:20261119190500+07")
    raw = out.read_bytes()
    for leak in (str(tmp_path).encode(), b"Users", b"http://", b"https://", b"reportlab.com"):
        assert leak not in raw
    assert res.scope_label == "Toàn bộ nhân viên" and "19/11/2026" in res.range_label


def test_employee_team_and_custom_range(store: Store, agg: Aggregates, tmp_path: Path) -> None:
    demo_dataset(store, employees=6, days=40)
    one = build(agg, ReportSpec(scope="employee", employee_keys=[email_of(2)]), tmp_path / "e.pdf")
    body = flat(text_of(one.path))
    assert email_of(2) in body and "Xếp hạng nhân viên" not in body
    two = build(
        agg,
        ReportSpec(scope="employee", employee_keys=[email_of(1), email_of(3)]),
        tmp_path / "e2.pdf",
    )
    two_text = flat(text_of(two.path))
    assert two.pages > one.pages and email_of(3) in two_text
    assert "Thống kê công cụ mang tính gần đúng" in two_text
    assert "gần đúng" not in flat(text_of(one.path))
    team = build(agg, ReportSpec(scope="team", team="Data", range="30d"), tmp_path / "t.pdf")
    tb = flat(text_of(team.path))
    assert "Nhóm Data" in tb and email_of(2) in tb and email_of(0) not in tb
    custom = build(
        agg, ReportSpec(range="custom", start="2026-10-01", end="2026-10-31"), tmp_path / "c.pdf"
    )
    assert "01/10/2026 – 31/10/2026" in flat(text_of(custom.path))


def test_per_employee_pages_add_pages(store: Store, agg: Aggregates, tmp_path: Path) -> None:
    demo_dataset(store, employees=6, days=10)
    with_ = build(agg, ReportSpec(per_employee_pages=True), tmp_path / "a.pdf")
    without = build(
        agg, ReportSpec(per_employee_pages=False, include_charts=False), tmp_path / "b.pdf"
    )
    assert with_.pages >= without.pages + 6


def test_unpriced_models_show_unknown(store: Store, agg: Aggregates, tmp_path: Path) -> None:
    put(store, ev(1, model="claude-mystery-9"), ev(2, model=None))
    res = build(agg, ReportSpec(range="today"), tmp_path / "u.pdf")
    body = flat(text_of(res.path))
    assert "KHÔNG RÕ" in body and "claude-mystery-9" in body and DISCLAIMER in body
    put(store, ev(3, model="claude-sonnet-4-6"))
    part = flat(text_of(build(agg, ReportSpec(range="today"), tmp_path / "p.pdf").path))
    assert "$3.00+" in part and "một phần" in part


def test_markup_injection_renders_literally(store: Store, agg: Aggregates, tmp_path: Path) -> None:
    evil = '<b>bold</b> <img src="x"/> &amp; <font color="red">x</font> ' + "W" * 300
    store.upsert_device("dev1", "<i>Đội</i>", "<b>who</b>@x.com", 0, "alice", "PC1")
    put(
        store,
        ev(
            1,
            session_title=evil,
            project="<para>p&q",
            claude_account_email="<b>who</b>@x.com",
            team="<i>Đội</i>",
        ),
        ev(2, session_id="s2", session_title="مرحبا 😀 שלום ‮ evil", project="rtl"),
    )
    res = build(agg, ReportSpec(range="today"), tmp_path / "x.pdf")
    body = flat(text_of(res.path))
    assert "<b>bold</b>" in body and "<b>who</b>@x.com" in body and "<para>p&q" in body
    assert "<img" in body and "&amp;" in body  # literal text, nothing fetched or decoded
    assert b"/URI" not in res.path.read_bytes()
    hidden = build(agg, ReportSpec(range="today", include_session_titles=False), tmp_path / "h.pdf")
    assert "<b>bold</b>" not in flat(text_of(hidden.path))


def test_atomic_write_leaves_nothing_on_failure(
    store: Store, agg: Aggregates, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo_dataset(store, employees=3, days=5)
    folder = tmp_path / "out"
    folder.mkdir()
    final = folder / "r.pdf"

    def boom(_data: object) -> list[object]:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(sections, "about", boom)
    with pytest.raises(ReportError, match="disk on fire"):
        build(agg, ReportSpec(), final)
    assert list(folder.iterdir()) == []
    monkeypatch.undo()

    def stop(frac: float, _msg: str) -> None:
        if frac > 0.7:
            raise KeyboardInterrupt  # mid-layout abort (not an Exception subclass)

    with pytest.raises(KeyboardInterrupt):
        build(agg, ReportSpec(), final, progress=stop)
    assert list(folder.iterdir()) == []
    final.write_bytes(b"old")  # an existing file is only replaced by a complete PDF
    with pytest.raises(ReportEmptyScopeError):
        build(agg, ReportSpec(scope="employee", employee_keys=["ghost@x.com"]), final)
    assert final.read_bytes() == b"old" and len(list(folder.iterdir())) == 1


def test_errors_are_report_errors(
    store: Store, agg: Aggregates, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(store, ev(1))
    with pytest.raises(ReportSpecError):
        build(agg, ReportSpec(top_n=0), tmp_path / "a.pdf")
    with pytest.raises(ReportSpecError):
        build(agg, ReportSpec(), tmp_path / "a.pdf", tz_offset_minutes=0)
    with pytest.raises(ReportEmptyScopeError):
        build(agg, ReportSpec(scope="team", team="Nope"), tmp_path / "a.pdf")

    def no_font() -> None:
        raise ReportFontError("no font")

    monkeypatch.setattr(fonts, "ensure_fonts", no_font)
    with pytest.raises(ReportFontError):
        build(agg, ReportSpec(), tmp_path / "a.pdf")
    assert not (tmp_path / "a.pdf").exists()


def test_empty_scope_report(store: Store, agg: Aggregates, tmp_path: Path) -> None:
    store.upsert_device("dev9", "Ops", "idle@x.com", 0, "idle", "PC9")
    res = build(agg, ReportSpec(scope="employee", employee_keys=["idle@x.com"]), tmp_path / "e.pdf")
    body = flat(text_of(res.path))
    assert "Không có dữ liệu" in body and "Chưa có dữ liệu trong khoảng này" in body
    assert "idle@x.com" in body and res.pages >= 1


def test_sixty_employees_progress_and_page_cap(
    store: Store, agg: Aggregates, tmp_path: Path
) -> None:
    demo_dataset(store, employees=64, days=4, events_per_day=1)
    seen: list[float] = []
    res = build(
        agg, ReportSpec(range="7d"), tmp_path / "big.pdf", progress=lambda f, _m: seen.append(f)
    )
    assert 60 < res.pages <= PAGE_CAP
    assert "và 4 người khác" in flat(text_of(res.path))
    assert seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen)


def _peak_rss_mb() -> float | None:
    import sys

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
            ] + [(n, ctypes.c_size_t) for n in ("a", "b", "c", "d", "e", "f")]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return pmc.PeakWorkingSetSize / 2**20
        return None
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def test_moderate_dataset_builds_quickly(store: Store, agg: Aggregates, tmp_path: Path) -> None:
    import time

    demo_dataset(store, employees=12, days=30, events_per_day=2, session_every=3)
    t = time.perf_counter()
    res = build(agg, ReportSpec(range="30d"), tmp_path / "m.pdf")
    assert time.perf_counter() - t < 6 and res.pages > 12


@pytest.mark.slow
def test_performance_50_employees_90_days(store: Store, agg: Aggregates, tmp_path: Path) -> None:
    import time

    # 50 employees x 90 days, ~18 sessions each, ~15k events
    demo_dataset(store, employees=50, days=90, events_per_day=4, session_every=5)
    t = time.perf_counter()
    res = build(
        agg, ReportSpec(range="custom", start="2026-08-22", end="2026-11-19"), tmp_path / "perf.pdf"
    )
    took = time.perf_counter() - t
    peak = _peak_rss_mb()
    print(f"\nperf: {took:.2f}s, {res.pages} pages, {res.bytes / 1e6:.2f} MB, peak RSS {peak} MB")
    assert took < 15 and res.pages >= 50
    assert peak is None or peak < 300


ARTIFACTS = Path(__file__).resolve().parents[2] / "tests" / "_artifacts" / "reports"


def _render(pdf_path: Path, prefix: str, pages: list[int]) -> list[Path]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    out = []
    try:
        for i in pages:
            idx = i if i >= 0 else len(pdf) + i
            png = ARTIFACTS / f"{prefix}-p{idx + 1}.png"
            pdf[idx].render(scale=1.5).to_pil().save(png)
            out.append(png)
    finally:
        pdf.close()
    return out


def test_visual_artifacts(store: Store, agg: Aggregates, tmp_path: Path) -> None:
    """Renders the QA set to tests/_artifacts/reports (git-ignored) for a human look."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    demo_dataset(store, employees=14, days=30, unpriced=True)
    store.upsert_device("dev-idle", "Ops", "idle@congty.vn", 0, "idle", "PC-IDLE")
    put(
        store,
        ev(
            "evil",
            device_id="dev-x",
            claude_account_email="<b>x</b>@congty.vn",
            session_title='<b>bold</b> <img src="x"/> &amp; ' + "W" * 120,
            timestamp="2026-11-19T03:00:00Z",
            project="<para>",
        ),
    )
    a = build(agg, ReportSpec(range="30d"), ARTIFACTS / "sample-all.pdf")
    e = build(
        agg,
        ReportSpec(scope="employee", employee_keys=[email_of(1)], range="30d"),
        ARTIFACTS / "sample-employee.pdf",
    )
    z = build(
        agg,
        ReportSpec(scope="employee", employee_keys=["idle@congty.vn"]),
        ARTIFACTS / "empty-scope.pdf",
    )
    pngs = _render(a.path, "all", [0, 1, 2, 3, -1]) + _render(e.path, "employee", [0, 1])
    pngs += _render(z.path, "empty", [0])
    (tmp_path / "u").mkdir()
    s2 = make_store(tmp_path / "u")
    put(s2, ev(1, model="claude-mystery-9"), ev(2, model=None, session_id="s2"))
    u = build(
        make_agg(s2, write_pricing(tmp_path)), ReportSpec(range="today"), ARTIFACTS / "unpriced.pdf"
    )
    s2.close()
    pngs += _render(u.path, "unpriced", [0])
    assert all(p.stat().st_size > 10_000 for p in pngs)


def test_device_lines_never_show_raw_device_ids() -> None:
    from tokenvault.manager.agg_models import DeviceInfo

    lines = sections._device_lines([DeviceInfo("dev-secret-id", "", "", "", False, None)])
    assert "dev-secret-id" not in lines[0] and lines[0].startswith("—")


def _image_count(path: Path) -> int:
    return len(list(PdfReader(str(path)).pages[0].images))


def test_cover_carries_the_logo_image(store: Store, agg: Aggregates, tmp_path: Path) -> None:
    demo_dataset(store, employees=3, days=5)
    out = tmp_path / "logo.pdf"
    build(agg, ReportSpec(range="7d"), out)
    assert _image_count(out) >= 1


def test_report_still_builds_without_the_logo(
    store: Store, agg: Aggregates, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenvault.common import brand

    monkeypatch.setattr(brand, "logo_dir", lambda: tmp_path / "nope")
    demo_dataset(store, employees=3, days=5)
    out = tmp_path / "nologo.pdf"
    res = build(agg, ReportSpec(range="7d"), out)
    assert out.is_file() and res.pages >= 1
    assert _image_count(out) == 0
    assert "TOKENVAULT" in flat(text_of(out)).upper()
