from __future__ import annotations

import json
import struct
import tracemalloc
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from tokenvault.common.config_payload import (
    MAGIC,
    PayloadConfig,
    append_payload,
    read_payload,
)

SECRET = "A" * 43
FP = "ab" * 32


def make_cfg(**kw: object) -> PayloadConfig:
    base = PayloadConfig(
        manager_id="mgr-1", hosts=["192.168.1.10", "pc-manager"], port=47800,
        fingerprint=FP, secret=SECRET, team="Dev",
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def make_exe(tmp_path: Path, size: int = 5000) -> Path:
    p = tmp_path / "tpl.exe"
    p.write_bytes(b"MZ" + bytes(range(256)) * (size // 256))
    return p


def raw_footer(payload: bytes, crc: int | None = None, length: int | None = None,
               magic: bytes = MAGIC) -> bytes:
    c = zlib.crc32(payload) if crc is None else crc
    n = len(payload) if length is None else length
    return payload + struct.pack(">I", c) + struct.pack(">Q", n) + magic


def good_dict() -> dict[str, object]:
    cfg = make_cfg()
    return {"manager_id": cfg.manager_id, "hosts": cfg.hosts, "port": cfg.port,
            "fingerprint": cfg.fingerprint, "secret": cfg.secret, "team": cfg.team,
            "version": 1}


def good_payload() -> bytes:
    return json.dumps(good_dict(), sort_keys=True, ensure_ascii=False).encode()


def test_payload_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "o.exe"
    cfg = make_cfg()
    append_payload(make_exe(tmp_path), out, cfg)
    assert read_payload(out) == cfg


def test_exe_without_footer_returns_none(tmp_path: Path) -> None:
    assert read_payload(make_exe(tmp_path)) is None
    empty = tmp_path / "e.exe"
    empty.write_bytes(b"")
    assert read_payload(empty) is None


def test_corrupt_length_returns_none(tmp_path: Path) -> None:
    exe = tmp_path / "x.exe"
    exe.write_bytes(b"MZ" + raw_footer(good_payload(), length=10**12))
    assert read_payload(exe) is None
    exe.write_bytes(b"MZ" + raw_footer(good_payload(), length=len(good_payload()) + 500))
    assert read_payload(exe) is None


def test_original_bytes_unchanged_prefix(tmp_path: Path) -> None:
    tpl = make_exe(tmp_path)
    out = tmp_path / "o.exe"
    append_payload(tpl, out, make_cfg())
    t, o = tpl.read_bytes(), out.read_bytes()
    assert o[: len(t)] == t and len(o) > len(t) and o[:2] == b"MZ"
    assert o.endswith(MAGIC)


def test_fake_mz_prefix_preserved(tmp_path: Path) -> None:
    tpl = tmp_path / "t.exe"
    tpl.write_bytes(b"MZ\x90\x00\x03")
    out = tmp_path / "o.exe"
    append_payload(tpl, out, make_cfg())
    assert out.read_bytes()[:5] == b"MZ\x90\x00\x03"


@pytest.mark.parametrize("variant", ["crc", "magic", "oversize", "json", "fields", "version"])
def test_bad_content_returns_none(tmp_path: Path, variant: str) -> None:
    d = good_dict()
    if variant == "crc":
        g = good_payload()
        blob = raw_footer(g, crc=zlib.crc32(g) ^ 1)
    elif variant == "magic":
        blob = raw_footer(good_payload(), magic=b"TVLTCFG2")
    elif variant == "oversize":
        blob = raw_footer(b"x" * (70 * 1024))
    elif variant == "json":
        blob = raw_footer(b"{not json")
    elif variant == "fields":
        d["port"] = 0
        blob = raw_footer(json.dumps(d).encode())
    else:
        d["version"] = 99
        blob = raw_footer(json.dumps(d).encode())
    exe = tmp_path / "b.exe"
    exe.write_bytes(b"MZ" + bytes(100) + blob)
    assert read_payload(exe) is None


def test_wrong_json_types_return_none(tmp_path: Path) -> None:
    for bad in ('[1,2]', '{"port": "x"}', '{"hosts": "a"}', "null"):
        exe = tmp_path / "t.exe"
        exe.write_bytes(b"MZ" + raw_footer(bad.encode()))
        assert read_payload(exe) is None


def test_append_twice_replaces(tmp_path: Path) -> None:
    tpl = make_exe(tmp_path)
    a, b, single = tmp_path / "a.exe", tmp_path / "b.exe", tmp_path / "s.exe"
    append_payload(tpl, a, make_cfg(team="One"))
    append_payload(a, b, make_cfg(team="Two"))
    assert read_payload(b) == make_cfg(team="Two")
    append_payload(tpl, single, make_cfg(team="Two"))
    assert b.read_bytes() == single.read_bytes()  # exactly one footer


def test_unicode_round_trip(tmp_path: Path) -> None:
    cfg = make_cfg(team="Nhóm Phát Triển Đà Nẵng", manager_id="máy-chủ_1")
    out = tmp_path / "u.exe"
    append_payload(make_exe(tmp_path), out, cfg)
    assert read_payload(out) == cfg
    assert "Nhóm Phát Triển".encode() in out.read_bytes()


@pytest.mark.parametrize("kw", [
    {"hosts": []}, {"hosts": ["bad host!"]}, {"hosts": ["a" * 254]}, {"port": 0},
    {"port": 70000}, {"fingerprint": "AB" * 32}, {"fingerprint": "ab" * 31},
    {"secret": "short"}, {"secret": "A" * 31 + "!"}, {"team": ""}, {"team": "a/b"},
    {"team": "a\\b"}, {"team": "a\x01b"}, {"team": "x" * 41}, {"manager_id": ""},
    {"manager_id": "a b"}, {"manager_id": "x" * 65},
])
def test_validate_rejects(kw: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        make_cfg(**kw).validate()


def test_validate_accepts_ipv6_and_append_validates(tmp_path: Path) -> None:
    make_cfg(hosts=["fe80::1", "::1", "host-1.corp.local"]).validate()
    with pytest.raises(ValueError):
        append_payload(make_exe(tmp_path), tmp_path / "o.exe", make_cfg(port=0))
    assert not (tmp_path / "o.exe").exists()


def test_large_template_streamed(tmp_path: Path) -> None:
    big = tmp_path / "big.exe"
    with big.open("wb") as f:
        f.write(b"MZ")
        chunk = b"\x07" * (1 << 20)
        for _ in range(40):
            f.write(chunk)
    out = tmp_path / "o.exe"
    tracemalloc.start()
    append_payload(big, out, make_cfg())
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 8 * (1 << 20)
    assert read_payload(out) == make_cfg()
    assert out.stat().st_size > big.stat().st_size


# ---------------------------------------------------------------- review round 1
def test_deeply_nested_json_returns_none(tmp_path: Path) -> None:
    deep = b"[" * 30000 + b"]" * 30000
    exe = tmp_path / "d.exe"
    exe.write_bytes(b"MZ" + raw_footer(deep))
    assert read_payload(exe) is None


def test_repr_and_str_hide_secret() -> None:
    cfg = make_cfg()
    assert SECRET not in repr(cfg) and SECRET not in str(cfg)


@pytest.mark.parametrize("host", [
    "999.1.1.1", "1.2.3.4\n", "1.2.3.4 ", " host", "fe80::1%eth0", ":::", "1::2::3",
    "http://x", "1.2.3.4:47800", "a..b", "a" * 64 + ".com", ("a" * 60 + ".") * 5 + "com",
    "1.2.3", "1.2.3.4.5", "host\n", "-bad", "host.", "", "a b",
])
def test_bad_hosts_rejected(host: str) -> None:
    with pytest.raises(ValueError):
        make_cfg(hosts=[host]).validate()


@pytest.mark.parametrize("host", [
    "10.0.0.1", "192.168.1.255", "fe80::1", "2001:db8::ff00:42:8329", "pc-manager",
    "Host_1.corp.example.com", "a" * 63 + ".com",
])
def test_good_hosts_accepted(host: str) -> None:
    make_cfg(hosts=[host]).validate()


def test_host_count_and_duplicates() -> None:
    with pytest.raises(ValueError):
        make_cfg(hosts=[f"10.0.0.{i}" for i in range(9)]).validate()
    make_cfg(hosts=[f"10.0.0.{i}" for i in range(8)]).validate()
    with pytest.raises(ValueError):
        make_cfg(hosts=["Host", "host"]).validate()
    with pytest.raises(ValueError):
        make_cfg(hosts=["10.0.0.1", "10.0.0.1"]).validate()


@pytest.mark.parametrize("kw", [
    {"secret": SECRET + "\n"}, {"fingerprint": FP + "\n"}, {"manager_id": "mgr\n"},
    {"team": "Dev\n"}, {"team": "   "}, {"version": True},
])
def test_trailing_newline_and_odd_values_rejected(kw: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        make_cfg(**kw).validate()


def test_magic_only_tail_keeps_all_template_bytes(tmp_path: Path) -> None:
    junk = bytes(range(200)) * 3 + b"\x00" * 12 + MAGIC  # garbage + magic, no valid footer
    tpl = tmp_path / "t.exe"
    tpl.write_bytes(b"MZ" + junk)
    out = tmp_path / "o.exe"
    append_payload(tpl, out, make_cfg())
    assert out.read_bytes().startswith(tpl.read_bytes())
    assert read_payload(out) == make_cfg()


@pytest.mark.parametrize("n", [1, 2**40])
def test_forged_magic_tiny_and_huge_length(tmp_path: Path, n: int) -> None:
    exe = tmp_path / "f.exe"
    exe.write_bytes(b"MZ" + bytes(50) + struct.pack(">I", 0) + struct.pack(">Q", n) + MAGIC)
    assert read_payload(exe) is None
    out = tmp_path / "o.exe"
    append_payload(exe, out, make_cfg())
    assert out.read_bytes().startswith(exe.read_bytes())


def test_bad_crc_footer_is_not_stripped(tmp_path: Path) -> None:
    g = good_payload()
    tpl = tmp_path / "t.exe"
    tpl.write_bytes(b"MZ" + raw_footer(g, crc=zlib.crc32(g) ^ 1))
    out = tmp_path / "o.exe"
    append_payload(tpl, out, make_cfg())
    assert out.read_bytes().startswith(tpl.read_bytes())
