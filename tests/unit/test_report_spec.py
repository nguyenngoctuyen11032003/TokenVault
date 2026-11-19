from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from tokenvault.manager.reports.errors import ReportSpecError
from tokenvault.manager.reports.spec import (
    ReportSpec,
    range_label,
    resolve_range,
    safe_file_part,
    unique_report_path,
)

OK = ReportSpec()


def test_defaults_are_valid() -> None:
    assert OK.validate() is OK
    assert ReportSpec(scope="employee", employee_keys=["a@x.com", "device:d2"]).validate()
    assert ReportSpec(scope="team", team="Dev", range="today").validate()
    assert ReportSpec(range="custom", start="2026-01-01", end="2026-12-31").validate()


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": "everyone"},
        {"scope": "employee"},
        {"scope": "employee", "employee_keys": [""]},
        {"scope": "employee", "employee_keys": ["a", "a"]},
        {"employee_keys": "a@x.com"},
        {"scope": "team"},
        {"scope": "team", "team": "   "},
        {"range": "90d"},
        {"range": "custom"},
        {"range": "custom", "start": "2026-11-01"},
        {"range": "custom", "start": "2026-11-01", "end": "2026-13-01"},
        {"range": "custom", "start": "01/11/2026", "end": "2026-11-19"},
        {"range": "custom", "start": "2026-11-19", "end": "2026-11-01"},
        {"range": "custom", "start": "2025-01-01", "end": "2026-01-02"},
        {"range": "7d", "start": "2026-11-01"},
        {"top_n": 0},
        {"top_n": 101},
        {"top_n": True},
        {"top_n": 1.5},
        {"include_charts": "yes"},
        {"per_employee_pages": None},
        {"language": "en"},
    ],
)
def test_invalid_fields_raise(changes: dict[str, object]) -> None:
    with pytest.raises(ReportSpecError):
        replace(OK, **changes).validate()  # type: ignore[arg-type]


def test_span_boundaries() -> None:
    # 366 days inclusive is allowed, 367 is not; a single day is fine
    ReportSpec(range="custom", start="2024-01-01", end="2024-12-31").validate()
    ReportSpec(range="custom", start="2026-11-19", end="2026-11-19").validate()
    with pytest.raises(ReportSpecError):
        ReportSpec(range="custom", start="2025-01-01", end="2026-01-02").validate()
    assert replace(OK, top_n=1).validate() and replace(OK, top_n=100).validate()


def test_resolve_range_and_labels() -> None:
    today = date(2026, 11, 19)
    assert resolve_range(ReportSpec(range="today"), today) == (today, today)
    assert resolve_range(ReportSpec(range="7d"), today) == (date(2026, 11, 13), today)
    assert resolve_range(ReportSpec(range="30d"), today)[0] == date(2026, 10, 21)
    c = ReportSpec(range="custom", start="2026-11-01", end="2026-11-19")
    first, last = resolve_range(c, today)
    assert range_label(c, first, last) == "01/11/2026 – 19/11/2026"
    assert "hôm nay" in range_label(ReportSpec(range="today"), today, today)


def test_safe_file_part_rules() -> None:
    assert safe_file_part("a@x.com") == "a_x_com"
    assert safe_file_part('Đội <Dev>: "QA"/\\|?*') == "Đội _Dev_ _QA"
    assert safe_file_part("...") == "BaoCao"
    assert safe_file_part("CON") == "CON_"
    assert len(safe_file_part("x" * 200)) <= 40


def test_unique_report_path_names_and_suffixes(tmp_path: Path) -> None:
    now = datetime(2026, 11, 19, 19, 5)
    p1 = unique_report_path(tmp_path, OK, now)
    assert p1.name == "TokenVault-BaoCao-TatCa-20261119-1905.pdf"
    p1.write_bytes(b"x")
    p2 = unique_report_path(tmp_path, OK, now)
    assert p2.name == "TokenVault-BaoCao-TatCa-20261119-1905 (2).pdf"
    p2.write_bytes(b"x")
    assert unique_report_path(tmp_path, OK, now).name.endswith(" (3).pdf")
    team = unique_report_path(tmp_path, ReportSpec(scope="team", team="QA/Test:1"), now)
    assert team.name == "TokenVault-BaoCao-Nhom-QA_Test_1-20261119-1905.pdf"
    one = ReportSpec(scope="employee", employee_keys=["an@corp.vn"])
    assert "NhanVien-an_corp_vn" in unique_report_path(tmp_path, one, now).name
    many = ReportSpec(scope="employee", employee_keys=["a", "b", "c"])
    assert "NhanVien-3" in unique_report_path(tmp_path, many, now).name
    for p in (team, p1):
        assert not set('<>:"/\\|?*') & set(p.name)


def test_number_and_cost_formatting_rolls_over() -> None:
    from tokenvault.manager.reports.strings_vi import fmt_compact, fmt_cost

    assert [fmt_compact(x) for x in (999, 999.6, 1234, 340_000, 999_999, 1_234_567)] == [
        "999",
        "1K",
        "1.23K",
        "340K",
        "1M",
        "1.23M",
    ]
    assert fmt_cost(999.999) == "$1K" and fmt_cost(999.994) == "$999.99"
    assert fmt_cost(0.004) == "<$0.01" and fmt_cost(None) == "KHÔNG RÕ"
    assert fmt_cost(5, partial=True) == "$5.00+"
