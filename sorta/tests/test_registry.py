"""FileRegistry 测试（开发文档 §13.1：未知 file_id、过期 scan_id、
扫描后源文件被替换 / 变化、越界路径拒绝）。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from sorta.errors import PathSafetyError, ScanSessionError, UnknownFileError
from sorta.scanner import FileRegistry, VerificationStatus


@pytest.fixture
def registry() -> FileRegistry:
    return FileRegistry(ttl_seconds=3600)


def _register(registry: FileRegistry, root: Path, name: str = "a.txt"):
    session = registry.create_session(root)
    p = root / name
    st = os.stat(p)
    record = registry.register_file(
        session,
        relative_path=name,
        extension="txt",
        size=st.st_size,
        modified_ns=st.st_mtime_ns,
    )
    return session, record


def test_register_and_get_roundtrip(tmp_path, registry):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    session, record = _register(registry, tmp_path)

    assert session.get(record.id) is record
    assert session.find_by_path("a.txt").id == record.id
    got, path = registry.resolve_path(session.scan_id, record.id)
    assert got is record and path == tmp_path / "a.txt"


def test_unknown_file_id_rejected(tmp_path, registry):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    session, _ = _register(registry, tmp_path)
    with pytest.raises(UnknownFileError, match="file_"):
        registry.resolve_path(session.scan_id, "file_nonexistent")


def test_unknown_scan_id_rejected(registry):
    with pytest.raises(ScanSessionError, match="未知"):
        registry.get_session("scan_nope")


def test_expired_session_rejected_and_cleanup(tmp_path):
    now = time.time()

    # 已过期的会话在被访问时报错并移除
    registry = FileRegistry(ttl_seconds=100, clock=lambda: now)
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    session, record = _register(registry, tmp_path)
    registry._clock = lambda: now + 101  # 模拟时间流逝
    with pytest.raises(ScanSessionError, match="过期"):
        registry.get_session(session.scan_id)
    with pytest.raises(ScanSessionError):
        registry.resolve_path(session.scan_id, record.id)
    assert registry.cleanup_expired() == 0  # 访问时已移除，无残留

    # 未被访问的过期会话由 cleanup_expired 批量清理
    registry2 = FileRegistry(ttl_seconds=100, clock=lambda: now)
    session2 = registry2.create_session(tmp_path)
    registry2._clock = lambda: now + 101
    assert registry2.cleanup_expired() == 1
    with pytest.raises(ScanSessionError, match="未知"):
        registry2.get_session(session2.scan_id)


def test_get_session_root_mismatch_rejected(tmp_path, registry):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    session, _ = _register(registry, tmp_path)
    with pytest.raises(ScanSessionError, match="不符"):
        registry.get_session(session.scan_id, root=other)


def test_resolve_rejects_tampered_traversal_record(tmp_path, registry):
    session = registry.create_session(tmp_path)
    session.upsert(relative_path="../escape.txt", extension="txt", size=1, modified_ns=1)
    file_id = next(iter(session.files))
    with pytest.raises(PathSafetyError, match="越界|非法路径段"):
        registry.resolve_path(session.scan_id, file_id)


def test_resolve_rejects_absolute_path_record(tmp_path, registry):
    session = registry.create_session(tmp_path)
    session.upsert(relative_path=str(tmp_path / "abs.txt"), extension="txt", size=1, modified_ns=1)
    file_id = next(iter(session.files))
    with pytest.raises(PathSafetyError, match="绝对路径"):
        registry.resolve_path(session.scan_id, file_id)


def test_verify_unchanged(tmp_path, registry):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    session, record = _register(registry, tmp_path)
    v = registry.verify(session.scan_id, record.id)
    assert v.status is VerificationStatus.UNCHANGED
    assert v.current_size == record.size


def test_verify_detects_modification(tmp_path, registry):
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    session, record = _register(registry, tmp_path)

    # 内容变化（大小随之变化）
    p.write_text("hello world, changed", encoding="utf-8")
    assert registry.verify(session.scan_id, record.id).status is VerificationStatus.MODIFIED

    # 只动 mtime、大小不变（通过 upsert 刷新基线后再改时间戳）
    session2 = registry.create_session(tmp_path)
    st = os.stat(p)
    fresh = registry.register_file(
        session2,
        relative_path="a.txt",
        extension="txt",
        size=st.st_size,
        modified_ns=st.st_mtime_ns,
    )
    os.utime(p, ns=(st.st_atime_ns + 1_000_000_000, st.st_mtime_ns + 1_000_000_000))
    assert registry.verify(session2.scan_id, fresh.id).status is VerificationStatus.MODIFIED


def test_verify_missing_after_delete(tmp_path, registry):
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    session, record = _register(registry, tmp_path)
    p.unlink()
    assert registry.verify(session.scan_id, record.id).status is VerificationStatus.MISSING


def test_resolve_rejects_symlink_replacement(tmp_path, registry, can_symlink):
    if not can_symlink:
        pytest.skip("当前环境无法创建符号链接")
    outside = tmp_path / "sorta-outside-target"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")

    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    session, record = _register(registry, tmp_path)

    p.unlink()  # 扫描后原文件被替换为指向外部的符号链接（TOCTOU 场景）
    os.symlink(secret, p)
    with pytest.raises(PathSafetyError):
        registry.resolve_path(session.scan_id, record.id)


@pytest.mark.skipif(os.name != "nt", reason="依赖 NTFS 大小写不敏感语义")
def test_upsert_same_path_keeps_file_id(tmp_path, registry):
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    session = registry.create_session(tmp_path)
    st = os.stat(p)
    first = session.upsert(
        relative_path="a.txt", extension="txt", size=st.st_size, modified_ns=st.st_mtime_ns
    )
    second = session.upsert(
        relative_path="A.TXT", extension="txt", size=99, modified_ns=1
    )  # Windows 大小写不敏感：同一路径
    assert second.id == first.id
    assert second.size == 99
