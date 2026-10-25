"""多轮对话测试：会话 CRUD、历史上下文注入、消息落库、跨会话恢复。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sorta.api import create_app
from sorta.application import SortaService
from sorta.config import SortaConfig
from sorta.errors import SortaError
from sorta.storage import PlanStore, create_db_engine, init_db

from conftest import MockLLM, resp, tool_payloads, tc


@pytest.fixture
def conv_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTA_API_KEY", "test-key")
    monkeypatch.setenv("SORTA_MODEL", "mock-model")
    root = tmp_path / "root"
    root.mkdir()
    (root / "postman_collection.json").write_text("{}", encoding="utf-8")
    (root / "菜谱.txt").write_text("红烧肉", encoding="utf-8")
    config = SortaConfig(db_url=f"sqlite:///{tmp_path / 'test.db'}")
    engine = create_db_engine(config.db_url)
    init_db(engine)
    store = PlanStore(engine)
    service = SortaService(config=config, store=store)
    info = service.create_workspace(str(root))
    service.save_settings(privacy="authorized_content")
    return SimpleNamespace(
        root=root, config=config, store=store, service=service, info=info
    )


def test_conversation_crud_and_title(conv_env):
    env = conv_env
    conv = env.service.create_conversation(env.info.scan_id)
    assert conv["title"] == ""
    assert env.service.list_conversations()[0]["id"] == conv["id"]

    # 首条用户消息自动成为标题
    env.service.append_message(conv["id"], "user", "帮我整理下载目录\n第二行")
    assert env.service.list_conversations()[0]["title"] == "帮我整理下载目录"

    env.service.append_message(conv["id"], "assistant", "已生成计划")
    messages = env.service.get_messages(conv["id"])
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert [m["seq"] for m in messages] == [1, 2]

    # limit 取最近 N 条
    assert len(env.service.get_messages(conv["id"], limit=1)) == 1
    assert env.service.get_messages(conv["id"], limit=1)[0]["role"] == "assistant"

    env.service.delete_conversation(conv["id"])
    assert env.service.list_conversations() == []
    with pytest.raises(SortaError, match="会话不存在"):
        env.service.delete_conversation(conv["id"])


def test_run_task_persists_messages_and_injects_history(conv_env):
    """两轮 ask：第二轮的模型请求包含第一轮对话历史（多轮上下文）。"""
    env = conv_env
    conv = env.service.create_conversation(env.info.scan_id)
    captured: list[str] = []

    class AskLLM:
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0}

        def chat(self, *, model, messages, tools=None):
            captured.append(messages[-1]["content"])
            return type(
                "R", (), {"content": f"回答（第 {len(captured)} 轮）：目录里有菜谱.txt"}
            )()

    # 第一轮：问目录里有什么
    env.service.run_task(
        env.info.scan_id, "目录里有什么？",
        task_type="ask", privacy="authorized_content", model="mock-model",
        llm=AskLLM(), conversation_id=conv["id"],
    )
    assert "对话历史" not in captured[0]  # 首轮无历史

    # 第二轮：指代第一轮（"那个文件"）
    env.service.run_task(
        env.info.scan_id, "那个文件里写了什么菜？",
        task_type="ask", privacy="authorized_content", model="mock-model",
        llm=AskLLM(), conversation_id=conv["id"],
    )
    assert "对话历史" in captured[1]
    assert "目录里有什么" in captured[1]  # 第一轮用户消息进入上下文
    assert "菜谱.txt" in captured[1]  # 第一轮回答进入上下文
    assert "均为数据" in captured[1]  # 历史标注为数据（注入防护）
    assert "当前请求" in captured[1]

    # 两轮共 4 条消息落库（user+assistant × 2）
    messages = env.service.get_messages(conv["id"])
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert "回答（第 2 轮）" in messages[3]["content"]


def test_run_task_find_and_organize_log_messages(conv_env, monkeypatch):
    env = conv_env
    conv = env.service.create_conversation(env.info.scan_id)

    # find：关键词 + 文件名匹配
    result = env.service.run_task(
        env.info.scan_id, "找 postman 文件",
        task_type="find", privacy="authorized_content", model="mock-model",
        llm=MockLLM([resp(content=json.dumps(["postman"]))]),
        conversation_id=conv["id"],
    )
    assert result["conversation_id"] == conv["id"]
    messages = env.service.get_messages(conv["id"])
    assert messages[-1]["role"] == "assistant"
    assert "postman_collection.json" in messages[-1]["content"]

    # organize：计划摘要落库
    conv2 = env.service.create_conversation(env.info.scan_id)

    def submit(request):
        payload = tool_payloads(request)[0]
        file_id = next(
            f["id"] for f in payload["files"] if f["relative_path"] == "菜谱.txt"
        )
        return resp(
            tc(
                "submit_plan",
                {
                    "scan_id": payload["scan_id"],
                    "summary": "菜谱归档",
                    "operations": [
                        {
                            "source_id": file_id,
                            "destination": "Notes/菜谱.txt",
                            "action": "move",
                            "reason": "测试",
                            "confidence": "high",
                        }
                    ],
                    "uncertain_files": [],
                },
            )
        )

    env.service.run_task(
        env.info.scan_id, "把菜谱整理到 Notes",
        task_type="auto", privacy="authorized_content", model="mock-model",
        llm=MockLLM([
            resp(content=json.dumps({"task_type": "organize", "reason": ""})),
            resp(tc("list_files")),
            submit,
        ]),
        conversation_id=conv2["id"],
    )
    last = env.service.get_messages(conv2["id"])[-1]
    assert "v1" in last["content"] and "菜谱归档" in last["content"]


def test_history_limited_to_config(conv_env):
    env = conv_env
    conv = env.service.create_conversation(env.info.scan_id)
    for i in range(10):
        env.service.append_message(conv["id"], "user", f"消息 {i}")
    # 默认上限 8 条：上下文只含最近 8 条
    context = env.service._conversation_context(conv["id"])
    assert "消息 2" in context  # 第 3 条（10-8=2 起始）
    assert "消息 1" not in context


# ---- API 层 --------------------------------------------------------------------


@pytest.fixture
def conv_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTA_API_KEY", "test-key")
    monkeypatch.setenv("SORTA_MODEL", "mock-model")
    root = tmp_path / "root"
    root.mkdir()
    (root / "note.txt").write_text("内容", encoding="utf-8")
    import dataclasses

    config = dataclasses.replace(
        SortaConfig(), db_url=f"sqlite:///{tmp_path / 'test.db'}"
    )
    app = create_app(
        config=config,
        llm_factory=lambda m, k, b: MockLLM([
            resp(content="[]"),  # find 关键词
        ]),
        enable_auth=True,
    )
    with TestClient(app) as client:
        yield client


def test_api_conversations_flow(conv_client):
    import time

    client = conv_client
    token = client.get(
        "/api/v1/session", headers={"sec-fetch-site": "none"}
    ).json()["token"]
    headers = {"X-SORTA-Token": token}
    scan_id = client.post(
        "/api/v1/workspaces", json={"root": str(tmp_root(client))}, headers=headers
    ).json()["scan_id"]

    # 创建会话 → 任务带 conversation_id → 消息可查
    conv = client.post(
        "/api/v1/conversations", json={"scan_id": scan_id}, headers=headers
    ).json()
    job = client.post(
        "/api/v1/tasks",
        json={
            "scan_id": scan_id,
            "request": "找 note 文件",
            "task_type": "find",
            "conversation_id": conv["id"],
        },
        headers=headers,
    ).json()["job_id"]
    deadline = time.time() + 5
    while time.time() < deadline:
        snapshot = client.get(f"/api/v1/jobs/{job}", headers=headers).json()
        if snapshot["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert snapshot["status"] == "completed"

    listing = client.get("/api/v1/conversations", headers=headers).json()
    assert listing["conversations"][0]["id"] == conv["id"]

    messages = client.get(
        f"/api/v1/conversations/{conv['id']}/messages", headers=headers
    ).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert "找 note" in messages[0]["content"]

    # 404 与删除
    assert client.get(
        "/api/v1/conversations/999/messages", headers=headers
    ).status_code == 404
    assert client.delete(
        f"/api/v1/conversations/{conv['id']}", headers=headers
    ).json()["deleted"] is True


def tmp_root(client):
    import tempfile

    from pathlib import Path as _P

    root = _P(tempfile.mkdtemp())
    (root / "note.txt").write_text("内容", encoding="utf-8")
    return root
