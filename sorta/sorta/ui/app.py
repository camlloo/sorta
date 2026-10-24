"""SORTA 图形界面（按《SORTA智能文件整理助手界面示意.png》实现）。

三页导航（示意图 ②）：对话 / 文件库 / 设置；右侧执行计划面板（示意图 ⑥⑦）；
对话式输入（示意图 ⑤）+ 能力卡片（示意图 ④）+ 已索引状态（示意图 ③）。

在 M4 表单式界面之上新增：对话式多轮交互、文件库详情与本地内容预览、
LLM 内容总结、哈希重复文件检测（只列出、不删除）、设置持久化。
业务逻辑与 CLI 共用，不复制安全逻辑。
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pandas as pd
import streamlit as st

from sorta.ui import logic

_PREVIEW_CHARS = 300


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


# ---- 设置持久化（示意图 ③：设置页保存扫描目录等基础选项） ----------------------


def _settings_file(config) -> Path:
    from sorta.storage import default_db_url

    url = config.db_url or default_db_url()
    prefix = "sqlite:///"
    db_path = Path(url[len(prefix) :]) if url.startswith(prefix) else Path.home() / ".sorta"
    return db_path.parent / "ui_settings.json"


def _load_settings(config) -> dict:
    settings_file = _settings_file(config)
    if settings_file.exists():
        try:
            return json.loads(settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def _save_settings(config, settings: dict) -> None:
    settings_file = _settings_file(config)
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _scan_root(config, root: str):
    """扫描目录并返回 (registry, session)。"""
    from sorta.scanner import FileRegistry, Scanner

    registry = FileRegistry(ttl_seconds=config.scan_session_ttl_seconds)
    page = Scanner(registry, config).scan(Path(root))
    return registry, registry.get_session(page.scan_id)


# ---- 对话流主入口（示意图 ⑤：用户消息 → 只读调查 → 计划 → 助手回复） ----------


def _submit_prompt(prompt: str, config, store) -> None:
    from sorta.agent import AgentController, OpenAICompatClient
    from sorta.errors import SortaError
    from sorta.planner import compute_plan_hash
    from sorta.scanner import Scanner

    st.session_state.chat_history.append({"role": "user", "content": prompt})
    root = st.session_state.get("root", "")
    privacy_mode = st.session_state.get("privacy", "authorized_content")

    try:
        run_config = replace(config, default_privacy=privacy_mode)
        if st.session_state.get("session") is None:
            with st.status("正在扫描工作目录…", expanded=True) as status:
                registry, session = _scan_root(run_config, root)
                status.update(label=f"扫描完成：{len(session.files)} 个文件", state="complete")
            st.session_state.registry, st.session_state.session = registry, session
            store.save_session(session)

        with st.status("SORTA 正在调查…", expanded=True) as status:
            controller = AgentController(
                registry=st.session_state.registry,
                scanner=Scanner(st.session_state.registry, run_config),
                config=run_config,
                llm=OpenAICompatClient(
                    api_key=os.environ["SORTA_API_KEY"],
                    base_url=os.environ.get("SORTA_BASE_URL") or None,
                ),
                on_tool_record=lambda record: status.write(
                    f"{'✓' if record.ok else '✗'} {record.name}: {record.summary}"
                ),
            )
            status.update(label=f"SORTA 正在调查（模型 {os.environ.get('SORTA_MODEL')}）…")
            result = controller.run(root, prompt, model=os.environ["SORTA_MODEL"])
            status.update(label=f"调查完成：{len(result.tool_log)} 次工具调用", state="complete")

        # controller.run 会创建自己的扫描会话；以它为准更新并落库（否则 plans 外键失败）
        session = st.session_state.registry.get_session(result.scan_id)
        st.session_state.session = session
        uncertain_names = [
            session.get(fid).relative_path if session.get(fid) else fid
            for fid in result.plan.uncertain_files
        ]
        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": logic.chat_reply_text(
                    summary=result.plan.summary,
                    tool_calls=len(result.tool_log),
                    operations=len(result.plan.operations),
                    uncertain=uncertain_names,
                ),
            }
        )

        st.session_state.plan = result.plan
        st.session_state.tool_log = [
            f"{'✓' if r.ok else '✗'} {r.name}: {r.summary}" for r in result.tool_log
        ]
        st.session_state.exec_result = None
        st.session_state.validated_plan = None
        st.session_state.issues = []
        st.session_state.plan_editor_key = f"plan_editor_{id(result.plan)}"
        if "plan_editor" in st.session_state:
            del st.session_state["plan_editor"]

        store.save_session(session)
        st.session_state.plan_id = store.save_plan(
            result.plan.model_dump(mode="json"), compute_plan_hash(result.plan), status="draft"
        )
    except SortaError as exc:
        st.session_state.chat_history.append({"role": "assistant", "content": f"调查失败：{exc}"})
    except Exception as exc:  # noqa: BLE001  UI 层兜底，把异常写进对话而不是白屏
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": f"调查失败：{type(exc).__name__}: {exc}",
        })


# ---- 计划表格（对话页右栏复用） ------------------------------------------------


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


def _validate_plan(config, plan) -> None:
    from sorta.planner import PlanValidator, has_errors

    validator = PlanValidator(st.session_state.registry, config)
    _, issues = validator.validate(plan)
    st.session_state.issues = issues
    st.session_state.validated_plan = None if has_errors(issues) else plan


# ---- 对话页（示意图 ①④⑤⑥⑦） -------------------------------------------------


def _render_plan_panel(config) -> None:
    from sorta.executor import UndoEngine
    from sorta.executor.flows import PlanAlreadyExecutedError, approve_and_execute
    from sorta.planner import compute_plan_hash
    from sorta.scanner import Scanner

    plan = st.session_state.plan
    executed = st.session_state.get("exec_result") is not None
    st.markdown("### 执行计划")
    for i, step in enumerate(
        logic.build_plan_steps(
            tool_calls=len(st.session_state.tool_log),
            operations=len(plan.operations),
            executed=executed,
        ),
        start=1,
    ):
        st.markdown(
            f"**{i}. {step['title']}**  \n<span style='color:gray'>{step['detail']}</span>",
            unsafe_allow_html=True,
        )

    st.divider()
    st.markdown("**预览**")
    edited_plan = _edited_plan_from_widget(plan)
    if edited_plan is None:
        st.session_state.validated_plan = None
        return
    validated = st.session_state.get("validated_plan")
    if validated is None or compute_plan_hash(validated) != compute_plan_hash(edited_plan):
        _validate_plan(config, edited_plan)
        edited_plan = st.session_state.validated_plan

    if edited_plan is not None:
        st.dataframe(
            [
                {"源": r["源路径"], "目标": r["目标路径"]}
                for r in _operations_df(edited_plan).to_dict("records")
            ],
            width="stretch",
            hide_index=True,
        )
        with st.expander("编辑计划（勾选剔除 / 修改目标路径）"):
            st.caption("内容变更会自动重新校验。")
            st.data_editor(
                _operations_df(edited_plan),
                key=st.session_state.plan_editor_key,
                num_rows="fixed",
                width="stretch",
                column_config={
                    "应用": st.column_config.CheckboxColumn("应用", default=True),
                    "源路径": st.column_config.TextColumn("源路径", disabled=True),
                    "目标路径": st.column_config.TextColumn("目标路径"),
                    "动作": st.column_config.TextColumn("动作", disabled=True),
                    "置信度": st.column_config.TextColumn("置信度", disabled=True),
                    "依据": st.column_config.TextColumn("依据", disabled=True, width="large"),
                },
            )

    issues = st.session_state.issues or []
    for issue in [i for i in issues if i.level == "error"]:
        where = f"操作#{issue.operation_index}" if issue.operation_index is not None else "计划"
        st.error(f"{where} {issue.code}: {issue.message}")
    for issue in [i for i in issues if i.level == "warning"]:
        where = f"操作#{issue.operation_index}" if issue.operation_index is not None else "计划"
        st.warning(f"{where} {issue.code}: {issue.message}")
    if not issues:
        st.success("校验通过：未发现问题。")

    result = st.session_state.get("exec_result")
    if result is None:
        col_confirm, col_cancel = st.columns(2)
        if col_confirm.button(
            "▶ 确认并执行",
            type="primary",
            disabled=edited_plan is None,
            use_container_width=True,
        ):
            try:
                st.session_state.exec_result = approve_and_execute(
                    edited_plan,
                    registry=st.session_state.registry,
                    scanner=Scanner(st.session_state.registry, config),
                    config=config,
                    store=st.session_state.store,
                )
            except PlanAlreadyExecutedError as exc:
                st.error(str(exc))
            st.rerun()
        if col_cancel.button("↩ 取消", use_container_width=True):
            plan_id = st.session_state.get("plan_id")
            if plan_id:
                with contextlib.suppress(Exception):
                    st.session_state.store.update_plan_status(plan_id, "rejected")
            st.session_state.plan = None
            st.session_state.validated_plan = None
            st.session_state.issues = []
            st.session_state.chat_history.append(
                {
                    "role": "assistant",
                    "content": logic.cancel_reply_text(len(plan.operations)),
                }
            )
            st.rerun()
        return

    if result.status == "completed":
        st.success(f"执行完成（{result.execution_id}）")
    else:
        st.error(f"执行状态：{result.status}（{result.execution_id}）")
    for op in result.operations:
        line = f"{'✅' if op.status == 'succeeded' else '❌'} {op.source} → {op.destination}"
        (st.write if op.status == "succeeded" else st.error)(line)
    if result.status in ("completed", "partial_failed") and st.button(
        "撤销该批次", use_container_width=True
    ):
            try:
                undo = UndoEngine(store=st.session_state.store).undo_execution(result.execution_id)
            except Exception as exc:  # noqa: BLE001
                st.error(f"撤销失败：{type(exc).__name__}: {exc}")
                return
            if undo.status == "undone":
                st.success(f"已全部撤销（{len(undo.operations)} 项）")
            else:
                st.warning("部分撤销：冲突项已保留现场，详见「文件库 → 执行历史」。")


def _render_chat_column() -> None:
    st.markdown("### 对话")
    history = st.session_state.get("chat_history", [])
    if not history:
        st.markdown(
            "<h1 style='text-align:center;margin-top:36px'>你好，<br>我是 <b>SORTA</b></h1>"
            "<p style='text-align:center;color:gray'>"
            "用自然语言，帮你查找、理解、整理和管理文件。</p>",
            unsafe_allow_html=True,
        )
        st.markdown("")
        for row in (logic.CAPABILITIES[0:2], logic.CAPABILITIES[2:4]):
            cols = st.columns(2)
            for col, card in zip(cols, row, strict=False):
                if col.button(
                    f"{card['icon']} **{card['title']}**  \n“{card['example']}”",
                    use_container_width=True,
                ):
                    _submit_prompt(card["example"], st.session_state.config, st.session_state.store)
                    st.rerun()
    else:
        for msg in history:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])
        with st.expander("最近一次调查的工具调用日志"):
            for line in st.session_state.get("tool_log", []):
                st.write(line)


def page_chat() -> None:
    config = st.session_state.config
    store = st.session_state.store
    left, right = st.columns([0.62, 0.38], gap="medium")
    with left:
        model = os.environ.get("SORTA_MODEL")
        st.markdown(f"**SORTA · {model or '未配置模型'}**")
        _render_chat_column()
    with right:
        if st.session_state.get("plan") is not None:
            _render_plan_panel(config)
        else:
            st.markdown("### 执行计划")
            st.info("发起对话后，SORTA 生成的文件操作计划会显示在这里，供你预览、编辑和确认。")

    api_ready = bool(os.environ.get("SORTA_API_KEY") and os.environ.get("SORTA_MODEL"))
    if not api_ready:
        st.error("缺少 SORTA_API_KEY / SORTA_MODEL：请复制 `.env.example` 为 `.env` 并填写。")
    prompt = st.chat_input(
        "输入你的文件需求，例如：帮我整理下载文件夹",
        disabled=not api_ready or not st.session_state.get("root"),
    )
    if prompt:
        _submit_prompt(prompt, config, store)
        st.rerun()


# ---- 文件库页（示意图 ③ + 新增：详情/总结/重复检测） ---------------------------


def page_files() -> None:
    config = st.session_state.config
    store = st.session_state.store
    st.markdown("### 文件库")
    session = st.session_state.get("session")
    registry = st.session_state.get("registry")
    if session is None or registry is None:
        st.info("尚未扫描工作目录：请在「设置」中选择目录并扫描，或在「对话」页直接发起整理。")
        return

    st.caption(f"已索引 {len(session.files)} 个文件（根目录 `{session.root}`）")
    query = st.text_input("搜索文件名", placeholder="例如：postman、笔记、pdf")
    rows = logic.filter_file_rows(logic.format_file_rows(session.files.values()), query)
    st.dataframe(rows, width="stretch", hide_index=True)

    st.markdown("#### 文件详情")
    paths = [r["路径"] for r in rows]
    if not paths:
        st.caption("没有匹配的文件。")
        return
    choice = st.selectbox("选择文件", paths, index=None, placeholder="选择一个文件查看详情")
    if choice:
        record = session.find_by_path(choice)
        if record is None:
            st.error("该文件不在当前索引中（可能已被移动或删除）。")
            return
        try:
            _, abs_path = registry.resolve_path(session.scan_id, record.id)
        except Exception as exc:  # noqa: BLE001
            st.error(f"无法访问该文件：{exc}")
            return
        st.write(
            f"类型 `.{record.extension or '-'}` · 大小 `{record.size}` 字节 · "
            f"修改时间 `{record.modified_ns}`"
        )

        preview_key = f"preview_{record.id}"
        if st.button("本地内容预览（不发送云端）", key=f"btn_preview_{record.id}"):
            from sorta.scanner.readers import read_file

            st.session_state[preview_key] = read_file(
                abs_path,
                max_chars=_PREVIEW_CHARS,
                max_bytes=config.max_file_bytes,
                file_id=record.id,
            )
        preview = st.session_state.get(preview_key)
        if preview is not None:
            if preview.status.value == "ok":
                st.text_area("内容预览（截断）", preview.content, height=150, disabled=True)
            else:
                st.caption(f"无法提取文本：{preview.error}")

        if st.button("📄 让 SORTA 总结此文件", key=f"btn_sum_{record.id}", type="primary"):
            _summarize_file(config, choice, abs_path, record)

    st.markdown("#### 重复文件检测")
    if st.button("✨ 按内容哈希查找重复文件"):
        from sorta.ui.logic import file_sha256

        hashes = []
        progress = st.progress(0.0)
        records = list(session.files.values())
        for i, record in enumerate(records):
            _, abs_path = registry.resolve_path(session.scan_id, record.id)
            hashes.append(
                (record.relative_path, file_sha256(abs_path, max_bytes=config.max_file_bytes) or "")
            )
            progress.progress((i + 1) / max(len(records), 1))
        st.session_state.dup_groups = logic.find_duplicate_groups(hashes)
    dup_groups = st.session_state.get("dup_groups")
    if dup_groups is not None:
        st.info(logic.duplicates_reply_text(dup_groups))
        for i, group in enumerate(dup_groups, start=1):
            st.write(f"**组 {i}**：" + "、".join(f"`{p}`" for p in group))

    _render_history(store)


def _summarize_file(config, choice: str, abs_path, record) -> None:
    from sorta.agent.llm import OpenAICompatClient
    from sorta.scanner.readers import ReadStatus, read_file
    from sorta.ui.logic import build_summary_messages, summarize_reply_text

    outcome = read_file(
        abs_path,
        max_chars=config.max_read_chars,
        max_bytes=config.max_file_bytes,
        file_id=record.id,
    )
    if outcome.status is not ReadStatus.OK:
        st.warning(f"无法提取文本：{outcome.error}")
        return
    llm = OpenAICompatClient(
        api_key=os.environ["SORTA_API_KEY"],
        base_url=os.environ.get("SORTA_BASE_URL") or None,
    )
    with st.spinner("SORTA 正在阅读并总结…"):
        answer = llm.chat(
            model=os.environ["SORTA_MODEL"],
            messages=build_summary_messages(
                outcome.content, "总结这份文件的主要内容，200 字以内。"
            ),
        ).content
    st.info(summarize_reply_text(choice, answer or "（模型未返回内容）"))


def _render_history(store) -> None:
    from sorta.executor import UndoEngine, recover_interrupted

    st.markdown("#### 执行历史与撤销")
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
    choice = st.selectbox("选择批次", [e.id for e in executions], key="history_select")
    if st.button("撤销该批次", key="history_undo"):
        try:
            undo = UndoEngine(store=store).undo_execution(choice)
        except Exception as exc:  # noqa: BLE001
            st.error(f"撤销失败：{type(exc).__name__}: {exc}")
            return
        undone = sum(1 for o in undo.operations if o.status == "undone")
        if undo.status == "undone":
            st.success(f"已全部撤销（{undone} 项）")
        else:
            st.warning(f"部分撤销：{undone}/{len(undo.operations)} 项成功，冲突项已保留现场")
        for op in undo.operations:
            line = f"{'✅' if op.status == 'undone' else '⚠️'} {op.destination} → {op.source}"
            (st.write if op.status == "undone" else st.warning)(
                line + (f"（{op.error}）" if op.error else "")
            )


def _fmt_epoch(epoch: float | None) -> str:
    from datetime import datetime

    if not epoch:
        return "-"
    return datetime.fromtimestamp(epoch).astimezone().strftime("%Y-%m-%d %H:%M")


# ---- 设置页（示意图 ③） -------------------------------------------------------


def page_settings() -> None:
    config = st.session_state.config
    store = st.session_state.store
    st.markdown("### 设置")
    st.markdown("#### 工作目录")
    root = st.text_input(
        "授权根目录", value=st.session_state.get("root", ""), placeholder=r"例如 D:\Downloads"
    )
    privacy = st.radio(
        "隐私模式（发送给云端模型的内容）",
        ["仅元数据（不发送文件内容）", "授权读取内容（文件文本将发送给云端模型）"],
        index=0 if st.session_state.get("privacy") == "metadata_only" else 1,
    )
    privacy_mode = "metadata_only" if privacy.startswith("仅元数据") else "authorized_content"
    st.caption(
        "metadata_only：只发送文件名与元数据；authorized_content：发送限长文本提取。"
        "所有文件操作仅限该目录内，执行前必须经你确认。"
    )

    if st.button("保存并扫描", type="primary"):
        try:
            registry, session = _scan_root(config, root)
        except Exception as exc:  # noqa: BLE001
            st.error(f"扫描失败：{type(exc).__name__}: {exc}")
            return
        st.session_state.root = root
        st.session_state.privacy = privacy_mode
        st.session_state.registry = registry
        st.session_state.session = session
        store.save_session(session)
        _save_settings(config, {"root": root, "privacy": privacy_mode})
        st.success(f"已保存并索引 {len(session.files)} 个文件。")
        st.rerun()

    st.markdown("#### 模型与接口")
    model = os.environ.get("SORTA_MODEL")
    if model:
        st.success(
            f"模型：`{model}` · 接口：`{os.environ.get('SORTA_BASE_URL') or '默认 OpenAI 接口'}`"
        )
    else:
        st.error("未配置模型：请复制 `.env.example` 为 `.env` 并填写 SORTA_MODEL / SORTA_API_KEY。")
    st.caption(
        f"扫描会话有效期：{config.scan_session_ttl_seconds} 秒；"
        f"单文件读取上限：{config.max_file_bytes} 字节；"
        f"数据库：`{config.db_url or '系统数据目录'}`"
    )


# ---- 入口 --------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="SORTA — AI 文件整理助手", page_icon="🗂️", layout="wide")
    config = _load_config()
    st.session_state.config = config
    st.session_state.store = _get_store(config.db_url)
    st.session_state.settings = _load_settings(config)
    if "root" not in st.session_state:
        st.session_state.root = st.session_state.settings.get("root", "")
    if "privacy" not in st.session_state:
        st.session_state.privacy = st.session_state.settings.get("privacy", "authorized_content")
    for key, default in (
        ("chat_history", []),
        ("tool_log", []),
        ("issues", []),
        ("validated_plan", None),
        ("exec_result", None),
        ("plan", None),
        ("plan_id", None),
    ):
        if key not in st.session_state:
            st.session_state[key] = default

    with st.sidebar:
        st.markdown(
            "# SORTA\n"
            "<span style='color:gray;letter-spacing:2px;font-size:0.72em'>"
            "AI FILE ORGANIZER</span>",
            unsafe_allow_html=True,
        )

    def _run_chat():
        page_chat()

    def _run_files():
        page_files()

    def _run_settings():
        page_settings()

    nav = st.navigation(
        [
            st.Page(_run_chat, title="对话", icon="💬", default=True),
            st.Page(_run_files, title="文件库", icon="🗂️"),
            st.Page(_run_settings, title="设置", icon="⚙️"),
        ]
    )
    nav.run()

    session = st.session_state.get("session")
    with st.sidebar:
        st.divider()
        if session is not None:
            st.markdown(f"🟢 **已索引 {len(session.files)} 个文件**")
            st.caption(str(st.session_state.get("root", "")))
        else:
            st.markdown("⚪ 未扫描目录")
            st.caption("在「设置」中选择工作目录")


if __name__ == "__main__":
    main()
