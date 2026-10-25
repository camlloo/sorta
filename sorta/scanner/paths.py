"""路径安全工具：所有相对路径到绝对路径的解析必须经过这里（§7.2 / §10.2）。

Windows 优先的考量：
- 比较使用 os.path.normcase，匹配 NTFS 大小写不敏感语义；
- realpath 会解引用符号链接与 junction，越界目标在解析阶段即被拒绝；
- 额外拒绝形如 "C:foo" 的盘符相对路径段。
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePath

from sorta.errors import PathSafetyError

_DRIVE_PART = re.compile(r"^[A-Za-z]:$")


def is_within(root: Path, candidate: Path) -> bool:
    """candidate 是否位于 root 内部（含 root 本身），大小写不敏感，跨盘符安全。"""
    root_key = os.path.normcase(os.path.abspath(root))
    candidate_key = os.path.normcase(os.path.abspath(candidate))
    try:
        return os.path.commonpath([root_key, candidate_key]) == root_key
    except ValueError:  # Windows 上不同盘符 commonpath 会抛 ValueError
        return False


def resolve_within_root(root: Path, relative: str | PurePath) -> Path:
    """把相对路径解析为 root 内的绝对路径；任何越界可能都抛 PathSafetyError。

    注意：realpath 解引用符号链接后，目标若落在 root 之外（包括扫描后文件
    被替换为指向外部的链接这类 TOCTOU 场景）同样拒绝。
    """
    rel = relative if isinstance(relative, PurePath) else PurePath(relative)
    if rel.is_absolute():
        raise PathSafetyError(f"拒绝绝对路径: {relative!r}")

    parts = rel.parts
    if not parts:
        raise PathSafetyError("相对路径为空")
    for part in parts:
        if part == ".." or _DRIVE_PART.match(part):
            raise PathSafetyError(f"非法路径段 {part!r}（禁止越界或盘符相对路径）: {relative!r}")

    root_real = Path(os.path.realpath(root))
    direct = root_real.joinpath(*parts)
    candidate = Path(os.path.realpath(direct))
    if not is_within(root_real, candidate):
        raise PathSafetyError(f"解析结果越界: {relative!r} -> {candidate}")
    is_junction = os.path.isjunction(direct) if hasattr(os.path, "isjunction") else False
    if direct.is_symlink() or is_junction:
        # 注册表只登记普通文件；路径变为链接说明扫描后被替换（TOCTOU），即使
        # 解析结果仍在边界内也拒绝。
        raise PathSafetyError(f"拒绝符号链接/junction: {relative!r}")
    return candidate
