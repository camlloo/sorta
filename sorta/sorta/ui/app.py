"""SORTA Streamlit 图形界面（M4，开发文档 §11.1）。

启动方式：uv run sorta ui（或 uv run streamlit run sorta/ui/app.py）。
布局对应 §11.1 五个区域：Workspace（侧栏）/ Chat / Plan Review / Execution /
History & Undo。业务逻辑全部复用既有模块：调查走 AgentController，编辑走
planner.editing.apply_edits，审批执行走 executor.flows.approve_and_execute。
"""

from __future__ import annotations

import os
from dataclasses import replace

import pandas as pd
import streamlit as st


@st.cache_resource
def _get_store(db_url: str | None):
    from sorta.storage import PlanStore, create_db_engine, init_db

    engine = create_db_engine(db_url)
    init_db(engine)
    return PlanStore(engine)


def _load_config():
    from dotenv import load_dotenv

    from sorta.config import SortaConfig

    load_dotenv()
    return SortaConfig.from_env()


def _privacy_to_mode(choice: str) -> str:
    return "metadata_only" if choice.startswith("仅元数据") else "authorized_content"


def _fmt_epoch(epoch: float | None) -> str:
    from datetime import datetime

    if not epoch:
        return "-"
    return datetime.fromtimestamp(epoch).astimezone().strftime("%m-%d %H:%M:%S")


def _operations_df(plan) -> pd.DataFrame:
    session = st.session_state.session
    rows = []
    for op in plan.operations:
        record = session.get(op.source_id) if session else None
        rows.append(
            {
                "应用": True,
                "源路径": record.relative_path if record else f"<未知 {op.source_id}>",
                "目标路径": op.destination,
                "动作": op.action.value,
                "置信度": op.confidence.value,
                "依据": op.reason,
            }
        )
    return pd.DataFrame(rows, columns=["应用", "源路径", "目标路径", "动作", "置信度", "依据"])


def _edited_plan_from_widget(plan):
    """从 data_editor 当前值重建计划；编辑非法时提示并返回 None。"""
    from sorta.planner.editing import apply_edits

    edited = st.session_state.get("plan_editor")
    if edited is None:
        return plan
    try:
        return apply_edits(
            plan,
            keep=[bool(v) for v in edited["应用"]],
            destinations=[str(v) for v in edited["目标路径"]],
        )
    except ValueError as exc:  # 含 pydantic ValidationError
        st.error(f"计划编辑非法，未应用：{exc}")
        return None


def _run_investigation(root: str, request: str, privacy_mode: str, tool_status) -> None:
    from sorta.agent import AgentController, OpenAICompatClient
    from sorta.planner import compute_plan_hash
    from sorta.scanner import FileRegistry, Scanner

    config = replace(st.session_state.config, default_privacy=privacy_mode)
    registry = FileRegistry(ttl_seconds=config.scan_session_ttl_seconds)
    controller = AgentController(
        registry=registry,
        scanner=Scanner(registry, config),
        config=config,
        llm=OpenAICompatClient(
            api_key=os.environ["SORTA_API_KEY"],
            base_url=os.environ.get("SORTA_BASE_URL") or None,
        ),
        on_tool_record=lambda record: tool_status.write(
            f"{'✓' if record.ok else '✗'} #{record.index} {record.name}: {record.summary}"
        ),
    )
    tool_status.update(label=f"Agent 调查中（模型 {os.environ.get('SORTA_MODEL')}）…")
    result = controller.run(root, request, model=os.environ["SORTA_MODEL"])
    tool_status.update(label=f"调查完成：{len(result.tool_log)} 次工具调用", state="complete")

    st.session_state.registry = registry
    st.session_state.session = registry.get_session(result.scan_id)
    st.session_state.plan = result.plan
    st.session_state.tool_log = [
        f"{'✓' if r.ok else '✗'} #{r.index} {r.name}: {r.summary}" for r in result.tool_log
    ]
    st.session_state.exec_result = None
    st.session_state.validated_plan = None
    st.session_state.issues = []

    # 与 CLI 行为一致：会话与计划落库，跨进程 apply 也能用
    store = st.session_state.store
    store.save_session(st.session_state.session)
    st.session_state.plan_id = store.save_plan(
        result.plan.model_dump(mode="json"), compute_plan_hash(result.plan), status="draft"
    )
    st.session_state.plan_editor_key = f"plan_editor_{id(result.plan)}"


