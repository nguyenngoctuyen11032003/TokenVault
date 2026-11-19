import json
from pathlib import Path

from tokenvault.adapter.identity import (
    Identity,
    default_identity,
    display_account,
    read_identity,
)


def _write(p: Path, obj) -> Path:
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def test_reads_email_and_uuid(tmp_path):
    f = _write(tmp_path / "c.json", {"oauthAccount": {
        "emailAddress": "a@b.com", "accountUuid": "u-1", "accessToken": "SECRET"}})
    i = read_identity(f, "tuyen", "PC1")
    assert i.email == "a@b.com" and i.account_uuid == "u-1"
    assert display_account(i) == "a@b.com"
    assert i.device_id == "PC1:tuyen"
    assert "SECRET" not in repr(i)


def test_missing_file(tmp_path):
    i = read_identity(tmp_path / "nope.json", "tuyen", "PC1")
    assert i.email is None and i.account_uuid is None
    assert display_account(i) == "Unknown account (tuyen@PC1)"


def test_no_oauth_account(tmp_path):
    i = read_identity(_write(tmp_path / "c.json", {"x": 1}), "tuyen", "PC1")
    assert i.email is None


def test_invalid_json(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_identity(p, "tuyen", "PC1").email is None


def test_directory_instead_of_file(tmp_path):
    assert read_identity(tmp_path, "u", "h").email is None


def test_wrong_types(tmp_path):
    cases = [
        [],
        "s",
        {"oauthAccount": []},
        {"oauthAccount": {"emailAddress": 5, "accountUuid": ["x"]}},
        {"oauthAccount": {"emailAddress": "", "accountUuid": ""}},
    ]
    for data in cases:
        i = read_identity(_write(tmp_path / "c.json", data), "u", "h")
        assert i.email is None and i.account_uuid is None


def test_length_limits(tmp_path):
    f = _write(tmp_path / "c.json", {"oauthAccount": {
        "emailAddress": "a" * 255, "accountUuid": "b" * 129}})
    i = read_identity(f, "u", "h")
    assert i.email is None and i.account_uuid is None


def test_oversize_file(tmp_path):
    p = tmp_path / "c.json"
    big = {"oauthAccount": {"emailAddress": "a@b.com"}, "pad": "x" * (5 * 1024 * 1024)}
    p.write_text(json.dumps(big))
    assert read_identity(p, "u", "h").email is None


def test_unicode_user(tmp_path):
    i = read_identity(tmp_path / "x.json", "Tuyền", "PC1")
    assert i.windows_user == "Tuyền" and i.device_id == "PC1:Tuyền"


def test_default_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("getpass.getuser", lambda: "bob")
    monkeypatch.setattr("socket.gethostname", lambda: "H")
    i = default_identity()
    assert isinstance(i, Identity)
    assert i.device_id == "H:bob" and i.email is None
