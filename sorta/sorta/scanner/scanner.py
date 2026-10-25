"""授权目录扫描器（开发文档 §6.1 list_files 的底层实现）。

安全约束（§6.1 / §10.2）：
- 不跟随符号链接与 Windows junction，链接本身也不注册；
- 隐藏文件/目录默认跳过（dotfile 约定 + Windows 隐藏属性）；
- 深度与每页数量受配置上限约束，超出即报错而非静默放宽；
- 普通文件与可清理空目录分开返回，目录单独标记且不可按文件处理；
- 无法读取的目录（权限不足等）跳过并在结果中如实报告。
"""

from __future__ import annotations

import os
import stat as stat_mod
from dataclasses import dataclass
from pathlib import Path, PurePath

from pydantic import BaseModel, Field

from sorta.config import SortaConfig
from sorta.errors import ScanError, ScanSessionError
from sorta.models import FileRecord
from sorta.scanner.registry import FileRegistry, ScanSession


class ScanPage(BaseModel):
    """一次扫描返回的一页结果（§6.1 输出契约）。"""

    scan_id: str
    files: list[FileRecord] = Field(default_factory=list)
    empty_directories: list[FileRecord] = Field(default_factory=list)
    directory_scan_has_more: bool = False
    has_more: bool = False
    next_cursor: str | None = None
    skipped_directories: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class _FileMeta:
    relative_path: str  # posix 风格，始终正斜杠
    extension: str
    size: int
    modified_ns: int


def _is_junction(entry: os.DirEntry) -> bool:
    is_junction = getattr(entry, "is_junction", None)  # Python 3.12+ 提供
    return bool(is_junction()) if callable(is_junction) else False


def _is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    if os.name == "nt":
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            return False
        return bool(st.st_file_attributes & stat_mod.FILE_ATTRIBUTE_HIDDEN)
    return False


def _relative_key(root: Path, path: str | Path) -> str:
    rel = os.path.relpath(path, root)
    return PurePath(rel).as_posix()


class Scanner:
    def __init__(self, registry: FileRegistry, config: SortaConfig | None = None):
        self.registry = registry
        self.config = config or SortaConfig()

    def scan(
        self,
        root: str | Path,
        *,
        max_depth: int | None = None,
        limit: int | None = None,
        include_hidden: bool | None = None,
        cursor: str | None = None,
        session: ScanSession | None = None,
    ) -> ScanPage:
        cfg = self.config
        root_path = Path(root)
        if not root_path.exists():
            raise ScanError(f"根目录不存在: {root_path}")
        if not root_path.is_dir():
            raise ScanError(f"根目录不是目录: {root_path}")
        root_real = Path(os.path.realpath(root_path))

        if cursor is not None:
            scan_id_cursor, _, last_key = cursor.partition("|")
            if not scan_id_cursor or not last_key:
                raise ScanError(f"cursor 格式非法: {cursor!r}")
            if session is not None and scan_id_cursor != session.scan_id:
                raise ScanError("cursor 与传入会话不一致")
            session = self.registry.get_session(scan_id_cursor, root=root_real)
        elif session is not None:
            # Agent 流程：会话由 controller 预先创建（§4.1 第 2 步），扫描登记进同一会话
            if os.path.normcase(str(session.root)) != os.path.normcase(str(root_real)):
                raise ScanSessionError(f"传入会话与根目录 {root_real} 不符")
            last_key = ""
        else:
            session = self.registry.create_session(root_real)
            last_key = ""
        scan_id = session.scan_id

        depth = cfg.max_scan_depth if max_depth is None else max_depth
        page_size = cfg.max_scan_files if limit is None else limit
        if depth > cfg.max_scan_depth:
            raise ScanError(f"max_depth={depth} 超过上限 {cfg.max_scan_depth}")
        if page_size > cfg.max_scan_files:
            raise ScanError(f"limit={page_size} 超过上限 {cfg.max_scan_files}")
        hidden = cfg.include_hidden if include_hidden is None else include_hidden

        metas, skipped = self._walk(root_real, depth, hidden)
        if last_key:
            metas = [m for m in metas if m.relative_path > last_key]

        page_metas = metas[:page_size]
        has_more = len(metas) > page_size
        files = [
            self.registry.register_file(
                session,
                relative_path=m.relative_path,
                extension=m.extension,
                size=m.size,
                modified_ns=m.modified_ns,
            )
            for m in page_metas
        ]
        from sorta.scanner.directories import scan_empty_directories

        directories = scan_empty_directories(session, limit=cfg.max_plan_operations)
        return ScanPage(
            scan_id=scan_id,
            files=files,
            empty_directories=directories.directories,
            directory_scan_has_more=directories.has_more,
            has_more=has_more,
            next_cursor=(
                f"{scan_id}|{page_metas[-1].relative_path}" if has_more and page_metas else None
            ),
            skipped_directories=sorted(set(skipped + directories.skipped)),
        )

    def _walk(
        self, root_real: Path, max_depth: int, include_hidden: bool
    ) -> tuple[list[_FileMeta], list[str]]:
        metas: list[_FileMeta] = []
        skipped: list[str] = []
        stack: list[tuple[str, int]] = [(str(root_real), 0)]
        while stack:
            current, depth = stack.pop()
            try:
                with os.scandir(current) as it:
                    entries = list(it)
            except OSError:
                skipped.append(_relative_key(root_real, current))
                continue
            for entry in entries:
                if entry.is_symlink() or _is_junction(entry):
                    continue
                if not include_hidden and _is_hidden(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if depth + 1 <= max_depth:
                        stack.append((entry.path, depth + 1))
                elif entry.is_file(follow_symlinks=False) and depth <= max_depth:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    metas.append(
                        _FileMeta(
                            relative_path=_relative_key(root_real, entry.path),
                            extension=Path(entry.name).suffix.lower().lstrip("."),
                            size=st.st_size,
                            modified_ns=st.st_mtime_ns,
                        )
                    )
        metas.sort(key=lambda m: m.relative_path)
        return metas, skipped
