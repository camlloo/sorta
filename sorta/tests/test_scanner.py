"""Scanner 测试（开发文档 §13.1：深度/数量限制、中文与空格路径、隐藏文件、
空目录、权限不足、符号链接）。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from sorta.config import SortaConfig
from sorta.errors import ScanError, ScanSessionError
from sorta.scanner import FileRegistry, Scanner


@pytest.fixture
def scanner() -> Scanner:
    return Scanner(FileRegistry(), SortaConfig())


def _write(root: Path, rel: str, content: str = "x") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_scan_finds_files_with_metadata(tmp_path, scanner):
    _write(tmp_path, "notes.txt", "hello")
    _write(tmp_path, "sub/data.json", "{}")
    page = scanner.scan(tmp_path)

    assert page.has_more is False and page.next_cursor is None
    assert {f.relative_path for f in page.files} == {"notes.txt", "sub/data.json"}
    for f in page.files:
        assert f.scan_id == page.scan_id
        assert f.size >= 1
        assert f.modified_ns > 0
        assert f.is_regular_file is True
        assert f.id.startswith("file_")


def test_scan_extension_lowercase(tmp_path, scanner):
    _write(tmp_path, "A.PDF", "")
    _write(tmp_path, "noext", "")
    page = scanner.scan(tmp_path)
    ext_by_path = {f.relative_path: f.extension for f in page.files}
    assert ext_by_path["A.PDF"] == "pdf"
    assert ext_by_path["noext"] == ""


def test_scan_depth_limit(tmp_path, scanner):
    _write(tmp_path, "a.txt")
    _write(tmp_path, "l1/b.txt")
    _write(tmp_path, "l1/l2/c.txt")
    _write(tmp_path, "l1/l2/l3/d.txt")  # 深度 3，默认上限 2 之外

    paths = {f.relative_path for f in scanner.scan(tmp_path).files}
    assert paths == {"a.txt", "l1/b.txt", "l1/l2/c.txt"}

    paths0 = {f.relative_path for f in scanner.scan(tmp_path, max_depth=0).files}
    assert paths0 == {"a.txt"}


def test_scan_limit_and_pagination(tmp_path, scanner):
    for i in range(5):
        _write(tmp_path, f"f{i}.txt")
    page1 = scanner.scan(tmp_path, limit=2)
    assert len(page1.files) == 2 and page1.has_more is True
    assert page1.files[0].relative_path == "f0.txt"

    page2 = scanner.scan(tmp_path, cursor=page1.next_cursor, limit=2)
    assert [f.relative_path for f in page2.files] == ["f2.txt", "f3.txt"]
    assert page2.scan_id == page1.scan_id
    assert page2.has_more is True

    page3 = scanner.scan(tmp_path, cursor=page2.next_cursor, limit=2)
    assert [f.relative_path for f in page3.files] == ["f4.txt"]
    assert page3.has_more is False and page3.next_cursor is None


def test_scan_new_session_gets_fresh_file_ids(tmp_path, scanner):
    """file_id 只在所属扫描会话内有效（开发文档 §5.2）：新会话必须重新分配。"""
    _write(tmp_path, "a.txt")
    _write(tmp_path, "b.txt")
    page1 = scanner.scan(tmp_path)
    page2 = scanner.scan(tmp_path)
    assert page1.scan_id != page2.scan_id
    assert {f.id for f in page1.files}.isdisjoint({f.id for f in page2.files})


def test_scan_hidden_dotfiles_skipped_by_default(tmp_path, scanner):
    _write(tmp_path, "visible.txt")
    _write(tmp_path, ".hidden.txt")
    _write(tmp_path, ".hiddendir/inner.txt")

    assert {f.relative_path for f in scanner.scan(tmp_path).files} == {"visible.txt"}

    all_paths = {f.relative_path for f in scanner.scan(tmp_path, include_hidden=True).files}
    assert all_paths == {".hidden.txt", ".hiddendir/inner.txt", "visible.txt"}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 隐藏属性")
def test_scan_windows_hidden_attribute(tmp_path, scanner):
    p = _write(tmp_path, "attributed.txt", "x")
    subprocess.run(["attrib", "+h", str(p)], check=True)

    assert scanner.scan(tmp_path).files == []
    assert len(scanner.scan(tmp_path, include_hidden=True).files) == 1


def test_scan_skips_symlinks(tmp_path, scanner, can_symlink):
    if not can_symlink:
        pytest.skip("当前环境无法创建符号链接")
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "sorta-outside"  # 位于 root 之外
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "normal.txt").write_text("x", encoding="utf-8")

    os.symlink(outside / "secret.txt", root / "linked-file.txt")
    os.symlink(outside, root / "linked-dir")

    paths = {f.relative_path for f in scanner.scan(root).files}
    assert paths == {"normal.txt"}


def test_scan_skips_junction_dir(tmp_path, scanner, can_junction):
    if not can_junction:
        pytest.skip("当前环境无法创建 junction")
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "sorta-outside-junction"  # 位于 root 之外
    (outside / "deep").mkdir(parents=True)
    (outside / "deep" / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "normal.txt").write_text("x", encoding="utf-8")

    link = root / "junction-dir"
    try:
        os.junction(str(link), str(outside))
    except OSError as exc:
        pytest.skip(f"os.junction 创建失败: {exc}")
    if not os.path.isjunction(str(link)):  # 参数顺序与预期相反的兜底
        os.junction(str(outside), str(link))
    assert os.path.isjunction(str(link))

    paths = {f.relative_path for f in scanner.scan(root).files}
    assert paths == {"normal.txt"}


def test_scan_chinese_and_space_paths(tmp_path, scanner):
    _write(tmp_path, "中文 目录/逆向 工程 笔记.txt", "内容")
    _write(tmp_path, "任务书 v2.md", "# 任务")
    paths = {f.relative_path for f in scanner.scan(tmp_path).files}
    assert paths == {"中文 目录/逆向 工程 笔记.txt", "任务书 v2.md"}


def test_scan_empty_directory(tmp_path, scanner):
    (tmp_path / "empty-sub").mkdir()
    page = scanner.scan(tmp_path)
    assert page.files == [] and page.has_more is False


def test_scan_rejects_missing_or_file_root(tmp_path, scanner):
    with pytest.raises(ScanError, match="不存在"):
        scanner.scan(tmp_path / "nope")
    f = tmp_path / "plain.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ScanError, match="不是目录"):
        scanner.scan(f)


def test_scan_rejects_limits_over_config_cap(tmp_path, scanner):
    _write(tmp_path, "a.txt")
    with pytest.raises(ScanError, match="max_depth"):
        scanner.scan(tmp_path, max_depth=3)
    with pytest.raises(ScanError, match="limit"):
        scanner.scan(tmp_path, limit=201)


def test_scan_cursor_with_wrong_root_rejected(tmp_path, scanner):
    _write(tmp_path, "a.txt")
    other = tmp_path / "other-root"
    other.mkdir()
    page = scanner.scan(tmp_path)
    fake = page.next_cursor or f"{page.scan_id}|a.txt"
    with pytest.raises(ScanSessionError, match="不符"):
        scanner.scan(other, cursor=fake)


def test_scan_cursor_unknown_session(tmp_path, scanner):
    _write(tmp_path, "a.txt")
    with pytest.raises(ScanSessionError, match="未知"):
        scanner.scan(tmp_path, cursor="scan_deadbeef|a.txt")


def test_scan_reports_unreadable_directories(tmp_path, scanner, monkeypatch):
    _write(tmp_path, "normal.txt")
    _write(tmp_path, "locked/inner.txt")

    import sorta.scanner.scanner as scanner_mod

    real_scandir = os.scandir

    def fake_scandir(path):
        if Path(path).name == "locked":
            raise PermissionError("simulated permission denied")
        return real_scandir(path)

    monkeypatch.setattr(scanner_mod.os, "scandir", fake_scandir)
    page = scanner.scan(tmp_path)
    assert page.skipped_directories == ["locked"]
    assert {f.relative_path for f in page.files} == {"normal.txt"}


def test_scan_deterministic_order(tmp_path, scanner):
    names = ["b.txt", "a.txt", "z.txt", "m.txt"]
    for n in names:
        _write(tmp_path, n)
    ordered1 = [f.relative_path for f in scanner.scan(tmp_path).files]
    ordered2 = [f.relative_path for f in scanner.scan(tmp_path).files]
    assert ordered1 == ordered2 == sorted(names)
