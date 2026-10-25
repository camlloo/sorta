"""FastAPI 入口（v2 文档 D2）：业务 API + 前端静态托管，仅绑定 loopback。"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from sorta.api.errors import register_exception_handlers
from sorta.api.routes import ALL_ROUTERS
from sorta.application.jobs import JobManager
from sorta.application.service import SortaService
from sorta.config import SortaConfig
from sorta.storage import PlanStore, create_db_engine, default_db_url, init_db

_REPO_ROOT = Path(__file__).resolve().parents[2]  # sorta 项目根（pyproject 所在）
_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"


def _default_llm_factory(model: str, api_key: str, base_url: str | None) -> Any:
    from sorta.agent.llm import OpenAICompatClient

    return OpenAICompatClient(api_key=api_key, base_url=base_url)


def create_app(
    *,
    config: SortaConfig | None = None,
    llm_factory: Callable[[str, str, str | None], Any] | None = None,
    enable_auth: bool = True,
) -> FastAPI:
    """构建 FastAPI 应用。enable_auth=False 仅用于自动化测试。"""
    config = config or SortaConfig.from_env()
    engine = create_db_engine(config.db_url)
    init_db(engine)
    service = SortaService(config=config, store=PlanStore(engine))
    service.recover_interrupted()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield  # 引擎/线程池随进程结束回收；文件操作安全停点由执行器保证

    app = FastAPI(title="SORTA API", version="0.2.0", lifespan=lifespan)
    register_exception_handlers(app)
    for router in ALL_ROUTERS:
        app.include_router(router)

    cors_origins = [
        origin.strip()
        for origin in os.environ.get("SORTA_CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if cors_origins:
        # 仅开发模式显式配置；生产为同源部署，不需要 CORS
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.state.service = service
    app.state.jobs = JobManager()
    app.state.config = config
    app.state.llm_factory = llm_factory or _default_llm_factory
    app.state.model = os.environ.get("SORTA_MODEL", "")
    app.state.base_url = os.environ.get("SORTA_BASE_URL", "")
    app.state.has_api_key = bool(os.environ.get("SORTA_API_KEY"))
    app.state.api_token = secrets.token_urlsafe(24)
    app.state.enable_auth = enable_auth
    app.state.allow_cross_site_session = bool(cors_origins)  # 开发模式放宽

    dist_env = os.environ.get("SORTA_FRONTEND_DIST", "")
    dist = Path(dist_env) if dist_env else _FRONTEND_DIST
    if dist.exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        from fastapi.responses import FileResponse

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            """SPA 回退：非 API 路径一律返回 index.html（React Router 接管）。"""
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    return app


def default_db_path() -> Path:

    url = default_db_url()
    prefix = "sqlite:///"
    return (
        Path(url[len(prefix) :]) if url.startswith(prefix) else Path.home() / ".sorta" / "sorta.db"
    )


def get_api_token_file() -> Path:
    """token 落盘于数据目录，供 `sorta ui` 打开的同源页面之外的本地工具读取。"""
    return default_db_path().parent / "api_token"
