"""PlanStore / SQLite 持久化测试（开发文档 §9）。"""

from __future__ import annotations

import time

import pytest
from conftest import make_plan
from sqlalchemy.exc import IntegrityError

from sorta.errors import ScanSessionError
from sorta.scanner.registry import ScanSession
from sorta.storage import (
    IllegalPlanTransition,
    OperationLogRecord,
    create_db_engine,
    init_db,
)


@pytest.fixture
def store(m3_env):
    return m3_env.store


def test_foreign_keys_enforced(m3_env):
    """PRAGMA foreign_keys=ON 必须真实生效（SQLite 默认关闭）。"""
    from sorta.storage.models import FileRegistryRow

    engine = create_db_engine("sqlite://")
    init_db(engine)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            FileRegistryRow.__table__.insert().values(
                scan_id="scan_nonexistent",
                file_id="file_x",
                relative_path="a.txt",
                extension="txt",
                size=1,
                modified_ns=1,
            )
        )


def test_session_roundtrip(m3_env):
    loaded = m3_env.store.load_session(m3_env.scan_id)
    assert str(loaded.root) == str(m3_env.session.root)
    assert {f.id for f in loaded.files.values()} == {f.id for f in m3_env.session.files.values()}
    for record in loaded.files.values():
        original = m3_env.session.get(record.id)
        assert record.relative_path == original.relative_path
        assert record.size == original.size
        assert record.modified_ns == original.modified_ns


def test_load_session_unknown_and_expired(m3_env):
    with pytest.raises(ScanSessionError, match="不存在"):
        m3_env.store.load_session("scan_nope")

    expired = ScanSession.from_records(
        scan_id="scan_expired",
        root=m3_env.root,
        created_at=time.time() - 100,
        expires_at=time.time() - 50,
        records=list(m3_env.session.files.values()),
    )
    m3_env.store.save_session(expired)
    with pytest.raises(ScanSessionError, match="过期"):
        m3_env.store.load_session("scan_expired")
    # 撤销场景：过期会话仍能取到授权根目录
    assert m3_env.store.get_session_root("scan_expired") == str(expired.root)


def test_plan_save_and_get_by_hash(m3_env):
    from sorta.planner import compute_plan_hash

    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    plan_hash = compute_plan_hash(plan)
    plan_json = plan.model_dump(mode="json")

    plan_id1 = m3_env.store.save_plan(plan_json, plan_hash, status="draft")
    plan_id2 = m3_env.store.save_plan(plan_json, plan_hash, status="draft")  # 幂等
    assert plan_id1 == plan_id2

    record = m3_env.store.get_plan_by_hash(plan_hash)
    assert record is not None and record.id == plan_id1
    assert record.plan_json["operations"][0]["destination"] == "Out/a.txt"
    assert record.status == "draft"


def test_plan_state_machine_enforced(m3_env):
    from sorta.planner import compute_plan_hash

    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    plan_id = m3_env.store.save_plan(
        plan.model_dump(mode="json"), compute_plan_hash(plan), status="draft"
    )
    with pytest.raises(IllegalPlanTransition):
        m3_env.store.update_plan_status(plan_id, "executing")  # draft 不能直接执行

    for status in (
        "validated",
        "awaiting_approval",
        "approved",
        "executing",
        "completed",
        "undo_requested",
        "undone",
    ):
        m3_env.store.update_plan_status(plan_id, status)


def test_approval_binds_plan_hash(m3_env):
    from sorta.planner import compute_plan_hash

    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    plan_hash = compute_plan_hash(plan)
    plan_id = m3_env.store.save_plan(plan.model_dump(mode="json"), plan_hash, status="draft")

    approval = m3_env.store.save_approval(plan_id, plan_hash)
    loaded = m3_env.store.get_approval(plan_hash)
    assert loaded is not None and loaded.id == approval.id
    assert m3_env.store.get_approval("0" * 64) is None


def test_execution_idempotent_and_logs(m3_env):
    from sorta.planner import compute_plan_hash

    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt"), ("b.txt", "Out/b.txt")])
    plan_hash = compute_plan_hash(plan)
    plan_id = m3_env.store.save_plan(plan.model_dump(mode="json"), plan_hash, status="draft")

    assert m3_env.store.create_execution("exec_1", plan_id, plan_hash) is True
    assert m3_env.store.create_execution("exec_1", plan_id, plan_hash) is False  # 防重复点击

    for index, (src, dst) in enumerate([("a.txt", "Out/a.txt"), ("b.txt", "Out/b.txt")]):
        m3_env.store.upsert_operation(
            "exec_1",
            OperationLogRecord(
                operation_index=index,
                source_relpath=src,
                destination_relpath=dst,
                action="move",
                status="pending",
            ),
        )
    m3_env.store.upsert_operation(
        "exec_1",
        OperationLogRecord(
            operation_index=0,
            source_relpath="a.txt",
            destination_relpath="Out/a.txt",
            action="move",
            status="succeeded",
            source_fingerprint_before="5:111",
            dest_fingerprint_after="5:111",
        ),
    )  # upsert 更新而非新增

    logs = m3_env.store.list_operations("exec_1")
    assert len(logs) == 2
    assert logs[0].status == "succeeded" and logs[0].source_fingerprint_before == "5:111"
    assert logs[1].status == "pending"

    stuck = m3_env.store.list_operations_by_status(["pending"])
    assert len(stuck) == 1 and stuck[0].execution_id == "exec_1"
