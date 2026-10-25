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
    }


@workspaces_router.get("/api/v1/scans/{scan_id}/files")
def list_files(scan_id: str, request: Request, query: str = "") -> dict:
    rows = request.app.state.service.list_files(scan_id, query)
    return {"scan_id": scan_id, "count": len(rows), "files": rows}


@workspaces_router.post("/api/v1/scans/{scan_id}/files/{file_id}/preview")
def file_preview(scan_id: str, file_id: str, request: Request) -> dict:
    outcome = request.app.state.service.file_preview(scan_id, file_id)
    return outcome.model_dump()


@workspaces_router.post("/api/v1/scans/{scan_id}/files/{file_id}/summary")
def file_summary(scan_id: str, file_id: str, request: Request) -> dict:
    model, llm = make_llm(request)
    summary = request.app.state.service.summarize_file(
        scan_id, file_id, llm=llm, model=model
    )
    return {"summary": summary}


@workspaces_router.post("/api/v1/scans/{scan_id}/duplicates")
def find_duplicates(scan_id: str, request: Request) -> dict:
    groups = request.app.state.service.find_duplicates(scan_id)
    return {"groups": groups}


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


ALL_ROUTERS = (system_router, workspaces_router, plans_router, executions_router)


def _unused(*_args: Any) -> None:  # pragma: no cover
    """占位避免 F401：Depends/Header 由路由函数签名使用。"""
