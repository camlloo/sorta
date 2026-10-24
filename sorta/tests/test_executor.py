"""ExecutionEngine 测试（开发文档 §8.1）：安全执行、无覆盖、部分失败、审批绑定。"""

from __future__ import annotations

import pytest
from conftest import approve, execute_plan, make_plan

from sorta.executor import (
    ApprovalMismatchError,
    DuplicateExecutionError,
    PlanStaleError,
    recover_interrupted,
)
from sorta.planner import compute_plan_hash
from sorta.storage import OperationLogRecord


def test_move_executes_and_journals(m3_env):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    result = execute_plan(m3_env, plan)

    assert result.status == "completed"
    assert (m3_env.root / "Out" / "a.txt").read_text(encoding="utf-8") == "hello"
    assert not (m3_env.root / "a.txt").exists()

    plan_row = m3_env.store.get_plan_by_hash(compute_plan_hash(plan))
    assert plan_row.status == "completed"
    logs = m3_env.store.list_operations(result.execution_id)
    assert [log.status for log in logs] == ["succeeded"]
    assert logs[0].source_fingerprint_before == logs[0].dest_fingerprint_after
    assert m3_env.store.get_execution(result.execution_id).status == "completed"


def test_move_creates_nested_directories(m3_env):
    plan = make_plan(m3_env.session, [("sub/c.txt", "Deep/Nested/c.txt")])
    result = execute_plan(m3_env, plan)
    assert result.status == "completed"
    assert (m3_env.root / "Deep" / "Nested" / "c.txt").read_text(encoding="utf-8") == "deep"
    assert not (m3_env.root / "sub" / "c.txt").exists()


def test_copy_keeps_original(m3_env):
    plan = make_plan(m3_env.session, [("b.txt", "Backup/b.txt")], action="copy")
    result = execute_plan(m3_env, plan)
    assert result.status == "completed"
    assert (m3_env.root / "b.txt").read_text(encoding="utf-8") == "world"
    assert (m3_env.root / "Backup" / "b.txt").read_text(encoding="utf-8") == "world"


def test_destination_conflict_never_overwrites(m3_env):
    """安全不变量：发现冲突不得覆盖已有文件（§10.3）。"""
    (m3_env.root / "Out").mkdir()
    (m3_env.root / "Out" / "a.txt").write_text("precious", encoding="utf-8", newline="\n")
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt"), ("b.txt", "Out/b.txt")])
    result = execute_plan(m3_env, plan)

    assert result.status == "partial_failed"
    assert (m3_env.root / "Out" / "a.txt").read_text(encoding="utf-8") == "precious"
    assert (m3_env.root / "Out" / "b.txt").exists()  # 其余操作照常执行
    failed = [op for op in result.operations if op.status == "failed"]
    assert len(failed) == 1 and "拒绝覆盖" in failed[0].error


def test_all_failed_reports_failed(m3_env):
    (m3_env.root / "Out").mkdir()
    (m3_env.root / "Out" / "a.txt").write_text("x", encoding="utf-8")
    (m3_env.root / "Out" / "b.txt").write_text("x", encoding="utf-8")
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt"), ("b.txt", "Out/b.txt")])
    result = execute_plan(m3_env, plan)
    assert result.status == "failed"


def test_duplicate_execution_rejected(m3_env):
    """同一审批不能执行两次（防确认按钮重复点击，§9）。"""
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    _, approval = approve(m3_env, plan)
    execute_plan(m3_env, plan, approval=approval)

    from sorta.executor import ExecutionEngine

    engine = ExecutionEngine(
        registry=m3_env.registry,
        scanner=m3_env.scanner,
        config=m3_env.config,
        store=m3_env.store,
    )
    with pytest.raises(DuplicateExecutionError, match="已执行"):
        engine.execute(plan, approval_id=approval.id)


