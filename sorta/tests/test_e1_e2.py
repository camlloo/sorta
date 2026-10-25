"""E1/E2 改进批次测试：隐私门、重扫、计划修订（UX-01/03/04）。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from conftest import MockLLM, resp, tc, tool_payloads

from sorta.application import SortaService
from sorta.config import SortaConfig
from sorta.errors import ConsentRequiredError, SortaError
from sorta.storage import PlanStore, create_db_engine, init_db


@pytest.fixture
def privacy_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTA_API_KEY", "test-key")
    root = tmp_path / "root"
    root.mkdir()
    (root / "note.txt").write_text("SQL 注入学习笔记：union select", encoding="utf-8", newline="\n")
    (root / "dup1.bin").write_bytes(b"\x01\x02dup")
    (root / "dup2.bin").write_bytes(b"\x01\x02dup")
    config = SortaConfig(db_url=f"sqlite:///{tmp_path / 'test.db'}")
    engine = create_db_engine(config.db_url)
    init_db(engine)
    store = PlanStore(engine)
    service = SortaService(config=config, store=store)
    info = service.create_workspace(str(root))
    return SimpleNamespace(root=root, config=config, store=store, service=service, info=info)


def _fake_summary_llm():
    class _L:
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0}

        def chat(self, *, model, messages, tools=None):
            assert "SQL 注入" in messages[-1]["content"]  # 正文确实进入请求
            return type("R", (), {"content": "这是一份 SQL 注入学习笔记。"})()

    return _L()


# ---- E1-01：总结统一隐私门 -----------------------------------------------------


def test_summary_metadata_only_rejected(privacy_env):
    service_env = privacy_env
    service_env.service.save_settings(privacy="metadata_only")
    file_id = service_env.service.list_files(service_env.info.scan_id, "note")[0]["file_id"]
    with pytest.raises(ConsentRequiredError, match="metadata_only"):
        service_env.service.summarize_file(
            service_env.info.scan_id, file_id, llm=_fake_summary_llm(), model="m"
        )


def test_summary_ask_requires_explicit_confirm(privacy_env):
    service_env = privacy_env
    service_env.service.save_settings(privacy="ask_before_content")
    file_id = service_env.service.list_files(service_env.info.scan_id, "note")[0]["file_id"]
    with pytest.raises(ConsentRequiredError, match="授权"):
        service_env.service.summarize_file(
            service_env.info.scan_id, file_id, llm=_fake_summary_llm(), model="m"
        )
    # 用户在界面确认后（confirm_content_access=True）：允许本次读取
    result = service_env.service.summarize_file(
        service_env.info.scan_id,
        file_id,
        llm=_fake_summary_llm(),
        model="m",
        confirm_content_access=True,
    )
    assert "SQL 注入" in result


def test_summary_authorized_content_direct(privacy_env):
    service_env = privacy_env
    service_env.service.save_settings(privacy="authorized_content")
    file_id = service_env.service.list_files(service_env.info.scan_id, "note")[0]["file_id"]
    result = service_env.service.summarize_file(
        service_env.info.scan_id, file_id, llm=_fake_summary_llm(), model="m"
    )
    assert "SQL 注入" in result


# ---- E1-03/E1-04：重扫与扫描范围 ------------------------------------------------


def test_rescan_reflects_disk_and_keeps_old_scan(privacy_env):
    env = privacy_env
    old_scan_id = env.info.scan_id
    # 磁盘上新增文件 + 删除旧文件
    (env.root / "new-file.txt").write_text("brand new", encoding="utf-8", newline="\n")
    (env.root / "note.txt").unlink()

    info = env.service.rescan(old_scan_id)
    assert info.scan_id != old_scan_id  # 新快照
    assert info.file_count == 3
    new_paths = {r["path"] for r in env.service.list_files(info.scan_id)}
    assert "new-file.txt" in new_paths and "note.txt" not in new_paths

    # 旧扫描会话不受影响（历史依据不变）
    old_paths = {r["path"] for r in env.service.list_files(old_scan_id)}
    assert "note.txt" in old_paths and "new-file.txt" not in old_paths


# ---- E2-03：自然语言修改计划 ----------------------------------------------------


def _generate_plan(service_env, monkeypatch):
    monkeypatch.setenv("SORTA_API_KEY", "test-key")
    monkeypatch.setenv("SORTA_MODEL", "mock-model")

    def submit(request):
        payload = tool_payloads(request)[0]
        file_id = next(f["id"] for f in payload["files"] if f["relative_path"] == "note.txt")
        return resp(
            tc(
                "submit_plan",
                {
                    "scan_id": payload["scan_id"],
                    "summary": "初始计划",
                    "operations": [
                        {
                            "source_id": file_id,
                            "destination": "Notes/a.txt",
                            "action": "move",
                            "reason": "初始依据",
                            "confidence": "high",
                        }
                    ],
                    "uncertain_files": [],
                },
            )
        )

    record = service_env.service.generate_plan(
        service_env.info.scan_id,
        "整理测试",
        privacy="authorized_content",
        model="mock-model",
        llm=MockLLM([resp(tc("list_files")), submit]),
    )
    assert record.plan_json["operations"][0]["destination"] == "Notes/a.txt"
    return record


def test_revise_plan_creates_new_version(privacy_env, monkeypatch):
    env = privacy_env
    record = _generate_plan(env, monkeypatch)
    old_hash = record.plan_hash

    revised_json = json.dumps(
        {
            "scan_id": record.scan_id,
            "summary": "修订：a.txt 改为复制",
            "operations": [
                {
                    "source_id": env.service.list_files(record.scan_id, "note.txt")[0]["file_id"],
                    "destination": "Backup/a-copy.txt",
                    "action": "copy",
                    "reason": "用户要求复制而非移动",
                    "confidence": "high",
                }
            ],
            "uncertain_files": [],
        },
        ensure_ascii=False,
    )

    detail = env.service.revise_plan(
        record.id,
        "a.txt 改成复制，放到 Backup 目录",
        model="mock-model",
        llm=MockLLM([resp(content=revised_json)]),
        expected_version=record.version,
    )
    assert detail.version == record.version + 1
    assert detail.plan_hash != old_hash
    assert detail.operations[0]["action"] == "copy"
    assert detail.operations[0]["source"] == "note.txt"  # 源路径已解析进详情（UX-02）

    # 旧审批不能执行新计划：基于旧 hash 批准被拒绝
    with pytest.raises(SortaError, match="plan_hash"):
        env.service.approve_plan(
            record.id, expected_version=detail.version, expected_plan_hash=old_hash
        )


def test_revise_plan_version_conflict(privacy_env, monkeypatch):
    env = privacy_env
    record = _generate_plan(env, monkeypatch)
    with pytest.raises(SortaError) as exc:
        env.service.revise_plan(
            record.id,
            "改一下",
            model="mock-model",
            llm=MockLLM([resp(content="{}")]),
            expected_version=record.version + 5,
        )
    assert "版本" in str(exc.value)


def test_revise_plan_rejects_malformed_output(privacy_env, monkeypatch):
    env = privacy_env
    record = _generate_plan(env, monkeypatch)
    with pytest.raises(SortaError, match="合法的修订计划"):
        env.service.revise_plan(
            record.id,
            "改一下",
            model="mock-model",
            llm=MockLLM([resp(content="这不是 JSON")]),
            expected_version=record.version,
        )


# ---- 前端契约：源路径进入计划详情 ------------------------------------------------


def test_plan_detail_includes_source_paths(privacy_env, monkeypatch):
    env = privacy_env
    record = _generate_plan(env, monkeypatch)
    detail = env.service.get_plan_detail(record.id)
    assert detail.operations[0]["source"] == "note.txt"
    assert detail.operations[0]["destination"] == "Notes/a.txt"
