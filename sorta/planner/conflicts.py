"""冲突检测（开发文档 §7.2 第 4-7 条、第 9 条）。

路径比较统一使用 os.path.normcase，匹配 Windows NTFS 大小写不敏感语义
（SQLite 比较是大小写敏感的，与 NTFS 不一致，必须在应用层归一化——§9 约定）。
"""

from __future__ import annotations

import os
import re

# Windows 保留设备名（含带扩展名形式，如 CON.txt 同样非法）
_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_INVALID_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_MAX_RECOMMENDED_PATH = 240  # 接近 MAX_PATH 260 的预警线
_MAX_DEST_DEPTH = 8


def check_reserved_name(component: str) -> str | None:
    """返回错误说明；合法返回 None。适用于单个路径段。"""
    stem = component.split(".")[0].upper()
    if stem in _RESERVED_NAMES:
        return f"包含 Windows 保留设备名 {stem!r}"
    if _INVALID_CHARS.search(component):
        return '包含 Windows 非法字符 < > : " | ? * 或控制字符'
    if component != component.rstrip(" ."):
        return "以点或空格结尾（Windows 不允许）"
    return None


def destination_depth(destination: str) -> int:
    return len([p for p in destination.split("/") if p])


def destination_length_warning(root: str, destination: str) -> bool:
    return len(os.path.join(root, destination)) > _MAX_RECOMMENDED_PATH


def find_duplicate_sources(sources: list[str]) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for s in sources:
        key = os.path.normcase(s)
        if key in seen:
            dupes.add(key)
        seen.add(key)
    return dupes


def find_duplicate_destinations(destinations: list[str]) -> set[str]:
    return find_duplicate_sources(destinations)


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))
