"""FastAPI 集成测试（v2 D2）：完整闭环 + 错误码 + 鉴权。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from conftest import MockLLM, resp, tc, tool_payloads
from fastapi.testclient import TestClient

from sorta.api import create_app
from sorta.config import SortaConfig


def _make_mock_llm():
    def submit(request):
        payload = tool_payloads(request)[0]
        file_id = payload["files"][0]["id"]
        return resp(
            tc(
                "submit_plan",
                {
                    "scan_id": payload["scan_id"],
                    "summary": "API 集成计划",
                    "operations": [
                        {
                            "source_id": file_id,
                            "destination": "Notes/a.txt",
                            "action": "move",
                            "reason": "测试依据",
                            "confidence": "high",
                        }
                    ],
                    "uncertain_files": [],
                },
            )
        )

    return MockLLM([resp(tc("list_files")), submit])


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SORTA_API_KEY", raising=False)
    monkeypatch.setenv("SORTA_MODEL", "mock-model")
    monkeypatch.delenv("SORTA_CORS_ORIGINS", raising=False)

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8", newline="\n")
    (root / "b.txt").write_text("world", encoding="utf-8", newline="\n")

    import dataclasses

    config = dataclasses.replace(SortaConfig(), db_url="sqlite://")
    app = create_app(config=config, llm_factory=_make_mock_llm, enable_auth=True)
    client = TestClient(app)
    return SimpleNamespace(root=root, config=config, client=client, app=app)


def _wait_job(client, job_id: str, timeout: float = 5.0, headers: dict | None = None) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = client.get(f"/api/v1/jobs/{job_id}", headers=headers or {}).json()
        if snapshot["status"] in ("completed", "failed", "canceled"):
            return snapshot
        time.sleep(0.05)
    raise AssertionError("job 超时未完成")


def test_health_and_auth(api_env):
    client = api_env.client
    assert client.get("/api/v1/health").json()["status"] == "ok"

    # 未带 token：401（错误信封）
    response = client.get("/api/v1/settings")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"

    # session 端点发放 token（同源导航 Sec-Fetch-Site: none / same-origin）
    token = client.get("/api/v1/session", headers={"sec-fetch-site": "same-origin"}).json()["token"]
    response = client.get("/api/v1/settings", headers={"X-SORTA-Token": token})
    assert response.status_code == 200

    # 跨站会话请求被拒绝
    cross = client.get("/api/v1/session", headers={"sec-fetch-site": "cross-site"})
    assert cross.status_code == 403


def test_full_closure_over_api(api_env):
    client = api_env.client
    token = client.get("/api/v1/session", headers={"sec-fetch-site": "none"}).json()["token"]
    headers = {"X-SORTA-Token": token}

    # 工作区
    workspace = client.post(
        "/api/v1/workspaces", json={"root": str(api_env.root)}, headers=headers
    ).json()
    assert workspace["file_count"] == 2
    scan_id = workspace["scan_id"]

    # 文件列表
    files = client.get(
        f"/api/v1/scans/{scan_id}/files", params={"query": "a"}, headers=headers
    ).json()
    assert files["count"] == 1 and files["files"][0]["path"] == "a.txt"

    # 计划生成（Job 轮询）
    job_id = client.post(
        "/api/v1/plans",
        json={"scan_id": scan_id, "request": "整理测试", "privacy": "authorized_content"},
        headers=headers,
    ).json()["job_id"]
    job = _wait_job(client, job_id, headers=headers)
    assert job["status"] == "completed"
    assert len(job["tool_events"]) == 2  # list_files + submit_plan 事件被记录
    plan_id = job["result"]["plan_id"]
    version = job["result"]["version"]

    # 计划详情（含校验结果）
    detail = client.get(f"/api/v1/plans/{plan_id}", headers=headers).json()
    assert detail["version"] == version and detail["has_errors"] is False

    # 编辑（版本乐观锁）
    edited = client.patch(
        f"/api/v1/plans/{plan_id}",
        json={"keep": [True], "destinations": ["Renamed/a.txt"], "expected_version": version},
        headers=headers,
    ).json()
    assert edited["version"] == version + 1
    stale = client.patch(
        f"/api/v1/plans/{plan_id}",
        json={"keep": [True], "destinations": ["X/a.txt"], "expected_version": version},
        headers=headers,
    )
    assert stale.status_code == 409 and stale.json()["code"] == "PLAN_VERSION_CONFLICT"

    # 批准（hash 绑定）
    approval = client.post(
        f"/api/v1/plans/{plan_id}/approve",
        json={"expected_version": edited["version"], "expected_plan_hash": edited["plan_hash"]},
        headers=headers,
    ).json()
    assert approval["approval_id"] > 0

    # 执行
    execution = client.post(
        "/api/v1/executions",
        json={"approval_id": approval["approval_id"]},
        headers=headers,
    ).json()
    assert execution["status"] == "completed"
    execution_id = execution["execution_id"]

    # 执行详情 + 历史
    detail = client.get(f"/api/v1/executions/{execution_id}", headers=headers).json()
    assert detail["operations"][0]["status"] == "succeeded"
    history = client.get("/api/v1/history", headers=headers).json()
    assert any(e["execution_id"] == execution_id for e in history["executions"])

    # 撤销
    undo = client.post(f"/api/v1/executions/{execution_id}/undo", headers=headers).json()
    assert undo["status"] == "undone"


def test_plan_job_failure_maps_to_error_code(api_env, monkeypatch):
    monkeypatch.setenv("SORTA_MODEL", "")  # 触发 MODEL_UNAVAILABLE
    client = api_env.client
    client.app.state.model = ""
    workspace = client.post(
        "/api/v1/workspaces", json={"root": str(api_env.root)}
    ).json()  # enable_auth=True → 需要 token
    # 上面未带 token 会 401：改用带 token 的请求
    if "scan_id" not in workspace:
        token = client.get("/api/v1/session", headers={"sec-fetch-site": "none"}).json()["token"]
        workspace = client.post(
            "/api/v1/workspaces",
            json={"root": str(api_env.root)},
            headers={"X-SORTA-Token": token},
        ).json()
        headers = {"X-SORTA-Token": token}
    else:
        headers = {}
    response = client.post(
        "/api/v1/plans",
        json={"scan_id": workspace["scan_id"], "request": "x"},
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["code"] == "MODEL_UNAVAILABLE"


def test_workspace_invalid_root(api_env):
    client = api_env.client
    token = client.get("/api/v1/session", headers={"sec-fetch-site": "none"}).json()["token"]
    response = client.post(
        "/api/v1/workspaces",
        json={"root": "Z:/不存在的目录"},
        headers={"X-SORTA-Token": token},
    )
    assert response.status_code == 400
    assert response.json()["code"] in ("SCAN_INVALID", "SORTA_ERROR")
