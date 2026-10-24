"""M1 最小 CLI：目前仅 scan 子命令，用于人工验证扫描与注册能力。

完整命令集（plan/inspect-plan/apply/history/undo）见开发文档 §11.2，随 M2/M3 实现。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


def _fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _fmt_time(modified_ns: int) -> str:
    return datetime.fromtimestamp(modified_ns / 1e9).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _cmd_scan(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    from sorta.config import SortaConfig
    from sorta.scanner import FileRegistry, Scanner

    load_dotenv()
    config = SortaConfig.from_env()
    registry = FileRegistry(ttl_seconds=config.scan_session_ttl_seconds)
    scanner = Scanner(registry, config)
    page = scanner.scan(
        Path(args.root),
        max_depth=args.depth,
        limit=args.limit,
        include_hidden=args.all,
    )
    print(f"scan_id: {page.scan_id}")
    if not page.files:
        print("（未发现文件）")
    for f in page.files:
        print(f"{f.id}  {_fmt_size(f.size):>10}  {_fmt_time(f.modified_ns)}  {f.relative_path}")
    more = f"（还有更多，next_cursor={page.next_cursor}）" if page.has_more else ""
    print(f"共 {len(page.files)} 个文件{more}")
    if page.skipped_directories:
        print(f"无法读取的目录: {', '.join(page.skipped_directories)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sorta", description="SORTA — AI 智能文件整理助手（M1：只读扫描）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="扫描授权目录并注册文件")
    p_scan.add_argument("root", help="授权根目录（所有操作局限在该目录内）")
    p_scan.add_argument("--depth", type=int, default=None, help="扫描深度（默认取配置上限）")
    p_scan.add_argument("--limit", type=int, default=None, help="本页文件数（默认取配置上限）")
    p_scan.add_argument("--all", action="store_true", help="包含隐藏文件")
    p_scan.set_defaults(func=_cmd_scan)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
