"""文件操作安全原语（开发文档 §8.1）。

- 进程内互斥锁：同一时刻只允许一个执行批次（§8.1 第 2 步）
- 无覆盖移动/复制：Windows 的 os.rename 在目标存在时抛 FileExistsError，
  天然无覆盖；配合串行执行与执行前冲突复检（§8.1 允许的 MVP 策略）
- 指纹：默认 大小 + mtime_ns（§17.4）；目录创建必须先经过边界解析
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from sorta.errors import SortaError


class FileConflictError(SortaError):
    """目标已存在，拒绝覆盖（§10.3 安全不变量）。"""


class CrossDeviceMoveError(SortaError):
    """跨文件系统移动，MVP 不自动降级（§8.1）。"""


_EXECUTION_LOCK = threading.Lock()


def acquire_execution_lock():
    """进程内互斥锁（可作 context manager）。"""
    return _EXECUTION_LOCK


def metadata_fingerprint(path: str | Path) -> str | None:
    """大小 + mtime_ns 指纹；文件不存在返回 None。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return f"{st.st_size}:{st.st_mtime_ns}"


def fingerprints_match(path: str | Path, fingerprint: str | None) -> bool:
    current = metadata_fingerprint(path)
    return fingerprint is not None and current == fingerprint


def ensure_parent_dirs(root: Path, destination: str) -> Path:
    """创建目标父目录（已解析的边界内路径），返回目标绝对路径。"""
    dest = Path(os.path.realpath(root)) / destination
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def safe_move(source: Path, destination: Path) -> None:
    """无覆盖移动。目标存在抛 FileConflictError；跨设备抛 CrossDeviceMoveError。"""
    if os.path.lexists(destination):
        raise FileConflictError(f"目标已存在，拒绝覆盖: {destination}")
    try:
        os.rename(source, destination)
    except FileExistsError as exc:
        raise FileConflictError(f"目标已存在，拒绝覆盖: {destination}") from exc
    except OSError as exc:
        # Windows 跨盘: winerror 17；POSIX: EXDEV
        if getattr(exc, "winerror", None) == 17 or exc.errno == 18:
            raise CrossDeviceMoveError(
                f"跨文件系统移动不受 MVP 支持（限制同一盘符内）: {source} -> {destination}"
            ) from exc
        raise


def safe_copy(source: Path, destination: Path) -> None:
    """无覆盖复制（copy2 保留元数据）。先检查后复制，配合串行执行。"""
    if os.path.lexists(destination):
        raise FileConflictError(f"目标已存在，拒绝覆盖: {destination}")
    import shutil

    shutil.copy2(source, destination)