def test_execution_without_approval_rejected(m3_env):
    """安全不变量：未审批不产生任何真实文件变更（§10.3）。"""
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    from sorta.executor import ExecutionEngine

    engine = ExecutionEngine(
        registry=m3_env.registry,
        scanner=m3_env.scanner,
        config=m3_env.config,
        store=m3_env.store,
    )
    with pytest.raises(ApprovalMismatchError, match="审批"):
        engine.execute(plan, approval_id=999999)
    assert (m3_env.root / "a.txt").exists()
    assert not (m3_env.root / "Out").exists()


def test_edited_plan_rejected_by_hash(m3_env):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    _, approval = approve(m3_env, plan)
    edited = plan.model_copy(deep=True)
    edited.operations[0].destination = "Out2/a.txt"
    from sorta.executor import ExecutionEngine

    engine = ExecutionEngine(
        registry=m3_env.registry,
        scanner=m3_env.scanner,
        config=m3_env.config,
        store=m3_env.store,
    )
    with pytest.raises(ApprovalMismatchError, match="审批"):
        engine.execute(edited, approval_id=approval.id)


def test_source_modified_after_approval_goes_stale(m3_env):
    """审批后源文件变化 → 执行前重验拦截，计划置 STALE（§4.2）。"""
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    _, approval = approve(m3_env, plan)
    (m3_env.root / "a.txt").write_text("tampered!", encoding="utf-8", newline="\n")

    from sorta.executor import ExecutionEngine

    engine = ExecutionEngine(
        registry=m3_env.registry,
        scanner=m3_env.scanner,
        config=m3_env.config,
        store=m3_env.store,
    )
    with pytest.raises(PlanStaleError, match="STALE"):
        engine.execute(plan, approval_id=approval.id)
    plan_row = m3_env.store.get_plan_by_hash(compute_plan_hash(plan))
    assert plan_row.status == "stale"
    assert not (m3_env.root / "Out").exists()


def test_execution_id_deterministic(m3_env):
    from sorta.executor import execution_id_for

    assert execution_id_for("h" * 64, 1) == execution_id_for("h" * 64, 1)
    assert execution_id_for("h" * 64, 1) != execution_id_for("h" * 64, 2)


def test_recover_interrupted(m3_env):
    """崩溃恢复：按真实文件状态核对 RUNNING 日志，不盲目重放（§8.3）。"""
    from sorta.planner import compute_plan_hash

    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    plan_hash = compute_plan_hash(plan)
    plan_id = m3_env.store.save_plan(plan.model_dump(mode="json"), plan_hash, status="draft")

    # 场景 1：实际已移动完成（源没了、目标在且指纹一致）→ 补记 SUCCEEDED
    (m3_env.root / "Out").mkdir()
    import shutil

    shutil.move(str(m3_env.root / "a.txt"), str(m3_env.root / "Out" / "a.txt"))
    from sorta.executor.safety import metadata_fingerprint

    m3_env.store.create_execution("exec_recover", plan_id, plan_hash)
    m3_env.store.upsert_operation(
        "exec_recover",
        OperationLogRecord(
            operation_index=0,
            source_relpath="a.txt",
            destination_relpath="Out/a.txt",
            action="move",
            status="running",
            dest_fingerprint_after=metadata_fingerprint(m3_env.root / "Out" / "a.txt"),
        ),
    )
    report = recover_interrupted(m3_env.store)
    logs = m3_env.store.list_operations("exec_recover")
    assert logs[0].status == "succeeded"
    assert any("补记 SUCCEEDED" in note for _, _, note in report)

    # 场景 2：中断时什么都没发生 → FAILED
    m3_env.store.upsert_operation(
        "exec_recover",
        OperationLogRecord(
            operation_index=1,
            source_relpath="b.txt",
            destination_relpath="Out/b.txt",
            action="move",
            status="running",
        ),
    )
    recover_interrupted(m3_env.store)
    logs = m3_env.store.list_operations("exec_recover")
    assert logs[1].status == "failed"