def _render_plan_review(config) -> None:
    from sorta.planner import PlanValidator, compute_plan_hash, has_errors

    plan = st.session_state.plan
    st.subheader("2. 计划审查")
    with st.expander(f"只读工具调用日志（{len(st.session_state.tool_log)} 次）", expanded=False):
        for line in st.session_state.tool_log:
            st.write(line)

    st.caption("可在表格中取消勾选以剔除操作、直接修改目标路径；内容变更会自动重新校验。")
    st.data_editor(
        _operations_df(plan),
        key=st.session_state.plan_editor_key,
        num_rows="fixed",
        width="stretch",
        column_config={
            "应用": st.column_config.CheckboxColumn("应用", default=True),
            "源路径": st.column_config.TextColumn("源路径", disabled=True),
            "目标路径": st.column_config.TextColumn("目标路径", help="相对根目录，正斜杠分隔"),
            "动作": st.column_config.TextColumn("动作", disabled=True),
            "置信度": st.column_config.TextColumn("置信度", disabled=True),
            "依据": st.column_config.TextColumn("依据", disabled=True, width="large"),
        },
    )

    edited_plan = _edited_plan_from_widget(plan)
    if edited_plan is None:
        st.session_state.validated_plan = None
        return

    # 计划内容变化（含首次）即自动重新校验；按钮用于显式触发 + 反馈
    validated = st.session_state.get("validated_plan")
    content_changed = validated is None or compute_plan_hash(validated) != compute_plan_hash(
        edited_plan
    )
    if content_changed:
        validator = PlanValidator(st.session_state.registry, config)
        _, issues = validator.validate(edited_plan)
        st.session_state.issues = issues
        st.session_state.validated_plan = None if has_errors(issues) else edited_plan
    if st.button("重新校验"):
        st.toast("已重新校验")

    issues = st.session_state.issues or []
    for issue in [i for i in issues if i.level == "error"]:
        where = f"操作#{issue.operation_index}" if issue.operation_index is not None else "计划"
        st.error(f"{where} {issue.code}: {issue.message}")
    for issue in [i for i in issues if i.level == "warning"]:
        where = f"操作#{issue.operation_index}" if issue.operation_index is not None else "计划"
        st.warning(f"{where} {issue.code}: {issue.message}")
    if not issues:
        st.success("校验通过：未发现问题。")
    if edited_plan.uncertain_files:
        session = st.session_state.session
        names = [
            session.get(fid).relative_path if session.get(fid) else fid
            for fid in edited_plan.uncertain_files
        ]
        st.info("待确认文件（不移动）：" + "、".join(names))


def _render_execution(config) -> None:
    from sorta.executor.flows import PlanAlreadyExecutedError, approve_and_execute
    from sorta.scanner import Scanner

    st.subheader("3. 执行")
    validated = st.session_state.get("validated_plan")
    disabled = validated is None or len(validated.operations) == 0
    if disabled:
        st.caption("请先完成校验（至少保留一项操作）后执行。")
    if st.button("批准并执行", type="primary", disabled=disabled):
        try:
            result = approve_and_execute(
                validated,
                registry=st.session_state.registry,
                scanner=Scanner(st.session_state.registry, config),
                config=config,
                store=st.session_state.store,
            )
        except PlanAlreadyExecutedError as exc:
            st.error(str(exc))
            return
        st.session_state.exec_result = result
        st.toast("执行成功" if result.status == "completed" else "执行完成（有失败项）")

    result = st.session_state.exec_result
    if result is None:
        return
    if result.status == "completed":
        st.success(f"执行完成（execution_id={result.execution_id}）")
    else:
        st.error(f"执行状态：{result.status}（execution_id={result.execution_id}）")
    for op in result.operations:
        line = (
            f"{'✅' if op.status == 'succeeded' else '❌'} #{op.operation_index} "
            f"[{op.action}] {op.source} → {op.destination}"
        )
        if op.status == "succeeded":
            st.write(line)
        else:
            st.error(line + (f"（{op.error}）" if op.error else ""))


