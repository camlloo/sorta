"""API 路由：健康/会话/设置、工作区与文件、计划与 Job、执行与撤销。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from sorta.api.deps import make_llm, require_token
from sorta.config import PRIVACY_MODES

# ---- 请求模型 ---------------------------------------------------------------


class WorkspaceRequest(BaseModel):
    root: str = Field(min_length=1)


class PlanRequest(BaseModel):
    scan_id: str = Field(min_length=1)
    request: str = Field(min_length=1)
    privacy: str | None = None


class PlanEditRequest(BaseModel):
    keep: list[bool]
    destinations: list[str]
    expected_version: int


class ApproveRequest(BaseModel):
    expected_version: int
    expected_plan_hash: str


class ExecutionRequest(BaseModel):
    approval_id: int


class TaskRequest(BaseModel):
    scan_id: str = Field(min_length=1)
    request: str = Field(min_length=1)
    task_type: str = "auto"  # auto / find / ask / organize
    template_id: int | None = None
    conversation_id: int | None = None


class ConversationCreateRequest(BaseModel):
    scan_id: str = Field(min_length=1)
    title: str = ""


class TemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    instruction: str = Field(min_length=1)


class TemplateUpdateRequest(BaseModel):
    name: str | None = None
    instruction: str | None = None
    enabled: bool | None = None


class SettingsRequest(BaseModel):
    root: str | None = None
    privacy: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None


# ---- system（health/session 免鉴权；settings 需鉴权） -------------------------

system_router = APIRouter()


@system_router.get("/api/v1/health")
def health(request: Request) -> dict:
    return {"status": "ok", "service": "sorta-api"}


@system_router.get("/api/v1/session")
def session(request: Request) -> dict:
    """前端引导端点：发放会话 token（仅限同源页面；开发模式受 CORS 约束）。"""
    state = request.app.state
    sec_fetch_site = request.headers.get("sec-fetch-site", "")
    if not state.allow_cross_site_session and sec_fetch_site == "cross-site":
        raise HTTPException(
            status_code=403,
            detail={"code": "UNAUTHORIZED", "message": "拒绝跨站会话请求"},
        )
    llm = state.service.get_llm_config()
    return {
        "token": state.api_token,
        "model": llm["model"],
        "base_url": llm["base_url"],
        "has_api_key": bool(llm["api_key"]),
    }


@system_router.post("/api/v1/settings/test-connection", dependencies=[Depends(require_token)])
def test_connection(request: Request) -> dict:
    """模型连接测试（UX-07）：最小请求，不发送任何用户文件。

    分两步检查：普通对话连通性；工具调用兼容性（规划功能依赖 tools 参数，
    不能只凭普通文本响应判定可用）。区分鉴权失败、端点不可达、模型不存在。
    """
    import time as _time

    model, llm = make_llm(request)
    cfg = request.app.state.service.get_llm_config()

    def _classify(exc: Exception) -> tuple[str, str]:
        import openai

        if isinstance(exc, openai.AuthenticationError):
            return "AUTH_FAILED", "鉴权失败：API Key 无效或已过期"
        if isinstance(exc, openai.NotFoundError):
            return "MODEL_NOT_FOUND", "模型不存在或 Base URL 路径不正确"
        if isinstance(exc, openai.RateLimitError):
            return "RATE_LIMITED", "触发限流：请稍后重试"
        if isinstance(exc, openai.APIConnectionError):
            return "ENDPOINT_UNREACHABLE", "端点不可达：请检查 Base URL 与网络连接"
        if isinstance(exc, openai.BadRequestError):
            return "BAD_REQUEST", f"请求被端点拒绝：{exc}"
        if isinstance(exc, openai.PermissionDeniedError):
            return "PERMISSION_DENIED", "权限不足：该 Key 无权访问此模型"
        return "UNKNOWN", f"{type(exc).__name__}: {exc}"

    def _check(label: str, messages: list, tools: list | None) -> dict:
        started = _time.perf_counter()
        try:
            llm.chat(model=model, messages=messages, tools=tools)
        except Exception as exc:  # noqa: BLE001  测试端点：异常分类后如实报告
            code, message = _classify(exc)
            return {
                "ok": False,
                "code": code,
                "message": message,
                "latency_ms": int((_time.perf_counter() - started) * 1000),
            }
        return {
            "ok": True,
            "code": None,
            "message": f"{label}正常",
            "latency_ms": int((_time.perf_counter() - started) * 1000),
        }

    chat_check = _check(
        "对话连接",
        [{"role": "user", "content": "连接测试：请直接回复 OK，不要输出其他内容。"}],
        None,
    )
    probe_tool = {
        "type": "function",
        "function": {
            "name": "echo_probe",
            "description": "兼容性探测用的最小工具",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
    tools_check = _check(
        "工具调用兼容性",
        [{"role": "user", "content": "连接测试：请勿调用任何工具，直接回复 OK。"}],
        [probe_tool],
    )

    return {
        "ok": chat_check["ok"] and tools_check["ok"],
        "planning_ready": chat_check["ok"] and tools_check["ok"],
        "model": model,
        "base_url": cfg["base_url"],
        "checks": {"chat": chat_check, "tools": tools_check},
    }


@system_router.get("/api/v1/settings", dependencies=[Depends(require_token)])
def get_settings(request: Request) -> dict:
    return request.app.state.service.get_settings()


@system_router.put("/api/v1/settings", dependencies=[Depends(require_token)])
def put_settings(body: SettingsRequest, request: Request) -> dict:
    if body.privacy is not None and body.privacy not in PRIVACY_MODES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "VALIDATION_ERROR",
                "message": f"privacy 必须是 {'/'.join(PRIVACY_MODES)} 之一",
            },
        )
    current = request.app.state.service.get_settings()
    return request.app.state.service.save_settings(
        root=body.root if body.root is not None else current["root"],
        privacy=body.privacy if body.privacy is not None else current["privacy"],
        model=body.model,
        base_url=body.base_url,
        api_key=body.api_key,
    )


# ---- 工作区与文件（示意图 ③ 文件库） -----------------------------------------

workspaces_router = APIRouter(dependencies=[Depends(require_token)])


@workspaces_router.post("/api/v1/workspaces")
def create_workspace(body: WorkspaceRequest, request: Request) -> dict:
    info = request.app.state.service.create_workspace(body.root)
    return {
        "scan_id": info.scan_id,
        "root": info.root,
        "file_count": info.file_count,
        "created_at": info.created_at,
        "expires_at": info.expires_at,
        "has_more": info.has_more,
        "next_cursor": info.next_cursor,
        "skipped_directories": info.skipped_directories or [],
    }


@workspaces_router.post("/api/v1/scans/{scan_id}/rescan")
def rescan_workspace(scan_id: str, request: Request) -> dict:
    """对既有工作区重新扫描：新快照用于浏览，旧计划/日志依据不变（UX-04）。"""
    info = request.app.state.service.rescan(scan_id)
    return {
        "scan_id": info.scan_id,
        "root": info.root,
        "file_count": info.file_count,
        "created_at": info.created_at,
        "expires_at": info.expires_at,
        "has_more": info.has_more,
        "next_cursor": info.next_cursor,
        "skipped_directories": info.skipped_directories or [],
    }


@workspaces_router.get("/api/v1/scans/{scan_id}/files")
def list_files(scan_id: str, request: Request, query: str = "") -> dict:
    rows = request.app.state.service.list_files(scan_id, query)
    return {"scan_id": scan_id, "count": len(rows), "files": rows}


@workspaces_router.post("/api/v1/scans/{scan_id}/files/{file_id}/preview")
def file_preview(scan_id: str, file_id: str, request: Request) -> dict:
    outcome = request.app.state.service.file_preview(scan_id, file_id)
    return outcome.model_dump()


class SummaryRequest(BaseModel):
    confirm_content_access: bool = False


@workspaces_router.post("/api/v1/scans/{scan_id}/files/{file_id}/summary")
def file_summary(
    scan_id: str, file_id: str, request: Request, body: SummaryRequest | None = None
) -> dict:
    confirm = bool(body.confirm_content_access) if body else False
    model, llm = make_llm(request)
    summary = request.app.state.service.summarize_file(
        scan_id,
        file_id,
        llm=llm,
        model=model,
        confirm_content_access=confirm,
    )
    return {"summary": summary}


@workspaces_router.post("/api/v1/scans/{scan_id}/duplicates")
def find_duplicates(scan_id: str, request: Request) -> dict:
    return request.app.state.service.find_duplicates(scan_id)


# ---- 计划与 Job（示意图 ⑥ 执行计划面板） --------------------------------------

plans_router = APIRouter(dependencies=[Depends(require_token)])


@plans_router.post("/api/v1/plans")
def create_plan(body: PlanRequest, request: Request) -> dict:
    """启动计划生成（长耗时）：返回 job_id，前端轮询 GET /jobs/{job_id}。"""
    if body.privacy is not None and body.privacy not in PRIVACY_MODES:
        raise HTTPException(
            status_code=422, detail={"code": "VALIDATION_ERROR", "message": "privacy 不合法"}
        )
    service = request.app.state.service
    jobs = request.app.state.jobs
    model, llm = make_llm(request)
    privacy = body.privacy or request.app.state.service.get_settings()["privacy"]

    def _run(job_id: str, cancel_event) -> dict:
        record = service.generate_plan(
            body.scan_id,
            body.request,
            privacy=privacy,
            model=model,
            llm=llm,
            on_tool_record=lambda rec: jobs.add_event(
                job_id, f"{'✓' if rec.ok else '✗'} {rec.name}: {rec.summary}"
            ),
            cancel_event=cancel_event,
        )
        return {
            "plan_id": record.id,
            "scan_id": record.scan_id,
            "version": record.version,
            "plan_hash": record.plan_hash,
        }

    job_id = jobs.submit("plan", _run)
    return {"job_id": job_id}


@plans_router.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    snapshot = request.app.state.jobs.get(job_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"}
        )
    return snapshot


@plans_router.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    canceled = request.app.state.jobs.cancel(job_id)
    return {"canceled": canceled}


@plans_router.get("/api/v1/plans/{plan_id}")
def get_plan(plan_id: int, request: Request) -> dict:
    detail = request.app.state.service.get_plan_detail(plan_id)
    return _plan_detail_dict(detail)


@plans_router.patch("/api/v1/plans/{plan_id}")
def edit_plan(plan_id: int, body: PlanEditRequest, request: Request) -> dict:
    detail = request.app.state.service.edit_plan(
        plan_id,
        keep=body.keep,
        destinations=body.destinations,
        expected_version=body.expected_version,
    )
    return _plan_detail_dict(detail)


@plans_router.post("/api/v1/plans/{plan_id}/validate")
def validate_plan(plan_id: int, request: Request) -> dict:
    issues = request.app.state.service.validate_plan(plan_id)
    return {
        "issues": [issue.model_dump() for issue in issues],
        "has_errors": any(i.level == "error" for i in issues),
    }


@plans_router.post("/api/v1/plans/{plan_id}/approve")
def approve_plan(plan_id: int, body: ApproveRequest, request: Request) -> dict:
    return request.app.state.service.approve_plan(
        plan_id,
        expected_version=body.expected_version,
        expected_plan_hash=body.expected_plan_hash,
    )


class PlanRevisionRequest(BaseModel):
    instruction: str = Field(min_length=1)
    expected_version: int


@plans_router.post("/api/v1/plans/{plan_id}/revisions")
def revise_plan(plan_id: int, body: PlanRevisionRequest, request: Request) -> dict:
    """自然语言修改当前计划（UX-01）：返回 job_id，轮询获取新版本。"""
    model, llm = make_llm(request)
    service = request.app.state.service
    jobs = request.app.state.jobs

    def _run(job_id: str, cancel_event) -> dict:
        detail = service.revise_plan(
            plan_id,
            body.instruction,
            model=model,
            llm=llm,
            expected_version=body.expected_version,
        )
        del job_id, cancel_event
        return {
            "plan_id": detail.plan_id,
            "scan_id": detail.scan_id,
            "version": detail.version,
            "plan_hash": detail.plan_hash,
        }

    job_id = jobs.submit("plan-revision", _run)
    return {"job_id": job_id}


@plans_router.post("/api/v1/plans/{plan_id}/reject")
def reject_plan(plan_id: int, request: Request) -> dict:
    request.app.state.service.reject_plan(plan_id)
    return {"rejected": True}


def _plan_detail_dict(detail) -> dict:
    return {
        "plan_id": detail.plan_id,
        "scan_id": detail.scan_id,
        "version": detail.version,
        "plan_hash": detail.plan_hash,
        "status": detail.status,
        "summary": detail.summary,
        "operations": detail.operations,
        "uncertain_files": detail.uncertain_files,
        "uncertain_names": detail.uncertain_names,
        "issues": [issue.model_dump() for issue in detail.issues],
        "has_errors": detail.has_errors,
    }


# ---- 统一任务入口（EXT-02 查找/问答/整理分流） ---------------------------------

tasks_router = APIRouter(dependencies=[Depends(require_token)])


@tasks_router.post("/api/v1/tasks")
def create_task(body: TaskRequest, request: Request) -> dict:
    """统一任务入口：auto 分类后走 find/ask/organize，统一 Job 轮询。"""
    if body.task_type not in ("auto", "find", "ask", "organize"):
        raise HTTPException(status_code=422, detail={
            "code": "VALIDATION_ERROR",
            "message": "task_type 必须是 auto/find/ask/organize 之一",
        })
    model, llm = make_llm(request)
    service = request.app.state.service
    jobs = request.app.state.jobs
    privacy = request.app.state.service.get_settings()["privacy"]

    def _run(job_id: str, cancel_event) -> dict:
        del job_id
        return service.run_task(
            body.scan_id,
            body.request,
            task_type=body.task_type,
            template_id=body.template_id,
            privacy=privacy,
            model=model,
            llm=llm,
            cancel_event=cancel_event,
            conversation_id=body.conversation_id,
        )

    job_id = jobs.submit("task", _run)
    return {"job_id": job_id}


@tasks_router.post("/api/v1/tasks/stream")
def stream_task(body: TaskRequest, request: Request) -> Any:
    """流式任务入口（SSE）：ask 逐字推送；organize 返回信号转 job 通道。"""
    import json as _json

    from fastapi.responses import StreamingResponse

    model, llm = make_llm(request)
    service = request.app.state.service
    privacy = service.get_settings()["privacy"]

    def gen():
        for event in service.stream_task(
            body.scan_id,
            body.request,
            task_type=body.task_type,
            template_id=body.template_id,
            privacy=privacy,
            model=model,
            llm=llm,
            conversation_id=body.conversation_id,
        ):
            yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---- 对话会话（多轮持久化） ------------------------------------------------------

conversations_router = APIRouter(dependencies=[Depends(require_token)])


@conversations_router.get("/api/v1/conversations")
def list_conversations(request: Request) -> dict:
    return {"conversations": request.app.state.service.list_conversations()}


@conversations_router.post("/api/v1/conversations")
def create_conversation(body: ConversationCreateRequest, request: Request) -> dict:
    # 校验 scan 会话有效（与会话绑定）
    request.app.state.service._ensure_session(body.scan_id)
    return request.app.state.service.create_conversation(body.scan_id, body.title)


@conversations_router.get("/api/v1/conversations/{conversation_id}/messages")
def get_messages(conversation_id: int, request: Request) -> dict:
    service = request.app.state.service
    messages = service.get_messages(conversation_id)
    conversations = {c["id"]: c for c in service.list_conversations(limit=200)}
    conversation = conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail={
            "code": "CONVERSATION_NOT_FOUND", "message": "会话不存在",
        })
    return {"conversation": conversation, "messages": messages}


@conversations_router.delete("/api/v1/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, request: Request) -> dict:
    request.app.state.service.delete_conversation(conversation_id)
    return {"deleted": True}


# ---- 整理模板（EXT-01） --------------------------------------------------------

templates_router = APIRouter(dependencies=[Depends(require_token)])


@templates_router.get("/api/v1/templates")
def list_templates(request: Request) -> dict:
    return {"templates": request.app.state.service.list_templates()}


@templates_router.post("/api/v1/templates")
def add_template(body: TemplateCreateRequest, request: Request) -> dict:
    return request.app.state.service.add_template(body.name, body.instruction)


@templates_router.put("/api/v1/templates/{template_id}")
def update_template(template_id: int, body: TemplateUpdateRequest, request: Request) -> dict:
    return request.app.state.service.update_template(
        template_id,
        name=body.name,
        instruction=body.instruction,
        enabled=body.enabled,
    )


@templates_router.delete("/api/v1/templates/{template_id}")
def delete_template(template_id: int, request: Request) -> dict:
    request.app.state.service.delete_template(template_id)
    return {"deleted": True}


# ---- 执行与撤销（示意图 ⑦） ----------------------------------------------------

executions_router = APIRouter(dependencies=[Depends(require_token)])


@executions_router.post("/api/v1/executions")
def execute_plan(
    body: ExecutionRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None),
) -> dict:
    del idempotency_key  # 幂等由确定性 execution_id 保证（§9）；头保留为协议占位
    result = request.app.state.service.execute_approved(body.approval_id)
    return result.model_dump(mode="json")


@executions_router.get("/api/v1/executions/{execution_id}")
def get_execution(execution_id: str, request: Request) -> dict:
    detail = request.app.state.service.get_execution(execution_id)
    if detail is None:
        raise HTTPException(
            status_code=404, detail={"code": "EXECUTION_NOT_FOUND", "message": "执行批次不存在"}
        )
    return detail


@executions_router.post("/api/v1/executions/{execution_id}/undo")
def undo_execution(execution_id: str, request: Request) -> dict:
    result = request.app.state.service.undo_execution(execution_id)
    return result.model_dump(mode="json")


@executions_router.get("/api/v1/history")
def history(request: Request) -> dict:
    return {"executions": request.app.state.service.list_executions()}


ALL_ROUTERS = (
    system_router,
    workspaces_router,
    plans_router,
    tasks_router,
    templates_router,
    conversations_router,
    executions_router,
)


def _unused(*_args: Any) -> None:  # pragma: no cover
    """占位避免 F401：Depends/Header 由路由函数签名使用。"""
