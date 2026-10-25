"""API 依赖：本机会话鉴权（v2 文档 §2.1）。

- 生产（同源）：token 由 /api/v1/session 发放给同源页面，前端以
  X-SORTA-Token 头携带；自定义头会触发 CORS 预检，恶意网页无法跨源读取。
- 开发模式：SORTA_CORS_ORIGINS 配置精确来源（如 http://localhost:5173）。
- 健康检查与 session 端点豁免。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from sorta.errors import SortaError


class AppState:
    """create_app 写入 app.state 的运行时对象集合。"""

    service: Any
    jobs: Any
    api_token: str
    llm_factory: Any
    model: str


def _unauthorized(message: str = "缺少或无效的会话凭据") -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "UNAUTHORIZED", "message": message},
    )


def require_token(request: Request) -> None:
    if not getattr(request.app.state, "enable_auth", True):
        return  # 自动化测试关闭鉴权
    state: AppState = request.app.state
    provided = request.headers.get("X-SORTA-Token") or ""
    if not provided and request.headers.get("authorization", "").startswith("Bearer "):
        provided = request.headers["authorization"][len("Bearer ") :]
    if not provided or provided != state.api_token:
        raise _unauthorized()


def get_service(request: Request):
    return request.app.state.service


def get_jobs(request: Request):
    return request.app.state.jobs


def current_model(request: Request) -> str:
    """运行时读取模型配置（设置页保存后立即生效，无需重启）。"""
    model = request.app.state.service.get_llm_config()["model"]
    if not model:
        raise SortaError("SORTA_MODEL 未配置", details={"code": "MODEL_UNAVAILABLE"})
    return model


def make_llm(request: Request) -> tuple[str, Any]:
    """按当前设置构造 (model, LLMClient)；未配置时抛 503 MODEL_UNAVAILABLE。"""
    cfg = request.app.state.service.get_llm_config()
    if not cfg["api_key"] or not cfg["model"]:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MODEL_UNAVAILABLE",
                "message": "模型未配置：请在「设置」页填写模型名称与 API Key（兼容 OpenAI 接口）",
            },
        )
    client = request.app.state.llm_factory(
        cfg["model"], cfg["api_key"], cfg["base_url"] or None
    )
    return cfg["model"], client
