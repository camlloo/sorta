"""E3-02 测试：任务分流（查找/问答/整理/澄清）与整理模板 CRUD。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from conftest import MockLLM, resp, tc, tool_payloads
from fastapi.testclient import TestClient

from sorta.api import create_app
from sorta.application import SortaService
from sorta.config import SortaConfig
from sorta.errors import SortaError
from sorta.storage import PlanStore, create_db_engine, init_db


@pytest.fixture
def task_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTA_API_KEY", "test-key")
    monkeypatch.setenv("SORTA_MODEL", "mock-model")
    root = tmp_path / "root"
    root.mkdir()
    (root / "postman_collection.json").write_text("{}", encoding="utf-8")
    (root / "postman_任务书.md").write_text("# 任务书", encoding="utf-8")
    (root / "菜谱.txt").write_text("红烧肉", encoding="utf-8")
    config = SortaConfig(db_url=f"sqlite:///{tmp_path / 'test.db'}")
    engine = create_db_engine(config.db_url)
    init_db(engine)
    store = PlanStore(engine)
    service = SortaService(config=config, store=store)
    info = service.create_workspace(str(root))
    return SimpleNamespace(
        root=root, config=config, store=store, service=service, info=info
    )


def _classify_llm(task_type: str, reason: str = "分类理由"):
    return MockLLM([
        resp(content=json.dumps({"task_type": task_type, "reason": reason}, ensure_ascii=False))
    ])


def _keywords_llm(keywords: list[str]):
    return MockLLM([resp(content=json.dumps(keywords, ensure_ascii=False))])


# ---- 查找任务 ---------------------------------------------------------------


def test_find_task_keyword_match(task_env):
    env = task_env
    result = env.service.run_task(
        env.info.scan_id, "帮我找一下 postman 相关的文件",
        task_type="find", privacy="authorized_content",
        model="mock-model", llm=_keywords_llm(["postman"]),
    )
    assert result["task_type"] == "find"
    paths = [m["path"] for m in result["matches"]]
    assert paths == ["postman_collection.json", "postman_任务书.md"]
    assert "postman" in result["matches"][0]["reason"]
    assert result["scanned"] == 3
    assert "未检索文件内容" in result["note"]


def test_find_task_no_match_reports_scope(task_env):
    env = task_env
    result = env.service.run_task(
        env.info.scan_id, "找一下发票",
        task_type="find", privacy="authorized_content",
        model="mock-model", llm=_keywords_llm(["发票"]),
    )
    assert result["matches"] == []
    assert result["scanned"] == 3


# ---- 问答任务 ---------------------------------------------------------------


def test_ask_task_answers_from_listing_only(task_env):
    env = task_env
    captured: dict = {}

    class CapturingLLM:
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0}

        def chat(self, *, model, messages, tools=None):
            captured["system"] = messages[0]["content"]
            captured["user"] = messages[-1]["content"]
            answer = "目录里有 3 个文件，其中 postman_collection.json 是 Collection。"
            return type("R", (), {"content": answer})()

    result = env.service.run_task(
        env.info.scan_id, "这个目录里都有什么？",
        task_type="ask", privacy="authorized_content",
        model="mock-model", llm=CapturingLLM(),
    )
    assert result["task_type"] == "ask"
    assert "postman_collection.json" in result["answer"]
    assert "postman_collection.json" in result["references"]
    # 隐私边界：问答只基于文件清单，请求中不含文件正文
    assert "红烧肉" not in captured["user"]  # 菜谱.txt 的正文未进入请求
    assert "清单" in captured["user"]


# ---- 澄清 --------------------------------------------------------------------


def test_auto_classify_clarifies_when_unclear(task_env):
    env = task_env
    result = env.service.run_task(
        env.info.scan_id, "帮我处理一下",
        task_type="auto", privacy="authorized_content",
        model="mock-model", llm=_classify_llm("clarify", "你想查找还是整理文件？"),
    )
    assert result["task_type"] == "clarify"
    assert "查找还是整理" in result["question"]


def test_auto_classify_routes_to_organize(task_env, monkeypatch):
    env = task_env
    monkeypatch.setenv("SORTA_API_KEY", "test-key")
    monkeypatch.setenv("SORTA_MODEL", "mock-model")

    def classify(request):
        return resp(content=json.dumps({"task_type": "organize", "reason": "要求整理"}))

    def submit(request):
        payload = tool_payloads(request)[0]
        file_id = next(f["id"] for f in payload["files"] if f["relative_path"] == "菜谱.txt")
        return resp(
            tc(
                "submit_plan",
                {
                    "scan_id": payload["scan_id"],
                    "summary": "整理菜谱",
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

    # 分类与计划生成共用一个 llm 实例：脚本顺序 = 分类 → list_files → submit_plan
    result = env.service.run_task(
        env.info.scan_id, "把菜谱整理到 Notes",
        task_type="auto", privacy="authorized_content",
        model="mock-model",
        llm=MockLLM([classify(None), resp(tc("list_files")), submit]),
    )
    assert result["task_type"] == "organize"
    plan = env.store.get_plan(result["plan_id"])
    assert plan is not None and plan.status == "draft"


# ---- 整理模板 ----------------------------------------------------------------


def test_template_crud_and_builtin_seed(task_env):
    env = task_env
    templates = env.service.list_templates()
    names = [t["name"] for t in templates]
    assert "课程资料整理" in names  # 内置模板已种子化
    assert all(t["enabled"] for t in templates if t["name"] == "课程资料整理")

    added = env.service.add_template("测试模板", "所有 txt 移到 Text 目录")
    with pytest.raises(SortaError, match="同名"):
        env.service.add_template("测试模板", "重复")

    updated = env.service.update_template(added["id"], instruction="改后的规则")
    assert updated["name"] == "测试模板"

    disabled = env.service.update_template(added["id"], enabled=False)
    assert disabled["enabled"] is False

    env.service.delete_template(added["id"])
    assert all(t["id"] != added["id"] for t in env.service.list_templates())


class TaskRoutingLLM:
    """同一实例处理分类（无工具）与计划生成（带工具），并捕获模板注入。"""

    def __init__(self, scan_id: str, files: list[dict]):
        self.scan_id = scan_id
        self.files = files
        self.captured: str | None = None
        self.tools_calls = 0
        self.total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0}

    def chat(self, *, model, messages, tools=None):
        last = messages[-1]["content"]
        if "整理模板" in last:
            self.captured = last
        if tools is None:  # 分类调用
            payload = json.dumps({"task_type": "organize", "reason": ""}, ensure_ascii=False)
            return resp(content=payload)
        self.tools_calls += 1
        if self.tools_calls == 1:
            return resp(tc("list_files"))
        # generate_plan 会创建新扫描会话：从 list_files 工具结果中取新 scan_id
        scan_id = self.scan_id
        file_id = self.files[0]["file_id"]
        for message in reversed(messages):
            if message.get("role") == "tool":
                try:
                    payload = json.loads(message["content"])
                except (json.JSONDecodeError, AttributeError):
                    continue
                if "scan_id" in payload:
                    scan_id = payload["scan_id"]
                    if payload.get("files"):
                        file_id = payload["files"][0]["id"]
                    break
        return resp(
            tc(
                "submit_plan",
                {
                    "scan_id": scan_id,
                    "summary": "模板已应用",
                    "operations": [
                        {
                            "source_id": file_id,
                            "destination": "Sorted/first.txt",
                            "action": "move",
                            "reason": "模板偏好：按类型分目录",
                            "confidence": "high",
                        }
                    ],
                    "uncertain_files": [],
                },
            )
        )


def test_organize_with_template_injects_preference(task_env, monkeypatch):
    """模板偏好注入计划生成的用户消息；冲突取舍规则随模板注入。"""
    env = task_env
    monkeypatch.setenv("SORTA_API_KEY", "test-key")
    monkeypatch.setenv("SORTA_MODEL", "mock-model")
    added = env.service.add_template("注入测试模板", "所有文件按类型分目录存放。")
    files = env.service.list_files(env.info.scan_id)
    llm = TaskRoutingLLM(env.info.scan_id, files)

    result = env.service.run_task(
        env.info.scan_id, "整理这个目录",
        task_type="auto", template_id=added["id"],
        privacy="authorized_content", model="mock-model", llm=llm,
    )
    assert result["task_type"] == "organize"
    assert llm.captured is not None
    assert "注入测试模板" in llm.captured
    assert "所有文件按类型分目录存放" in llm.captured
    assert "以当前指令为准" in llm.captured  # 冲突取舍规则随模板注入
    plan = env.store.get_plan(result["plan_id"])
    assert plan is not None and plan.status == "draft"


# ---- API 层：统一任务入口与模板路由 -------------------------------------------


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTA_API_KEY", "test-key")
    monkeypatch.setenv("SORTA_MODEL", "mock-model")
    root = tmp_path / "root"
    root.mkdir()
    (root / "postman_collection.json").write_text("{}", encoding="utf-8")
    import dataclasses

    from sorta.config import SortaConfig

    config = dataclasses.replace(SortaConfig(), db_url=f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(
        config=config,
        llm_factory=lambda m, k, b: MockLLM([
            resp(content=json.dumps({"task_type": "find", "reason": ""}, ensure_ascii=False)),
            _keywords_resp(["postman"]),
        ]),
        enable_auth=True,
    )
    with TestClient(app) as test_client:
        yield test_client


def _keywords_resp(keywords):
    return resp(content=json.dumps(keywords, ensure_ascii=False))


def _wait_job(client, job_id, headers, timeout=5.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()
        if snapshot["status"] in ("completed", "failed", "canceled"):
            return snapshot
        time.sleep(0.05)
    raise AssertionError("job 超时")


def test_api_find_task_and_templates(api_client):
    client = api_client
    token = client.get("/api/v1/session", headers={"sec-fetch-site": "none"}).json()["token"]
    headers = {"X-SORTA-Token": token}

    import tempfile
    from pathlib import Path as _Path

    tmp_root = _Path(tempfile.mkdtemp())
    (tmp_root / "postman_collection.json").write_text("{}", encoding="utf-8")
    workspace = client.post(
        "/api/v1/workspaces", json={"root": str(tmp_root)}, headers=headers
    ).json()
    scan_id = workspace["scan_id"]

    # 模板 CRUD
    templates = client.get("/api/v1/templates", headers=headers).json()["templates"]
    assert any(t["name"] == "课程资料整理" for t in templates)
    created = client.post(
        "/api/v1/templates",
        json={"name": "API 模板", "instruction": "测试规则"},
        headers=headers,
    ).json()
    assert created["enabled"] is True
    updated = client.put(
        f"/api/v1/templates/{created['id']}", json={"enabled": False}, headers=headers
    ).json()
    assert updated["enabled"] is False
    deleted = client.delete(f"/api/v1/templates/{created['id']}", headers=headers).json()
    assert deleted["deleted"] is True

    # 查找任务（mock：分类为 find → 关键词 postman）
    job_id = client.post(
        "/api/v1/tasks",
        json={"scan_id": scan_id, "request": "找 postman 相关文件", "task_type": "auto"},
        headers=headers,
    ).json()["job_id"]
    job = _wait_job(client, job_id, headers)
    assert job["status"] == "completed"
    assert job["result"]["task_type"] == "find"
    assert any("postman" in m["path"] for m in job["result"]["matches"])
