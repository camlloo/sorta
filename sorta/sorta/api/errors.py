"""API 错误信封与异常处理（v2 API §4.5）：{code, message, details, request_id}。"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from sorta.errors import SortaError

_STATUS_BY_CODE = {
    "PLAN_NOT_FOUND": 404,
    "FILE_UNKNOWN": 404,
    "SCAN_EXPIRED": 410,
    "WORKSPACE_NOT_AUTHORIZED": 403,
    "APPROVAL_REQUIRED": 403,
    "PLAN_VERSION_CONFLICT": 409,
    "PLAN_CONFLICT": 409,
    "EXECUTION_IN_PROGRESS": 409,
    "PLAN_STALE": 409,
    "PLAN_INVALID_TRANSITION": 409,
    "UNDO_CONFLICT": 409,
    "MODEL_UNAVAILABLE": 503,
    "CONSENT_REQUIRED": 403,
    "OPERATION_CANCELED": 409,
    "UNAUTHORIZED": 401,
}


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {
        "code": code,
        "message": message,
        "details": details or {},
        "request_id": uuid.uuid4().hex[:12],
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(SortaError)
    async def _sorta_error(_: Request, exc: SortaError) -> JSONResponse:
        status = _STATUS_BY_CODE.get(exc.code, 400)
        return JSONResponse(
            status_code=status,
            content=error_body(exc.code, str(exc), getattr(exc, "details", {})),
        )

    @app.exception_handler(HTTPException)
    async def _http_exception(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            return JSONResponse(
                status_code=exc.status_code,
                content=error_body(
                    detail["code"], detail.get("message", ""), detail.get("details")
                ),
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body("HTTP_ERROR", str(detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR", "请求参数不合法", {"errors": exc.errors(include_url=False)}
            ),
        )
