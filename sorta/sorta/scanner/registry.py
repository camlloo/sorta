"""file_id 注册表（开发文档 §5.2）。

M1 为进程内实现；M3 引入 SQLite 持久化时保持本模块对外接口不变。
安全要点：
- file_id 使用 secrets 生成，不可猜测，且只在所属 scan_id 会话内有效；
- 会话绑定授权根目录，root 不符即拒绝；
- resolve_path 是注册表到真实文件的唯一出口，强制做边界校验；
- verify 用大小 + mtime_ns 检测扫描后文件是否变化（§7.2 第 8 条）。
"""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from sorta.errors import ScanSessionError, UnknownFileError
from sorta.models import FileRecord
from sorta.scanner.paths import resolve_within_root


class VerificationStatus(StrEnum):
    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    MISSING = "missing"


@dataclass(frozen=True)
class FileVerification:
    file_id: str
    status: VerificationStatus
    current_size: int | None = None
    current_modified_ns: int | None = None


@dataclass
class ScanSession:
    """一次扫描授权：根目录、有效期与已注册文件的映射。"""

    scan_id: str
    root: Path  # realpath 后的绝对根目录
    created_at: float
    expires_at: float
    files: dict[str, FileRecord] = field(default_factory=dict)
    _by_path: dict[str, str] = field(default_factory=dict)  # normcase(relpath) -> file_id

    def get(self, file_id: str) -> FileRecord | None:
        return self.files.get(file_id)

    def find_by_path(self, relative_path: str) -> FileRecord | None:
        file_id = self._by_path.get(os.path.normcase(relative_path))
        return self.files.get(file_id) if file_id else None

    def upsert(
        self, *, relative_path: str, extension: str, size: int, modified_ns: int
    ) -> FileRecord:
        """按路径注册或刷新文件；同一路径复用 file_id，元数据以最新扫描为准。"""
        existing = self.find_by_path(relative_path)
        if existing is not None:
            updated = existing.model_copy(
                update={"extension": extension, "size": size, "modified_ns": modified_ns}
            )
            self.files[existing.id] = updated
            return updated

        file_id = f"file_{secrets.token_hex(6)}"
        while file_id in self.files:  # 防御性：token 碰撞概率极低但代价为零
            file_id = f"file_{secrets.token_hex(6)}"
        record = FileRecord(
            id=file_id,
            scan_id=self.scan_id,
            relative_path=relative_path,
            extension=extension,
            size=size,
            modified_ns=modified_ns,
        )
        self.files[file_id] = record
        self._by_path[os.path.normcase(relative_path)] = file_id
        return record


class FileRegistry:
    """scan_id -> ScanSession 的进程内注册中心。"""

    def __init__(self, ttl_seconds: int = 3_600, clock: Callable[[], float] = time.time):
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, ScanSession] = {}

    def create_session(self, root: Path) -> ScanSession:
        now = self._clock()
        scan_id = f"scan_{secrets.token_hex(6)}"
        while scan_id in self._sessions:
            scan_id = f"scan_{secrets.token_hex(6)}"
        session = ScanSession(
            scan_id=scan_id,
            root=Path(os.path.realpath(root)),
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._sessions[scan_id] = session
        return session

    def get_session(self, scan_id: str, *, root: Path | None = None) -> ScanSession:
        session = self._sessions.get(scan_id)
        if session is None:
            raise ScanSessionError(f"未知的 scan_id: {scan_id!r}")
        if self._clock() > session.expires_at:
            del self._sessions[scan_id]
            raise ScanSessionError(f"扫描会话已过期: {scan_id!r}")
        if root is not None and os.path.normcase(str(root)) != os.path.normcase(str(session.root)):
            raise ScanSessionError(f"root 与扫描会话 {scan_id!r} 的授权根目录不符")
        return session

    def register_file(
        self,
        session: ScanSession,
        *,
        relative_path: str,
        extension: str,
        size: int,
        modified_ns: int,
    ) -> FileRecord:
        return session.upsert(
            relative_path=relative_path,
            extension=extension,
            size=size,
            modified_ns=modified_ns,
        )

    def resolve_path(self, scan_id: str, file_id: str) -> tuple[FileRecord, Path]:
        """file_id -> (注册记录, 边界校验后的绝对路径)。这是唯一合法出口。"""
        session = self.get_session(scan_id)
        record = session.get(file_id)
        if record is None:
            raise UnknownFileError(f"未知的 file_id: {file_id!r}（scan_id={scan_id!r}）")
        path = resolve_within_root(session.root, record.relative_path)  # 越界即抛 PathSafetyError
        return record, path

    def verify(self, scan_id: str, file_id: str) -> FileVerification:
        """对照注册元数据检查文件现状：unchanged / modified / missing。"""
        record, path = self.resolve_path(scan_id, file_id)
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return FileVerification(file_id, VerificationStatus.MISSING)
        unchanged = st.st_size == record.size and st.st_mtime_ns == record.modified_ns
        status = VerificationStatus.UNCHANGED if unchanged else VerificationStatus.MODIFIED
        return FileVerification(file_id, status, st.st_size, st.st_mtime_ns)

    def cleanup_expired(self) -> int:
        """清理过期会话，返回清理数量。"""
        now = self._clock()
        expired = [sid for sid, s in self._sessions.items() if now > s.expires_at]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)