def _render_history() -> None:
    from sorta.executor import UndoEngine, recover_interrupted

    st.divider()
    st.subheader("历史与撤销")
    store = st.session_state.store
    for _, index, note in recover_interrupted(store):
        st.warning(f"[恢复] 操作#{index}: {note}")

    executions = store.list_executions()
    if not executions:
        st.caption("暂无执行历史。")
        return
    st.dataframe(
        [
            {
                "批次": e.id,
                "状态": e.status,
                "开始": _fmt_epoch(e.started_at),
                "结束": _fmt_epoch(e.finished_at),
            }
            for e in executions
        ],
        width="stretch",
        hide_index=True,
    )
    choice = st.selectbox("选择批次", [e.id for e in executions])
    if st.button("撤销该批次"):
        try:
            undo = UndoEngine(store=store).undo_execution(choice)
        except Exception as exc:
            st.error(f"撤销失败：{type(exc).__name__}: {exc}")
            return
        undone = sum(1 for o in undo.operations if o.status == "undone")
        if undo.status == "undone":
            st.success(f"已全部撤销（{undone} 项）")
        else:
            st.warning(f"部分撤销：{undone}/{len(undo.operations)} 项成功，冲突项已保留现场")
        for op in undo.operations:
            line = (
                f"{'✅' if op.status == 'undone' else '⚠️'} #{op.operation_index} "
                f"{op.destination} → {op.source}"
            )
            if op.status == "undone":
                st.write(line)
            else:
                st.warning(line + (f"（{op.error}）" if op.error else ""))


def main() -> None:
    st.set_page_config(page_title="SORTA", page_icon="🗂️", layout="wide")
    st.title("🗂️ SORTA — AI 文件整理")

    config = _load_config()
    st.session_state.config = config
    st.session_state.store = _get_store(config.db_url)

    # ---- Workspace（侧栏，§11.1 区域 1） ----
    with st.sidebar:
        st.header("工作目录")
        root = st.text_input(
            "授权根目录", value=st.session_state.get("root", ""), placeholder=r"例如 D:\Downloads"
        )
        st.session_state.root = root
        privacy_choice = st.radio(
            "隐私模式（发送给云端模型的内容）",
            ["仅元数据（不发送文件内容）", "授权读取内容（文件文本将发送给云端模型）"],
            index=1,
        )
        st.caption(
            "metadata_only：只发送文件名与元数据；authorized_content：发送限长文本。"
            "CLI 支持 ask_before_content 逐文件确认模式。"
        )
        if root:
            try:
                from sorta.scanner import FileRegistry, Scanner

                page = Scanner(FileRegistry(), config).scan(root, limit=20)
                st.caption(
                    f"扫描预览：{len(page.files)} 个文件（最多显示 20）"
                    + ("，还有更多" if page.has_more else "")
                )
            except Exception as exc:
                st.error(f"无法扫描该目录：{type(exc).__name__}: {exc}")

    # ---- Chat / Instruction（§11.1 区域 2） ----
    st.subheader("1. 整理需求")
    request = st.text_area(
        "用自然语言描述整理目标",
        value=st.session_state.get("request", ""),
        placeholder=(
            "例如：把与 Web 安全、Postman 作业相关的文件整理到 Security-Learning，不确定的保留原处"
        ),
        height=90,
    )
    st.session_state.request = request
    api_ready = bool(os.environ.get("SORTA_API_KEY") and os.environ.get("SORTA_MODEL"))
    if not api_ready:
        st.error("缺少 SORTA_API_KEY / SORTA_MODEL（请复制 .env.example 为 .env 并填写）。")

    if st.button("开始调查", type="primary", disabled=not (root and request and api_ready)):
        with st.status("准备调查…", expanded=True) as tool_status:
            try:
                _run_investigation(root, request, _privacy_to_mode(privacy_choice), tool_status)
            except Exception as exc:
                tool_status.update(label="调查失败", state="error")
                st.error(f"{type(exc).__name__}: {exc}")

    # ---- Plan Review + Execution（§11.1 区域 3/4） ----
    if st.session_state.get("plan") is not None:
        _render_plan_review(config)
        _render_execution(config)

    # ---- History & Undo（§11.1 区域 5） ----
    _render_history()


if __name__ == "__main__":
    main()
