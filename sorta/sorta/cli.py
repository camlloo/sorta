"""SORTA CLI：scan / plan / inspect-plan / apply / history / undo（§11.2）。

apply 的信任边界：plan.json 是外部文件，必须重新走结构校验 + PlanValidator +
审批绑定，绝不直接信任其内容。
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


def _fmt_epoch(epoch: float | None) -> str:
    if epoch is None:
        return "-"
    return datetime.fromtimestamp(epoch).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _interactive_consent(record) -> bool:
    """ask_before_content 模式下逐文件征得用户同意（§10.1 推荐默认）。"""
    try:
        answer = input(f"允许读取「{record.relative_path}」的内容并发送给云端模型？[y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


# ---- 共享构件 ---------------------------------------------------------------


def _load_env_and_config():
    from dotenv import load_dotenv

    from sorta.config import SortaConfig

    load_dotenv()
    return SortaConfig.from_env()


def _open_store(config):
    from sorta.storage import PlanStore, create_db_engine, init_db

    engine = create_db_engine(config.db_url)
    init_db(engine)
    return PlanStore(engine)


def _load_plan_file(path: Path):
    """加载外部 plan.json：不可信输入，结构不符即拒绝。"""
    from sorta.agent.schemas import ExecutionPlan

    try:
        return ExecutionPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"错误: 计划文件不存在: {path}") from None
    except ValueError as exc:
        raise SystemExit(f"错误: 计划文件不是合法的 ExecutionPlan: {exc}") from None


def _restore_session(config, store, plan):
    """跨进程恢复扫描会话（plan 与 apply 分属两个进程）。"""
    from sorta.errors import SortaError
    from sorta.scanner import FileRegistry

    registry = FileRegistry(ttl_seconds=config.scan_session_ttl_seconds)
    try:
        session = store.load_session(plan.scan_id)
    except SortaError as exc:
        raise SystemExit(f"错误: {exc}") from None
    registry.restore_session(session)
    return registry, session


def _print_plan_overview(plan, session) -> None:
    print(f"\n== 整理计划（scan_id={plan.scan_id}，共 {len(plan.operations)} 项操作）==")
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


# ---- scan / plan（M1/M2） ----------------------------------------------------


def _cmd_scan(args: argparse.Namespace) -> int:
    from sorta.scanner import FileRegistry, Scanner

    config = _load_env_and_config()
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


def _cmd_plan(args: argparse.Namespace) -> int:
    import os
    from dataclasses import replace

    from sorta.agent import AgentController, OpenAICompatClient
    from sorta.errors import SortaError
    from sorta.planner import compute_plan_hash
    from sorta.scanner import FileRegistry, Scanner

    config = _load_env_and_config()
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
    _print_plan_overview(plan, session)

    # M3：会话与计划落库，apply 可跨进程执行。
    # 注意顺序：plans.scan_id 外键指向 scan_sessions，必须先存会话再存计划。
    try:
        store = _open_store(config)
        store.save_session(session)
        plan_hash = compute_plan_hash(plan)
        plan_id = store.save_plan(plan.model_dump(mode="json"), plan_hash, status="draft")
    except Exception as exc:  # 持久化失败不应吞掉已生成的计划
        print(f"\n警告: 计划落库失败（{type(exc).__name__}: {exc}），apply 将无法跨进程执行该计划")
        plan_id = None

    if args.output:
        args.output.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        print(f"\n计划已写入 {args.output}")
    if plan_id is not None:
        print(
            f"\n计划已入库（plan_id={plan_id}, plan_hash={plan_hash[:12]}…）。\n"
            f"校验并执行: uv run sorta apply {args.output or '<计划文件>'}"
        )
    return 0


# ---- inspect-plan / apply / history / undo（M3） ------------------------------


def _validate_and_report(config, registry, plan) -> int:
    """运行 PlanValidator 并打印问题；返回 error 数量。"""
    from sorta.planner import PlanValidator

    validator = PlanValidator(registry, config)
    _, issues = validator.validate(plan)
    if not issues:
        print("\n校验通过：未发现问题。")
        return 0
    print(f"\n== 校验结果（{len(issues)} 项）==")
    for issue in issues:
        mark = "ERROR" if issue.level == "error" else "WARN "
        where = f"操作#{issue.operation_index}" if issue.operation_index is not None else "计划"
        print(f"[{mark}] {where} {issue.code}: {issue.message}")
    return sum(1 for i in issues if i.level == "error")


def _cmd_inspect_plan(args: argparse.Namespace) -> int:
    plan = _load_plan_file(Path(args.plan_file))
    config = _load_env_and_config()
    store = _open_store(config)
    registry, session = _restore_session(config, store, plan)
    _print_plan_overview(plan, session)
    errors = _validate_and_report(config, registry, plan)
    return 1 if errors else 0


def _cmd_apply(args: argparse.Namespace) -> int:
    from sorta.errors import SortaError
    from sorta.executor import ExecutionEngine, recover_interrupted
    from sorta.planner import compute_plan_hash
    from sorta.scanner import Scanner
    from sorta.storage import IllegalPlanTransition

    plan = _load_plan_file(Path(args.plan_file))
    config = _load_env_and_config()
    store = _open_store(config)

    for _, index, note in recover_interrupted(store):
        print(f"[恢复] 批次内操作#{index}: {note}")

    registry, session = _restore_session(config, store, plan)
    _print_plan_overview(plan, session)
    errors = _validate_and_report(config, registry, plan)
    if errors:
        print(f"\n存在 {errors} 个 error，拒绝审批与执行。请重新执行 plan 生成新计划。")
        return 1

    plan_hash = compute_plan_hash(plan)
    plan_id = store.save_plan(plan.model_dump(mode="json"), plan_hash, status="draft")
    for status in ("validated", "awaiting_approval"):
        store.update_plan_status(plan_id, status)

    if not args.yes:
        try:
            answer = input(f"\n确认执行以上 {len(plan.operations)} 项操作？[y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("已取消，未执行任何操作。")
            store.update_plan_status(plan_id, "rejected")
            return 0

    approval = store.save_approval(plan_id, plan_hash)
    store.update_plan_status(plan_id, "approved")

    engine = ExecutionEngine(
        registry=registry, scanner=Scanner(registry, config), config=config, store=store
    )
    try:
        result = engine.execute(plan, approval_id=approval.id)
    except (SortaError, IllegalPlanTransition) as exc:
        print(f"\n执行中止: {exc}")
        return 1

    print(f"\n== 执行结果（execution_id={result.execution_id}，状态: {result.status}）==")
    for op in result.operations:
        mark = "✓" if op.status == "succeeded" else "✗"
        print(f"{mark} #{op.operation_index} [{op.action}] {op.source} -> {op.destination}")
        if op.error:
            print(f"  失败原因: {op.error}")
    if result.status in ("completed", "partial_failed"):
        print(f"\n如需撤销: uv run sorta undo {result.execution_id}")
    return 0 if result.status == "completed" else 1


def _cmd_history(args: argparse.Namespace) -> int:
    from sorta.executor import recover_interrupted

    config = _load_env_and_config()
    store = _open_store(config)

    for _, index, note in recover_interrupted(store):
        print(f"[恢复] 批次内操作#{index}: {note}")

    executions = store.list_executions()
    if not executions:
        print("（暂无执行历史）")
        return 0
    print(f"== 执行历史（{len(executions)} 个批次）==")
    for e in executions:
        ops = store.list_operations(e.id)
        ok = sum(1 for o in ops if o.status == "succeeded")
        undone = sum(1 for o in ops if o.status == "undone")
        print(
            f"{e.id}  {e.status:<14} {len(ops)} 项（成功 {ok}，已撤销 {undone}）"
            f"  开始 {_fmt_epoch(e.started_at)}  结束 {_fmt_epoch(e.finished_at)}"
        )
        print(f"    撤销: uv run sorta undo {e.id}")
    return 0


def _cmd_undo(args: argparse.Namespace) -> int:
    from sorta.errors import SortaError
    from sorta.executor import UndoEngine

    config = _load_env_and_config()
    store = _open_store(config)
    undo_engine = UndoEngine(store=store)
    try:
        result = undo_engine.undo_execution(args.execution_id)
    except SortaError as exc:
        print(f"错误: {exc}")
        return 1

    print(f"\n== 撤销结果（execution_id={result.execution_id}，状态: {result.status}）==")
    for op in result.operations:
        mark = "✓" if op.status == "undone" else "✗"
        print(f"{mark} #{op.operation_index} [{op.action}] {op.destination} -> {op.source}")
        if op.error:
            print(f"  冲突原因: {op.error}")
    if result.status == "partially_undone":
        print("\n存在冲突项，已保留现场；请人工核对后处理。")
    return 0 if result.status == "undone" else 1


def _cmd_ui(args: argparse.Namespace) -> int:
    """启动 Streamlit 图形界面（M4）。"""
    import subprocess
    import sys

    app_path = Path(__file__).resolve().parent / "ui" / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_path)]
    if args.headless:
        cmd += ["--server.headless", "true"]
    print("启动图形界面…（Ctrl+C 退出）")
    return subprocess.call(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sorta", description="SORTA — AI 智能文件整理助手")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="扫描授权目录并注册文件")
    p_scan.add_argument("root", help="授权根目录（所有操作局限在该目录内）")
    p_scan.add_argument("--depth", type=int, default=None, help="扫描深度（默认取配置上限）")
    p_scan.add_argument("--limit", type=int, default=None, help="本页文件数（默认取配置上限）")
    p_scan.add_argument("--all", action="store_true", help="包含隐藏文件")
    p_scan.set_defaults(func=_cmd_scan)

    p_plan = sub.add_parser("plan", help="自然语言生成整理计划（只读调查）")
    p_plan.add_argument("--root", required=True, help="授权根目录")
    p_plan.add_argument("--request", required=True, help="自然语言整理需求")
    p_plan.add_argument("--output", type=Path, default=None, help="计划 JSON 输出路径")
    p_plan.add_argument("--privacy", choices=PRIVACY_MODES, default=None, help="覆盖隐私模式")
    p_plan.set_defaults(func=_cmd_plan)

    p_inspect = sub.add_parser("inspect-plan", help="校验计划文件并预览问题（不执行）")
    p_inspect.add_argument("plan_file", help="计划 JSON 文件路径")
    p_inspect.set_defaults(func=_cmd_inspect_plan)

    p_apply = sub.add_parser("apply", help="校验、确认并执行计划")
    p_apply.add_argument("plan_file", help="计划 JSON 文件路径")
    p_apply.add_argument("--yes", action="store_true", help="跳过交互确认（谨慎使用）")
    p_apply.set_defaults(func=_cmd_apply)

    p_history = sub.add_parser("history", help="查看执行历史")
    p_history.set_defaults(func=_cmd_history)

    p_undo = sub.add_parser("undo", help="撤销一个执行批次（无冲突时尽力恢复）")
    p_undo.add_argument("execution_id", help="执行批次 ID（见 history）")
    p_undo.set_defaults(func=_cmd_undo)

    p_ui = sub.add_parser("ui", help="启动 Streamlit 图形界面")
    p_ui.add_argument("--headless", action="store_true", help="不自动打开浏览器")
    p_ui.set_defaults(func=_cmd_ui)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
