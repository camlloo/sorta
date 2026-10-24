"""M1 最小 CLI：目前仅 scan 子命令，用于人工验证扫描与注册能力。

完整命令集（plan/inspect-plan/apply/history/undo）见开发文档 §11.2，随 M2/M3 实现。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from sorta.config import PRIVACY_MODES


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


def _interactive_consent(record) -> bool:
    """ask_before_content 模式下逐文件征得用户同意（§10.1 推荐默认）。"""
    try:
        answer = input(f"允许读取「{record.relative_path}」的内容并发送给云端模型？[y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _cmd_plan(args: argparse.Namespace) -> int:
    import os
    from dataclasses import replace

    from dotenv import load_dotenv

    from sorta.agent import AgentController, OpenAICompatClient
    from sorta.config import SortaConfig
    from sorta.errors import SortaError
    from sorta.scanner import FileRegistry, Scanner

    load_dotenv()
    config = SortaConfig.from_env()
    if args.privacy:
        config = replace(config, default_privacy=args.privacy)

    api_key = os.environ.get("SORTA_API_KEY")
    model = os.environ.get("SORTA_MODEL")
    base_url = os.environ.get("SORTA_BASE_URL") or None
    if not api_key or not model:
        print("缺少 SORTA_API_KEY 或 SORTA_MODEL 环境变量（复制 .env.example 为 .env 并填写）")
        return 2

    registry = FileRegistry(ttl_seconds=config.scan_session_ttl_seconds)
    scanner = Scanner(registry, config)
    consent = _interactive_consent if config.default_privacy == "ask_before_content" else None
    controller = AgentController(
        registry=registry,
        scanner=scanner,
        config=config,
        llm=OpenAICompatClient(api_key=api_key, base_url=base_url),
        consent_callback=consent,
    )

    print(f"正在调查（模型 {model}，隐私模式 {config.default_privacy}，数据将发送给云端模型）…")
    try:
        result = controller.run(args.root, args.request, model=model)
    except SortaError as exc:
        print(f"错误: {exc}")
        return 1

    print(f"\n== 只读工具调用摘要（共 {len(result.tool_log)} 次）==")
    for record in result.tool_log:
        mark = "✓" if record.ok else "✗"
        print(f"{mark} #{record.index} {record.name}: {record.summary}")

    plan = result.plan
    session = registry.get_session(result.scan_id)
    print(f"\n== 整理计划（scan_id={result.scan_id}，共 {len(plan.operations)} 项操作）==")
    print(f"摘要: {plan.summary}")
    if not plan.operations:
        print("（无操作）")
    for op in plan.operations:
        record = session.get(op.source_id)
        source = record.relative_path if record else f"<未知 file_id {op.source_id}>"
        print(f"- [{op.action.value}/{op.confidence.value}] {source} -> {op.destination}")
        print(f"  依据: {op.reason}")
    if plan.uncertain_files:
        print("\n待确认文件（不移动，由用户决定）:")
        for file_id in plan.uncertain_files:
            record = session.get(file_id)
            print(f"- {record.relative_path if record else file_id}")

    if args.output:
        args.output.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        print(f"\n计划已写入 {args.output}")
    print("\n注意：当前版本仅生成计划，不执行任何文件操作（执行引擎在 M3 提供）。")
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

    p_plan = sub.add_parser("plan", help="自然语言生成整理计划（只读调查，不执行）")
    p_plan.add_argument("--root", required=True, help="授权根目录")
    p_plan.add_argument("--request", required=True, help="自然语言整理需求")
    p_plan.add_argument("--output", type=Path, default=None, help="计划 JSON 输出路径")
    p_plan.add_argument("--privacy", choices=PRIVACY_MODES, default=None, help="覆盖隐私模式")
    p_plan.set_defaults(func=_cmd_plan)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
