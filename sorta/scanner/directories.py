"""只读发现可逐层清空的目录；不跟随链接，不把隐藏内容误判为空。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from sorta.models import FileRecord
from sorta.scanner.paths import resolve_within_root
from sorta.scanner.registry import ScanSession


@dataclass
class EmptyDirectoryScan:
    directories: list[FileRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    has_more: bool = False


def scan_empty_directories(session: ScanSession, *, limit: int) -> EmptyDirectoryScan:
    """后序登记目录：先列子目录，再列可随之变空的父目录；授权根目录永不入选。"""
    from sorta.scanner.scanner import _is_hidden, _is_junction

    result = EmptyDirectoryScan()
    visited = 0

    def walk(path: Path, depth: int) -> bool:
        nonlocal visited
        visited += 1
        relative = path.relative_to(session.root).as_posix()
        if depth > 64 or visited > 10_000:
            result.skipped.append(relative)
            result.has_more = True
            return False
        try:
            if path != session.root:
                path = resolve_within_root(session.root, relative)
            with os.scandir(path) as entries:
                children = list(entries)
            removable = True
            for entry in children:
                if (
                    entry.is_symlink()
                    or _is_junction(entry)
                    or _is_hidden(entry)
                    or not entry.is_dir(follow_symlinks=False)
                ):
                    removable = False
                    continue
                if not walk(Path(entry.path), depth + 1):
                    removable = False
            if path == session.root or not removable:
                return False
            if len(result.directories) >= limit:
                result.has_more = True
                return False
            metadata = path.stat()
            result.directories.append(
                session.upsert(
                    relative_path=relative,
                    extension="",
                    size=metadata.st_size,
                    modified_ns=metadata.st_mtime_ns,
                    is_regular_file=False,
                )
            )
            return True
        except (OSError, ValueError):
            result.skipped.append(relative)
            return False

    walk(session.root, 0)
    return result
