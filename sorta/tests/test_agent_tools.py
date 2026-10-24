"""ToolDispatcher 测试：白名单、参数校验、隐私门、内容缓存与检索诚实性（§6）。"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from sorta.agent.tools import PLAN_TOOL, READ_ONLY_TOOLS, ToolDispatcher
from sorta.config import SortaConfig
from sorta.scanner import FileRegistry, Scanner


def payload(record) -> dict:
    return json.loads(record.result_json)


@pytest.fixture
def env(tmp_path):
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
    (tmp_path / "b 数据.json").write_text('{"k": "笔记内容"}', encoding="utf-8")
    (tmp_path / "img.png").write_bytes(b"\x89PNG fake")
    config = SortaConfig()
    registry = FileRegistry()
    scanner = Scanner(registry, config)
    session = registry.create_session(tmp_path)
    scanner.scan(tmp_path, session=session)  # 预登记文件，模拟 list_files 已执行
    dispatcher = ToolDispatcher(
        registry=registry,
        session=session,
        scanner=scanner,
        config=config,
        consent_callback=lambda record: True,  # 读取类测试默认放行；隐私门测试单独覆盖
    )
    return dispatcher, session


def _first_file_id(dispatcher) -> str:
    record = dispatcher.dispatch("list_files")
    return payload(record)["files"][0]["id"]


def test_whitelist_rejects_non_readonly_tools(env):
    dispatcher, _ = env
    for name in ("shell", "move", "copy", "delete", "network_upload"):
        record = dispatcher.dispatch(name, "{}")
        assert record.ok is False
        assert "白名单" in record.summary
    assert set(READ_ONLY_TOOLS) <= {s["function"]["name"] for s in dispatcher.specs}
    # submit_plan 是计划提交通道：必须声明在工具清单中供模型调用，
    # 但绕过 controller 直接调度时仍被 dispatch 层拒绝（防御纵深）
    spec_names = {s["function"]["name"] for s in dispatcher.specs}
    assert PLAN_TOOL in spec_names
    record = dispatcher.dispatch(PLAN_TOOL, "{}")
    assert record.ok is False and "白名单" in record.summary


def test_list_files_uses_precreated_session_and_pagination(env):
    dispatcher, session = env
    r1 = dispatcher.dispatch("list_files", '{"limit": 1}')
    p1 = payload(r1)
    assert r1.ok and p1["scan_id"] == session.scan_id
    assert len(p1["files"]) == 1 and p1["has_more"] is True

    r2 = dispatcher.dispatch("list_files", json.dumps({"cursor": p1["next_cursor"]}))
    p2 = payload(r2)
    assert p2["scan_id"] == session.scan_id
    assert len(p1["files"]) + len(p2["files"]) == 3
    assert p2["has_more"] is False


def test_get_file_info_readable_and_unsupported(env):
    dispatcher, _ = env
    ids = {}
    record = dispatcher.dispatch("list_files")
    for f in payload(record)["files"]:
        ids[f["extension"]] = f["id"]

    info = payload(dispatcher.dispatch("get_file_info", json.dumps({"file_id": ids["txt"]})))
    assert info["readable"] is True and info["parse_format"] == "txt"
    assert info["state"] == "unchanged"

    info = payload(dispatcher.dispatch("get_file_info", json.dumps({"file_id": ids["png"]})))
    assert info["readable"] is False and info["parse_format"] is None
    assert any("格式" in n for n in info["notes"])


def test_get_file_info_unknown_id(env):
    dispatcher, _ = env
    record = dispatcher.dispatch("get_file_info", '{"file_id": "file_missing"}')
    assert record.ok is False and "未知" in record.summary


def test_read_file_caches_content_and_search_finds_it(env):
    dispatcher, _ = env
    fid = _first_file_id(dispatcher)  # a.txt 排序最先

    read = payload(dispatcher.dispatch("read_file", json.dumps({"file_id": fid})))
    assert read["status"] == "ok"
    assert read["content"] == "hello world" and read["truncated"] is False

    search = payload(dispatcher.dispatch("search_files", '{"query": "hello", "scope": "content"}'))
    assert len(search["matches"]) == 1
    assert search["matches"][0]["file_id"] == fid
    assert search["matches"][0]["match_types"] == ["content"]
    assert "已读取" in search["note"]


def test_search_name_match_without_content(env):
    dispatcher, _ = env
    search = payload(dispatcher.dispatch("search_files", '{"query": "数据", "scope": "name"}'))
    assert [m["relative_path"] for m in search["matches"]] == ["b 数据.json"]
    assert search["scanned_contents"] == 0


def test_search_content_requires_prior_read(env):
    dispatcher, _ = env
    search = payload(dispatcher.dispatch("search_files", '{"query": "hello", "scope": "content"}'))
    assert search["matches"] == []
    assert "未读取" in search["note"]


def test_arg_validation_errors(env):
    dispatcher, _ = env
    for name, bad_args in [
        ("get_file_info", "{}"),
        ("get_file_info", '{"file_id": 123}'),
        ("read_file", '{"max_chars": "abc"}'),
        ("search_files", '{"query": "   "}'),
        ("search_files", '{"query": "x", "scope": "everything"}'),
    ]:
        record = dispatcher.dispatch(name, bad_args)
        assert record.ok is False, f"{name} {bad_args} 应被拒绝"
        assert "参数" in record.summary or "校验" in record.summary


def test_ask_before_content_consent_denied_once(env):
    dispatcher, _ = env
    calls: list[str] = []

    def deny(record) -> bool:
        calls.append(record.relative_path)
        return False

    dispatcher.consent_callback = deny
    fid = _first_file_id(dispatcher)
    record = dispatcher.dispatch("read_file", json.dumps({"file_id": fid}))
    assert record.ok is False and "拒绝" in record.summary

    dispatcher.dispatch("read_file", json.dumps({"file_id": fid}))  # 二次尝试
    assert calls == ["a.txt"]  # 同一文件只询问一次


def test_ask_before_content_consent_granted(env):
    dispatcher, _ = env
    dispatcher.consent_callback = lambda record: True
    fid = _first_file_id(dispatcher)
    record = dispatcher.dispatch("read_file", json.dumps({"file_id": fid}))
    assert record.ok is True


def test_metadata_only_blocks_read_file(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    config = replace(SortaConfig(), default_privacy="metadata_only")
    registry = FileRegistry()
    session = registry.create_session(tmp_path)
    dispatcher = ToolDispatcher(
        registry=registry, session=session, scanner=Scanner(registry, config), config=config
    )
    names = {s["function"]["name"] for s in dispatcher.specs}
    assert "read_file" not in names

    record = dispatcher.dispatch("read_file", '{"file_id": "file_x"}')
    assert record.ok is False and "隐私" in record.summary


def test_authorized_content_reads_without_prompt(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    config = replace(SortaConfig(), default_privacy="authorized_content")
    registry = FileRegistry()
    session = registry.create_session(tmp_path)
    dispatcher = ToolDispatcher(
        registry=registry, session=session, scanner=Scanner(registry, config), config=config
    )

    def must_not_ask(record) -> bool:
        raise AssertionError("authorized_content 模式不应触发确认")

    dispatcher.consent_callback = must_not_ask
    record = dispatcher.dispatch("list_files")
    fid = payload(record)["files"][0]["id"]
    assert dispatcher.dispatch("read_file", json.dumps({"file_id": fid})).ok is True


def test_read_file_max_chars_clamped_to_config(tmp_path):
    (tmp_path / "long.txt").write_text("A" * 100, encoding="utf-8")
    config = replace(SortaConfig(), max_read_chars=10)
    registry = FileRegistry()
    session = registry.create_session(tmp_path)
    dispatcher = ToolDispatcher(
        registry=registry,
        session=session,
        scanner=Scanner(registry, config),
        config=config,
        consent_callback=lambda record: True,
    )
    fid = payload(dispatcher.dispatch("list_files"))["files"][0]["id"]
    read = payload(
        dispatcher.dispatch("read_file", json.dumps({"file_id": fid, "max_chars": 1000}))
    )
    assert read["content"] == "A" * 10 and read["truncated"] is True
    assert "收敛" in read["note"]
