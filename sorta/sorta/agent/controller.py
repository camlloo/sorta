"""Agent Controller：手写 Tool Calling 有限循环（开发文档 §4.3）。

不使用任何 Agent 框架（§3.3）：循环控制、工具分发、步数/次数上限全部在此
显式实现。流程：系统创建扫描会话 → 模型在只读工具与 submit_plan 之间选择 →
计划通过结构校验且 scan_id 匹配后返回。模型拿不到任何文件写接口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sorta.agent.llm import (
    LLMClient,
    assistant_message,
    system_message,
    tool_message,
    user_message,
)
from sorta.agent.prompts import build_system_prompt, build_user_message
from sorta.agent.schemas import ExecutionPlan
from sorta.agent.tools import PLAN_TOOL, ConsentCallback, ToolCallRecord, ToolDispatcher
from sorta.config import SortaConfig
from sorta.errors import SortaError
from sorta.scanner.registry import FileRegistry
from sorta.scanner.scanner import Scanner


class AgentProtocolError(SortaError):
    """模型未返回有效工具调用或整理计划（§4.3）。"""


class AgentStepLimitError(SortaError):
    """Agent 已达到最大调查步数（§4.3）。"""


class ToolCallLimitError(SortaError):
    """工具调用总次数超过上限（§10.2 失控工具调用防护）。"""


@dataclass
class AgentRunResult:
    plan: ExecutionPlan
    scan_id: str
    steps_used: int
    tool_log: list[ToolCallRecord] = field(default_factory=list)


class AgentController:
    def __init__(
        self,
        *,
        registry: FileRegistry,
        scanner: Scanner,
        config: SortaConfig,
        llm: LLMClient,
        consent_callback: ConsentCallback | None = None,
        on_tool_record: Any = None,
    ):
        self.registry = registry
        self.scanner = scanner
        self.config = config
        self.llm = llm
        self.consent_callback = consent_callback
        self.on_tool_record = on_tool_record  # 每次工具调用完成后的回调（UI 实时日志用）

    def run(self, root: str | Path, request: str, *, model: str) -> AgentRunResult:
        """执行一次完整调查并返回计划。只读；不改任何文件。"""
        session = self.registry.create_session(Path(root))
        dispatcher = ToolDispatcher(
            registry=self.registry,
            session=session,
            scanner=self.scanner,
            config=self.config,
            consent_callback=self.consent_callback,
        )
        root_display = str(session.root)
        messages: list[dict[str, Any]] = [
            system_message(build_system_prompt(self.config, root_display)),
            user_message(build_user_message(request, root_display)),
        ]

        plan: ExecutionPlan | None = None
        steps_used = 0
        for _ in range(self.config.max_agent_steps):
            steps_used += 1
            response = self.llm.chat(model=model, messages=messages, tools=dispatcher.specs)
            if not response.tool_calls:
                raise AgentProtocolError("模型未返回有效工具调用或整理计划")
            messages.append(assistant_message(response))

            batch_plan: ExecutionPlan | None = None
            for call in response.tool_calls:
                if len(dispatcher.log) >= self.config.max_tool_calls:
                    raise ToolCallLimitError(
                        f"工具调用次数超过上限 {self.config.max_tool_calls}，已中止"
                    )
                if call.name == PLAN_TOOL:
                    record, accepted = self._handle_submit_plan(dispatcher, call, session.scan_id)
                    if accepted is not None:
                        batch_plan = accepted
                else:
                    record = dispatcher.dispatch(call.name, call.arguments)
                messages.append(tool_message(call.id, record.result_json))
                if self.on_tool_record is not None:
                    self.on_tool_record(record)
            if batch_plan is not None:
                plan = batch_plan
                break
        if plan is None:
            raise AgentStepLimitError(
                f"Agent 已达到最大调查步数（{self.config.max_agent_steps}），未收到有效计划"
            )
        return AgentRunResult(
            plan=plan, scan_id=session.scan_id, steps_used=steps_used, tool_log=list(dispatcher.log)
        )

    def _handle_submit_plan(
        self, dispatcher: ToolDispatcher, call: Any, scan_id: str
    ) -> tuple[ToolCallRecord, ExecutionPlan | None]:
        try:
            plan = ExecutionPlan.model_validate_json(call.arguments or "{}")
        except ValidationError as exc:
            details = [
                {
                    "loc": [str(loc) for loc in error["loc"]],
                    "msg": error["msg"],
                    "type": error["type"],
                }
                for error in exc.errors(include_url=False)[:10]
            ]
            record = dispatcher.log_external(
                PLAN_TOOL,
                call.arguments,
                ok=False,
                summary="计划结构校验失败",
                result={"error": "计划结构校验失败，请修正后重新提交", "details": details},
            )
            return record, None
        if plan.scan_id != scan_id:
            record = dispatcher.log_external(
                PLAN_TOOL,
                call.arguments,
                ok=False,
                summary="计划 scan_id 与当前扫描会话不符",
                result={
                    "error": f"plan.scan_id 必须是当前会话 {scan_id}，收到 {plan.scan_id!r}；"
                    "请先调用 list_files 获取 scan_id"
                },
            )
            return record, None
        record = dispatcher.log_external(
            PLAN_TOOL,
            call.arguments,
            ok=True,
            summary=(
                f"计划已接收：{len(plan.operations)} 项操作，"
                f"{len(plan.uncertain_files)} 个待确认文件"
            ),
            result={"accepted": True, "operations": len(plan.operations)},
        )
        return record, plan
