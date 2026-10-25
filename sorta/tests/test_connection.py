"""E3-01 模型连接测试端点测试：成功、鉴权失败、端点不支持工具、未配置。"""

from __future__ import annotations

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from sorta.api import create_app


class FakeUsage:
    prompt_tokens = 1
    completion_tokens = 1


def _ok_client(capture: dict | None = None):
    class OkClient:
        total_usage = FakeUsage

        def chat(self, *, model, messages, tools=None):
            if capture is not None:
                capture["tools"] = tools
            return type("R", (), {"content": "OK"})()

    return OkClient()


def _auth_fail_client():
    class AuthFailClient:
        total_usage = FakeUsage

        def chat(self, *, model, messages, tools=None):
            req = httpx.Request("POST", "https://x/v1/chat/completions")
            raise openai.AuthenticationError(
                "invalid api key", response=httpx.Response(401, request=req), body=None
            )

    return AuthFailClient()


def _tools_reject_client():
    class ToolsRejectClient:
        total_usage = FakeUsage

        def chat(self, *, model, messages, tools=None):
            if tools:
                req = httpx.Request("POST", "https://x/v1/chat/completions")
                raise openai.BadRequestError(
                    "tools not supported", response=httpx.Response(400, request=req), body=None
                )
            return type("R", (), {"content": "OK"})()

    return ToolsRejectClient()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTA_API_KEY", "test-key")
    monkeypatch.setenv("SORTA_MODEL", "mock-model")
    import dataclasses

    from sorta.config import SortaConfig

    config = dataclasses.replace(SortaConfig(), db_url=f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(config=config, enable_auth=True)
    with TestClient(app) as test_client:
        yield test_client


def _token(client) -> str:
    return client.get("/api/v1/session", headers={"sec-fetch-site": "none"}).json()["token"]


def test_connection_ok_and_tools_compatible(client):
    capture: dict = {}
    client.app.state.llm_factory = lambda m, k, b: _ok_client(capture)

    response = client.post(
        "/api/v1/settings/test-connection", headers={"X-SORTA-Token": _token(client)}
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True and body["planning_ready"] is True
    assert body["checks"]["chat"]["ok"] is True
    assert body["checks"]["tools"]["ok"] is True
    assert "latency_ms" in body["checks"]["chat"]
    assert capture["tools"] is not None  # 探测请求确实携带了工具清单


def test_connection_auth_failure_classified(client):
    client.app.state.llm_factory = lambda m, k, b: _auth_fail_client()
    response = client.post(
        "/api/v1/settings/test-connection", headers={"X-SORTA-Token": _token(client)}
    )
    body = response.json()
    assert body["ok"] is False
    assert body["checks"]["chat"]["code"] == "AUTH_FAILED"
    assert "API Key" in body["checks"]["chat"]["message"]


def test_connection_tools_incompatible_flagged(client):
    """工具调用兼容性单独检查：对话通但 tools 被拒时，明确提示规划不可用。"""
    client.app.state.llm_factory = lambda m, k, b: _tools_reject_client()
    response = client.post(
        "/api/v1/settings/test-connection", headers={"X-SORTA-Token": _token(client)}
    )
    body = response.json()
    assert body["checks"]["chat"]["ok"] is True
    assert body["checks"]["tools"]["ok"] is False
    assert body["checks"]["tools"]["code"] == "BAD_REQUEST"
    assert body["planning_ready"] is False


def test_connection_requires_token(client):
    assert client.post("/api/v1/settings/test-connection").status_code == 401
