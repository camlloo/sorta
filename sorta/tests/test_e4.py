"""E4 测试：重复治理（EXT-05）、Project Pack（EXT-03）、OCR（EXT-06）。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from conftest import MockLLM, resp
from fastapi.testclient import TestClient

from sorta.api import create_app
from sorta.application import SortaService
from sorta.config import SortaConfig
from sorta.errors import ConsentRequiredError, SortaError
from sorta.storage import PlanStore, create_db_engine, init_db

DUP_CONTENT = b"\x00\x01duplicate-content"
OTHER_DUP = b"\x00\x01duplicate-content"


@pytest.fixture
def e4_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTA_API_KEY", "test-key")
    monkeypatch.setenv("SORTA_MODEL", "mock-model")
    root = tmp_path / "root"
    root.mkdir()
    (root / "任务书-接口测试.docx").touch()
    from docx import Document

    doc = Document()
    doc.add_paragraph(
        "任务书：使用 Postman 完成接口测试并导出 Collection，交付 collection.json 与运行截图。"
    )
    doc.save(root / "任务书-接口测试.docx")
    (root / "postman_collection.json").write_text('{"info": {"name": "hw"}}', encoding="utf-8")
    (root / "screenshot_run.png").write_bytes(b"\x89PNG fake screenshot")
    (root / "dup_a.txt").write_bytes(DUP_CONTENT)
    (root / "dup_b.txt").write_bytes(OTHER_DUP)
    (root / "unique.txt").write_text("唯一文件", encoding="utf-8")

    config = SortaConfig(db_url=f"sqlite:///{tmp_path / 'test.db'}")
    engine = create_db_engine(config.db_url)
    init_db(engine)
    store = PlanStore(engine)
    service = SortaService(config=config, store=store)
    service.save_settings(privacy="authorized_content")
    info = service.create_workspace(str(root))
    return SimpleNamespace(
        root=root, config=config, store=store, service=service, info=info, monkeypatch=monkeypatch
    )


# ---- EXT-05：重复治理 ----------------------------------------------------------


def test_duplicates_detail_format_and_size_first(e4_env):
    env = e4_env
    result = env.service.find_duplicates(env.info.scan_id)
    groups = result["groups"]
    assert len(groups) == 1
    group = groups[0]
    assert group["files"] == ["dup_a.txt", "dup_b.txt"]
    assert group["size"] == len(DUP_CONTENT)
    assert group["wasted_bytes"] == len(DUP_CONTENT)
    assert group["keep"] == "dup_a.txt"  # 路径最短者
    assert set(group["file_ids"]) == {"dup_a.txt", "dup_b.txt"}
    # 先比大小：只有 dup_a/dup_b 同大小 → 仅这两个文件计算哈希
    assert result["hashed"] == 2
    assert result["skipped"] == 0
    assert result["total_wasted_bytes"] == len(DUP_CONTENT)
    assert "建议" in result["note"]


def test_archive_plan_moves_and_undo(e4_env):
    env = e4_env
    dup_result = env.service.find_duplicates(env.info.scan_id)
    group = dup_result["groups"][0]
    to_archive = [group["file_ids"][f] for f in group["files"] if f != group["keep"]]

    record = env.service.build_archive_plan(env.info.scan_id, to_archive)
    assert record.version == 1
    plan_json = record.plan_json
    assert plan_json["operations"][0]["action"] == "move"
    assert plan_json["operations"][0]["destination"] == "重复文件归档/dup_b.txt"

    approval = env.service.approve_plan(
        record.id, expected_version=record.version, expected_plan_hash=record.plan_hash
    )
    result = env.service.execute_approved(approval["approval_id"])
    assert result.status == "completed"
    assert (env.root / "重复文件归档" / "dup_b.txt").exists()
    assert (env.root / "dup_a.txt").exists()  # 保留建议不动
    assert not (env.root / "dup_b.txt").exists()

    undo = env.service.undo_execution(result.execution_id)
    assert undo.status == "undone"
    assert (env.root / "dup_b.txt").exists()


def test_archive_plan_rejects_bad_dir(e4_env):
    env = e4_env
    with pytest.raises(SortaError, match="非法路径段"):
        env.service.build_archive_plan(env.info.scan_id, ["file_x"], archive_dir="../escape")
    with pytest.raises(SortaError, match="未选择"):
        env.service.build_archive_plan(env.info.scan_id, [])


# ---- EXT-03：Project Pack ------------------------------------------------------


def _taskbook_llm():
    items = json.dumps(
        [
            {
                "item": "Postman Collection 导出文件",
                "keywords": ["collection", "postman"],
                "verification": "文件为合法 Collection JSON",
            },
            {
                "item": "运行截图",
                "keywords": ["截图", "screenshot"],
                "verification": "截图内容正确",
            },
            {"item": "不存在的交付物", "keywords": ["不存在 xyz"], "verification": ""},
        ],
        ensure_ascii=False,
    )
    return MockLLM([resp(content=items)])


def _file_id(env, path: str) -> str:
    return env.service.list_files(env.info.scan_id, path)[0]["file_id"]


def test_analyze_taskbook_found_missing_needs_verification(e4_env):
    env = e4_env
    result = env.service.analyze_taskbook(
        env.info.scan_id,
        _file_id(env, "任务书-接口测试.docx"),
        llm=_taskbook_llm(),
        model="mock-model",
    )
    assert result["taskbook"] == "任务书-接口测试.docx"
    by_item = {i["item"]: i for i in result["items"]}

    found = by_item["Postman Collection 导出文件"]
    assert found["status"] == "NEEDS_VERIFICATION"  # 有核验要求 → 人工确认
    cand_paths = [c["path"] for c in found["candidates"]]
    assert "postman_collection.json" in cand_paths
    assert "任务书-接口测试.docx" not in cand_paths  # 任务书本身排除

    missing = by_item["不存在的交付物"]
    assert missing["status"] == "MISSING" and missing["candidates"] == []

    shot = by_item["运行截图"]
    assert shot["status"] == "NEEDS_VERIFICATION"
    assert any(c["path"] == "screenshot_run.png" for c in shot["candidates"])

    assert "复制" in result["note"]


def test_analyze_taskbook_privacy_gates(e4_env):
    env = e4_env
    taskbook_id = _file_id(env, "任务书-接口测试.docx")
    env.service.save_settings(privacy="metadata_only")
    with pytest.raises(ConsentRequiredError):
        env.service.analyze_taskbook(
            env.info.scan_id, taskbook_id, llm=_taskbook_llm(), model="mock-model"
        )
    env.service.save_settings(privacy="ask_before_content")
    with pytest.raises(ConsentRequiredError, match="授权"):
        env.service.analyze_taskbook(
            env.info.scan_id, taskbook_id, llm=_taskbook_llm(), model="mock-model"
        )
    result = env.service.analyze_taskbook(
        env.info.scan_id, taskbook_id, llm=_taskbook_llm(), model="mock-model",
        confirm_content_access=True,
    )
    assert len(result["items"]) == 3


def test_analyze_taskbook_unreadable(e4_env):
    env = e4_env
    png_id = _file_id(env, "screenshot_run.png")
    with pytest.raises(SortaError, match="无法提取任务书文本"):
        env.service.analyze_taskbook(
            env.info.scan_id, png_id, llm=_taskbook_llm(), model="mock-model"
        )


def test_analyze_taskbook_malformed_llm_output(e4_env):
    env = e4_env
    with pytest.raises(SortaError, match="合法的交付清单"):
        env.service.analyze_taskbook(
            env.info.scan_id, _file_id(env, "任务书-接口测试.docx"),
            llm=MockLLM([resp(content="不是 JSON")]), model="mock-model",
        )


def test_delivery_plan_copies_and_keeps_originals(e4_env):
    env = e4_env
    record = env.service.build_delivery_plan(
        env.info.scan_id,
        [
            {"item": "Collection", "file_id": _file_id(env, "postman_collection.json")},
            {"item": "运行截图", "file_id": _file_id(env, "screenshot_run.png")},
        ],
    )
    plan_json = record.plan_json
    assert all(op["action"] == "copy" for op in plan_json["operations"])
    dests = [op["destination"] for op in plan_json["operations"]]
    assert dests == ["交付材料/postman_collection.json", "交付材料/screenshot_run.png"]

    approval = env.service.approve_plan(
        record.id, expected_version=record.version, expected_plan_hash=record.plan_hash
    )
    result = env.service.execute_approved(approval["approval_id"])
    assert result.status == "completed"
    # 复制：原件保持不动
    assert (env.root / "postman_collection.json").exists()
    assert (env.root / "交付材料" / "postman_collection.json").exists()


def test_delivery_plan_dedupes_names(e4_env):
    env = e4_env
    (env.root / "sub").mkdir()
    (env.root / "sub" / "postman_collection.json").write_text("{}", encoding="utf-8")
    new_scan = env.service.rescan(env.info.scan_id).scan_id  # 重扫后用新会话
    ids = [r["file_id"] for r in env.service.list_files(new_scan, "postman_collection.json")]
    record = env.service.build_delivery_plan(
        new_scan,
        [
            {"item": "A", "file_id": ids[0]},
            {"item": "B", "file_id": ids[1]},
        ],
    )
    dests = [op["destination"] for op in record.plan_json["operations"]]
    assert len(set(dests)) == 2  # 同名自动编号，不互相覆盖


# ---- EXT-06：OCR --------------------------------------------------------------


def _vision_llm(captured: dict):
    class VisionLLM:
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0}

        def chat(self, *, model, messages, tools=None):
            captured["model"] = model
            captured["content"] = messages[-1]["content"]
            return type("R", (), {"content": "识别出的文字内容"})()

    return VisionLLM()


def test_ocr_success_and_image_sent(e4_env):
    env = e4_env
    captured: dict = {}
    result = env.service.ocr_file(
        env.info.scan_id, _file_id(env, "screenshot_run.png"),
        llm=_vision_llm(captured), vision_model="qwen-vl-plus",
    )
    assert "识别出的文字" in result["text"]
    assert result["model"] == "qwen-vl-plus"
    assert "不确定" in result["source"]
    # 图片以 data URL 进入多模态消息
    parts = captured["content"]
    assert isinstance(parts, list)
    image_part = next(p for p in parts if p["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_ocr_rejects_non_image(e4_env):
    env = e4_env
    with pytest.raises(SortaError, match="仅支持图片"):
        env.service.ocr_file(
            env.info.scan_id, _file_id(env, "postman_collection.json"),
            llm=_vision_llm({}), vision_model="qwen-vl-plus",
        )


def test_ocr_privacy_gate(e4_env):
    env = e4_env
    png_id = _file_id(env, "screenshot_run.png")
    env.service.save_settings(privacy="metadata_only")
    with pytest.raises(ConsentRequiredError):
        env.service.ocr_file(
            env.info.scan_id, png_id, llm=_vision_llm({}), vision_model="qwen-vl-plus"
        )
    env.service.save_settings(privacy="ask_before_content")
    with pytest.raises(ConsentRequiredError, match="授权"):
        env.service.ocr_file(
            env.info.scan_id, png_id, llm=_vision_llm({}), vision_model="qwen-vl-plus"
        )
    ok = env.service.ocr_file(
        env.info.scan_id, png_id, llm=_vision_llm({}), vision_model="qwen-vl-plus",
        confirm_content_access=True,
    )
    assert ok["text"]


def test_vision_model_settings_roundtrip(e4_env):
    env = e4_env
    assert env.service.get_llm_config()["vision_model"] == ""
    env.service.save_settings(vision_model="qwen-vl-plus")
    assert env.service.get_llm_config()["vision_model"] == "qwen-vl-plus"
    assert env.service.get_settings()["vision_model"] == "qwen-vl-plus"
    env.service.save_settings(vision_model="")  # 可清除
    assert env.service.get_llm_config()["vision_model"] == ""


# ---- API 层 --------------------------------------------------------------------


@pytest.fixture
def e4_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTA_API_KEY", "test-key")
    monkeypatch.setenv("SORTA_MODEL", "mock-model")
    root = tmp_path / "root"
    root.mkdir()
    (root / "dup_a.txt").write_bytes(DUP_CONTENT)
    (root / "dup_b.txt").write_bytes(OTHER_DUP)
    import dataclasses

    config = dataclasses.replace(SortaConfig(), db_url=f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(config=config, llm_factory=lambda m, k, b: MockLLM([]), enable_auth=True)
    with TestClient(app) as client:
        client.app.state.service.save_settings(privacy="authorized_content")
        yield SimpleNamespace(client=client, root=root)


def test_api_archive_and_ocr_endpoints(e4_client):
    ns = e4_client
    client = ns.client
    token = client.get("/api/v1/session", headers={"sec-fetch-site": "none"}).json()["token"]
    headers = {"X-SORTA-Token": token}
    scan = client.post(
        "/api/v1/workspaces", json={"root": str(ns.root)}, headers=headers
    ).json()["scan_id"]

    dup = client.post(f"/api/v1/scans/{scan}/duplicates", headers=headers).json()
    assert len(dup["groups"]) == 1
    group = dup["groups"][0]
    to_archive = [group["file_ids"][f] for f in group["files"] if f != group["keep"]]

    plan_resp = client.post(
        f"/api/v1/scans/{scan}/duplicates/archive",
        json={"file_ids": to_archive},
        headers=headers,
    ).json()
    assert "plan_id" in plan_resp

    # OCR：未配置视觉模型 → 503
    files = client.get(f"/api/v1/scans/{scan}/files", headers=headers).json()["files"]
    ocr_resp = client.post(
        f"/api/v1/scans/{scan}/files/{files[0]['file_id']}/ocr", json={}, headers=headers
    )
    assert ocr_resp.status_code == 503
    assert ocr_resp.json()["code"] == "MODEL_UNAVAILABLE"
