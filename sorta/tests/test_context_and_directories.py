import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from sorta.agent.llm import LLMResponse
from sorta.agent.schemas import ExecutionPlan, FileOperation
from sorta.errors import SortaError
from sorta.planner.schemas import compute_plan_hash
from sorta.scanner.registry import FileRegistry
from sorta.storage import PlanStore, create_db_engine, init_db

from .conftest import approve


@pytest.fixture
def directory_plan(service, tmp_path):
    root = tmp_path / "cleanup"
    root.mkdir()
    (root / "空目录").mkdir()
    (root / "reverse").mkdir()
    (root / "nonempty").mkdir()
    (root / "nonempty/keep.txt").write_text("keep")
    (root / "hidden-content").mkdir()
    (root / "hidden-content/.keep").write_text("")
    (root / ".hidden-directory").mkdir()
    (root / "zero-byte.txt").write_text("")
    scan = service.create_workspace(str(root))
    llm = Mock()
    result = service.run_task(
        scan.scan_id,
        "把目录下的所有空文件夹删除",
        task_type="auto",
        privacy="metadata_only",
        model="test",
        llm=llm,
    )
    llm.chat.assert_not_called()
    return root, service.get_plan_detail(result["plan_id"])


def test_empty_directories_are_visible_but_not_counted_as_files(service, directory_plan):
    root, plan = directory_plan
    sources = {op["source"] for op in plan.operations}
    assert sources == {"空目录", "reverse"}
    assert not plan.has_errors
    assert {op["action"] for op in plan.operations} == {"rmdir"}
    assert len(service.list_files(plan.scan_id)) == 2
    assert (root / "空目录").is_dir()  # 审批前零变更。
    restored = service.store.load_session(plan.scan_id)
    assert not restored.find_by_path("空目录").is_regular_file


def test_cleanup_execute_and_undo(service, directory_plan):
    root, plan = directory_plan
    result = service.execute_approved(approve(service, plan))
    assert result.status == "completed"
    assert not (root / "空目录").exists()
    assert not (root / "reverse").exists()
    assert (root / "nonempty/keep.txt").read_text() == "keep"
    assert (root / "hidden-content/.keep").exists()
    assert (root / ".hidden-directory").is_dir()
    assert (root / "zero-byte.txt").is_file()
    assert root.is_dir()
    assert service.undo_execution(result.execution_id).status == "undone"
    assert (root / "空目录").is_dir() and (root / "reverse").is_dir()


def test_nested_empty_tree_is_planned_children_first(service, tmp_path):
    root = tmp_path / "tree"
    (root / "a/b/c").mkdir(parents=True)
    scan = service.create_workspace(str(root))
    plan = service.generate_plan(
        scan.scan_id, "删除所有空目录", privacy="metadata_only", model="test", llm=Mock()
    )
    detail = service.get_plan_detail(plan.id)
    assert [op["source"] for op in detail.operations] == ["a/b/c", "a/b", "a"]
    result = service.execute_approved(approve(service, detail))
    assert result.status == "completed" and not (root / "a").exists()
    assert service.undo_execution(result.execution_id).status == "undone"
    assert (root / "a/b/c").is_dir()


def test_directory_became_nonempty_after_approval_is_not_deleted(service, directory_plan):
    root, plan = directory_plan
    approval_id = approve(service, plan)
    created = root / "reverse/new.txt"
    created.write_text("new user data")
    with pytest.raises(SortaError):
        service.execute_approved(approval_id)
    assert created.read_text() == "new user data"
    assert (root / "空目录").is_dir()
    assert not service.list_executions()


def test_runtime_directory_race_preserves_content(service, directory_plan, monkeypatch):
    root, plan = directory_plan
    original = Path.rmdir

    def race(path):
        if path.name == "reverse":
            (path / "new.txt").write_text("created during execution")
        return original(path)

    monkeypatch.setattr(Path, "rmdir", race)
    result = service.execute_approved(approve(service, plan))
    assert result.status == "partial_failed"
    assert (root / "reverse/new.txt").read_text() == "created during execution"


def test_undo_will_not_overwrite_recreated_directory(service, directory_plan):
    root, plan = directory_plan
    result = service.execute_approved(approve(service, plan))
    (root / "reverse").mkdir()
    (root / "reverse/user.txt").write_text("keep")
    assert service.undo_execution(result.execution_id).status == "partially_undone"
    assert (root / "reverse/user.txt").read_text() == "keep"


