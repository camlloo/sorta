"""SortaService 层测试（v2 D1）：编排不破坏领域规则。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import MockLLM, resp, tc, tool_payloads

from sorta.application import SortaService
from sorta.config import SortaConfig
from sorta.errors import PlanNotFoundError, PlanVersionConflictError, SortaError
from sorta.storage import PlanStore, create_db_engine, init_db


@pytest.fixture
def service_env(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8", newline="\n")
    (root / "b.txt").write_text("world", encoding="utf-8", newline="\n")
    (root / "dup1.bin").write_bytes(b"\x00\x01\x02dup")
    (root / "dup2.bin").write_bytes(b"\x00\x01\x02dup")
    # 用临时文件库：设置文件（ui_settings.json）随库路径隔离，不碰真实数据
    config = SortaConfig(db_url=f"sqlite:///{tmp_path / 'test.db'}")
    engine = create_db_engine(config.db_url)
    init_db(engine)
    store = PlanStore(engine)
    service = SortaService(config=config, store=store)
    return SimpleNamespace(root=root, config=config, store=store, service=service)


def _mock_llm(scan_destination: str = "Notes/a.txt"):
    def submit(request):
        payload = tool_payloads(request)[0]
        file_id = payload["files"][0]["id"]
        return resp(
            tc(
                "submit_plan",
                {
                    "scan_id": payload["scan_id"],
                    "summary": "服务层测试计划",
                    "operations": [
                        {
                            "source_id": file_id,
                            "destination": scan_destination,
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


def test_create_workspace_and_list_files(service_env):
    info = service_env.service.create_workspace(str(service_env.root))
    assert info.file_count == 4

    rows = service_env.service.list_files(info.scan_id, query="a.txt")
    assert len(rows) == 1 and rows[0]["path"] == "a.txt"
    assert rows[0]["file_id"].startswith("file_")


def test_generate_plan_persists_with_version_one(service_env):
    info = service_env.service.create_workspace(str(service_env.root))
    record = service_env.service.generate_plan(
        info.scan_id,
        "整理测试",
        privacy="authorized_content",
        model="mock-model",
        llm=_mock_llm(),
    )
    assert record.version == 1 and record.status == "draft"

    detail = service_env.service.get_plan_detail(record.id)
    assert detail.plan_hash == record.plan_hash
    assert detail.operations[0]["destination"] == "Notes/a.txt"


def test_edit_plan_increments_version_and_invalidates_approval(service_env):
    info = service_env.service.create_workspace(str(service_env.root))
    record = service_env.service.generate_plan(
        info.scan_id,
        "整理测试",
        privacy="authorized_content",
        model="mock-model",
        llm=_mock_llm(),
    )

    detail = service_env.service.edit_plan(
        record.id, keep=[True], destinations=["Renamed/a.txt"], expected_version=1
    )
    assert detail.version == 2
    assert detail.operations[0]["destination"] == "Renamed/a.txt"
    assert detail.plan_hash != record.plan_hash

    # 乐观锁：基于旧版本编辑被拒绝
    with pytest.raises(PlanVersionConflictError):
        service_env.service.edit_plan(
            record.id, keep=[True], destinations=["Other/a.txt"], expected_version=1
        )

    # 旧 hash 不能被批准：批准时校验当前 hash（§7.3 审批绑定）
    with pytest.raises(SortaError, match="plan_hash"):
        service_env.service.approve_plan(
            record.id, expected_version=2, expected_plan_hash=record.plan_hash
        )


def test_approve_execute_undo_roundtrip(service_env):
    info = service_env.service.create_workspace(str(service_env.root))
    record = service_env.service.generate_plan(
        info.scan_id,
        "整理测试",
        privacy="authorized_content",
        model="mock-model",
        llm=_mock_llm(),
    )
    approval = service_env.service.approve_plan(
        record.id, expected_version=record.version, expected_plan_hash=record.plan_hash
    )
    result = service_env.service.execute_approved(approval["approval_id"])
    assert result.status == "completed"
    assert (service_env.root / "Notes" / "a.txt").exists()

    undo = service_env.service.undo_execution(result.execution_id)
    assert undo.status == "undone"
    assert (service_env.root / "a.txt").exists()

    history = service_env.service.list_executions()
    assert history[0]["execution_id"] == result.execution_id


def test_duplicate_detection_and_preview(service_env):
    info = service_env.service.create_workspace(str(service_env.root))
    result = service_env.service.find_duplicates(info.scan_id)
    assert [g["files"] for g in result["groups"]] == [["dup1.bin", "dup2.bin"]]
    assert result["groups"][0]["wasted_bytes"] == 6
    assert result["hashed"] == 4 and result["skipped"] == 0  # a/b 同为 5B，也参与哈希

    rows = service_env.service.list_files(info.scan_id, query="a.txt")
    preview = service_env.service.file_preview(info.scan_id, rows[0]["file_id"])
    assert preview.status.value == "ok" and "hello" in (preview.content or "")


def test_plan_not_found(service_env):
    with pytest.raises(PlanNotFoundError):
        service_env.service.get_plan_detail(99999)


def test_settings_roundtrip(service_env):
    service_env.service.save_settings(root="C:/demo", privacy="metadata_only")
    settings = service_env.service.get_settings()
    assert settings["root"] == "C:/demo" and settings["privacy"] == "metadata_only"
    assert "has_api_key" in settings
    # 设置文件落在数据库同目录（JSON）
    assert service_env.service._settings_file().name == "ui_settings.json"


def test_model_settings_roundtrip_and_env_fallback(service_env, monkeypatch):
    """模型配置存设置文件且优先于环境变量；密钥只回显脱敏提示。"""
    service = service_env.service
    monkeypatch.setenv("SORTA_MODEL", "env-model")
    monkeypatch.setenv("SORTA_API_KEY", "sk-env-key-12345678")

    # 无设置文件：回退到环境变量
    settings = service.get_settings()
    assert settings["model"] == "env-model"
    assert settings["has_api_key"] is True

    # 保存 UI 配置：覆盖环境变量；api_key 缺省保持不变
    service.save_settings(model="qwen3.8-flash", base_url="https://example.com/v1")
    settings = service.get_settings()
    assert settings["model"] == "qwen3.8-flash"
    assert settings["base_url"] == "https://example.com/v1"
    assert settings["has_api_key"] is True
    assert settings["api_key_hint"].startswith("sk-")
    assert settings["api_key_hint"].endswith("5678")
    assert "****" in settings["api_key_hint"]
    assert "api_key" not in settings  # 明文密钥绝不返回

    # get_llm_config 供后端调用：返回明文
    llm_cfg = service.get_llm_config()
    assert llm_cfg["api_key"] == "sk-env-key-12345678"

    # 用户输入新密钥则覆盖
    service.save_settings(api_key="sk-new-key-99999999")
    assert service.get_llm_config()["api_key"] == "sk-new-key-99999999"