def test_file_can_never_be_removed_as_empty_directory(service, make_plan):
    root, detail = make_plan()
    operation = detail.operations[0]
    plan = ExecutionPlan(
        scan_id=detail.scan_id,
        summary="invalid",
        operations=[
            FileOperation(
                source_id=operation["source_id"],
                destination=operation["source"],
                action="rmdir",
                reason="invalid",
                confidence="high",
            )
        ],
    )
    plan_id = service.store.save_plan(
        plan.model_dump(mode="json"), compute_plan_hash(plan), "draft"
    )
    assert service.get_plan_detail(plan_id).has_errors
    with pytest.raises(SortaError):
        approve(service, service.get_plan_detail(plan_id))
    assert (root / "file0.txt").exists()


def test_directory_deletion_survives_registry_restart(service, directory_plan):
    root, detail = directory_plan
    approval_id = approve(service, detail)
    service.registry = FileRegistry()
    assert service.execute_approved(approval_id).status == "completed"
    assert not (root / "reverse").exists()


@pytest.mark.parametrize(
    "instruction",
    [
        "不要删除所有空目录",
        "别清理所有空文件夹",
        r"删除 D:\outside 下的所有空目录",
        "只删除 reverse 这个空目录",
        "删除所有空目录，但 reverse 不要动",
        "删除课程目录下所有空目录",
    ],
)
def test_negative_or_different_scope_does_not_trigger_builtin_cleanup(service, instruction):
    assert not service.is_empty_directory_cleanup(instruction, Path(r"D:\authorized"))


def test_early_user_constraints_are_retained_after_recent_window(service, make_plan):
    _, plan = make_plan()
    conversation = service.create_conversation(plan.scan_id)
    service.append_message(conversation["id"], "user", "安装包不要移动，文件正文不授权")
    for i in range(12):
        service.append_message(conversation["id"], "user", f"问题 {i}")
        service.append_message(conversation["id"], "assistant", f"回答 {i}")
    context = service._conversation_context(conversation["id"])
    assert "安装包不要移动，文件正文不授权" in context
    assert "问题 11" in context


def test_classifier_receives_authorized_root_and_prior_messages(service, make_plan):
    root, plan = make_plan()
    conversation = service.create_conversation(plan.scan_id)
    service.append_message(conversation["id"], "user", "安装包不要移动")
    llm = Mock()
    llm.chat.side_effect = [
        LLMResponse('{"task_type":"ask","reason":"已知目录"}'),
        LLMResponse("目录已配置"),
    ]
    service.run_task(
        plan.scan_id,
        "根目录是什么",
        task_type="auto",
        privacy="metadata_only",
        model="test",
        llm=llm,
        conversation_id=conversation["id"],
    )
    prompt = str(llm.chat.call_args_list[0].kwargs["messages"])
    assert str(root) in prompt.replace("\\\\", "\\")
    assert "安装包不要移动" in prompt


def test_revision_receives_history_and_links_conversation(service, make_plan):
    _, plan = make_plan()
    conversation = service.create_conversation(plan.scan_id)
    service.append_message(conversation["id"], "user", "安装包不要动")
    llm = Mock()
    llm.chat.return_value = LLMResponse(json.dumps(service.store.get_plan(plan.plan_id).plan_json))
    service.revise_plan(
        plan.plan_id,
        "其他照旧",
        expected_version=plan.version,
        model="test",
        llm=llm,
        conversation_id=conversation["id"],
    )
    assert "安装包不要动" in str(llm.chat.call_args.kwargs["messages"])
    assert service.get_conversation(conversation["id"])["plan_id"] == plan.plan_id


def test_execution_and_undo_results_become_conversation_context(service, directory_plan):
    _, plan = directory_plan
    conv = service.create_conversation(plan.scan_id)
    service.link_conversation_plan(conv["id"], plan.plan_id)
    result = service.execute_approved(approve(service, plan))
    context = service._conversation_context(conv["id"])
    assert "completed" in context and result.execution_id in context
    service.undo_execution(result.execution_id)
    context = service._conversation_context(conv["id"])
    assert "undone" in context


def test_new_cleanup_task_is_not_revision_of_pending_file_plan(service, make_plan):
    root, plan = make_plan()
    (root / "empty").mkdir()
    conv = service.create_conversation(plan.scan_id)
    service.link_conversation_plan(conv["id"], plan.plan_id)
    llm = Mock()
    events = list(
        service.stream_task(
            plan.scan_id,
            "删除所有空文件夹",
            task_type="auto",
            privacy="metadata_only",
            model="test",
            llm=llm,
            conversation_id=conv["id"],
            active_plan_id=plan.plan_id,
        )
    )
    assert events[-1]["type"] == "organize"
    llm.chat.assert_not_called()


def test_general_question_can_be_asked_while_plan_is_pending(service, make_plan):
    _, plan = make_plan()
    llm = Mock()
    llm.chat.return_value = LLMResponse('{"task_type":"ask","reason":"解释计划"}')
    llm.stream_chat.return_value = iter(["因为文件名包含课程名。"])
    events = list(
        service.stream_task(
            plan.scan_id,
            "为什么这样安排？",
            task_type="auto",
            privacy="metadata_only",
            model="test",
            llm=llm,
            active_plan_id=plan.plan_id,
        )
    )
    assert events[-1]["type"] == "done"
    assert service.store.get_plan(plan.plan_id).version == plan.version


def test_explicit_revision_is_routed_to_existing_plan(service, make_plan):
    _, plan = make_plan()
    llm = Mock()
    llm.chat.return_value = LLMResponse('{"task_type":"revise","reason":"调整目标"}')
    events = list(
        service.stream_task(
            plan.scan_id,
            "目标目录换成学习资料",
            task_type="auto",
            privacy="metadata_only",
            model="test",
            llm=llm,
            active_plan_id=plan.plan_id,
        )
    )
    assert events[-1]["type"] == "revise"
    assert events[-1]["plan_id"] == plan.plan_id


def test_v3_database_migration_preserves_records_and_conversations(tmp_path):
    database = tmp_path / "v3.db"
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE scan_sessions (
                id TEXT PRIMARY KEY, root_path TEXT, created_at BIGINT, expires_at BIGINT);
            CREATE TABLE file_registry (
                id INTEGER PRIMARY KEY, scan_id TEXT, file_id TEXT, relative_path TEXT,
                extension TEXT, size BIGINT, modified_ns BIGINT);
            CREATE TABLE conversations (
                id INTEGER PRIMARY KEY, scan_id TEXT, title TEXT,
                created_at BIGINT, updated_at BIGINT);
            PRAGMA user_version = 3;
        """)
        now = time.time()
        db.execute(
            "INSERT INTO scan_sessions VALUES (?, ?, ?, ?)",
            ("scan_old", str(tmp_path), now, now + 3600),
        )
        db.execute(
            "INSERT INTO file_registry VALUES (1, 'scan_old', 'file_old', 'keep.txt', 'txt', 4, 1)"
        )
        db.execute("INSERT INTO conversations VALUES (1, 'scan_old', '保留对话', ?, ?)", (now, now))
    engine = create_db_engine(f"sqlite:///{database.as_posix()}")
    try:
        init_db(engine)
        store = PlanStore(engine)
        assert store.load_session("scan_old").get("file_old").is_regular_file
        from sqlalchemy import text

        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT title FROM conversations")).scalar_one()
                == "保留对话"
            )
            assert connection.execute(text("PRAGMA user_version")).scalar_one() == 4
    finally:
        engine.dispose()


def test_links_are_not_registered_or_cleaned(service, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("创建符号链接需要本机权限")
    (root / "empty").mkdir()
    scan = service.create_workspace(str(root))
    record = service.generate_plan(
        scan.scan_id, "删除所有空目录", privacy="metadata_only", model="test", llm=Mock()
    )
    detail = service.get_plan_detail(record.id)
    assert [op["source"] for op in detail.operations] == ["empty"]
    service.execute_approved(approve(service, detail))
    assert outside.is_dir() and (root / "linked").is_symlink()


def test_completed_plan_remains_readable_after_snapshot_expires(service, directory_plan):
    from sqlalchemy import update

    from sorta.storage.models import ScanSessionRow

    _, plan = directory_plan
    service.execute_approved(approve(service, plan))
    with service.store.engine.begin() as connection:
        connection.execute(
            update(ScanSessionRow).where(ScanSessionRow.id == plan.scan_id).values(expires_at=0)
        )
    detail = service.get_plan_detail(plan.plan_id)
    assert detail.status == "completed" and not detail.has_errors
    assert len(detail.operations) == 2
    with pytest.raises(SortaError):
        service._ensure_session(plan.scan_id)
