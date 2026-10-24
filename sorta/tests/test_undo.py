"""UndoEngine 测试（开发文档 §8.3）：条件撤销、冲突拒绝、部分撤销。"""

from __future__ import annotations

import pytest
from conftest import execute_plan, make_plan

from sorta.errors import SortaError
from sorta.executor import UndoEngine
from sorta.planner import compute_plan_hash


@pytest.fixture
def undo_engine(m3_env):
    return UndoEngine(store=m3_env.store)


def _move_a_to_out(m3_env):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    result = execute_plan(m3_env, plan)
    assert result.status == "completed"
    return result


def test_undo_move_restores_file(m3_env, undo_engine):
    result = _move_a_to_out(m3_env)
    undo = undo_engine.undo_execution(result.execution_id)

    assert undo.status == "undone"
    assert (m3_env.root / "a.txt").read_text(encoding="utf-8") == "hello"
    assert not (m3_env.root / "Out" / "a.txt").exists()

    logs = m3_env.store.list_operations(result.execution_id)
    assert [log.status for log in logs] == ["undone"]


def test_undo_updates_plan_status(m3_env, undo_engine):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    result = execute_plan(m3_env, plan)
    undo_engine.undo_execution(result.execution_id)
    plan_row = m3_env.store.get_plan_by_hash(compute_plan_hash(plan))
    assert plan_row.status == "undone"


def test_undo_copy_removes_only_the_copy(m3_env, undo_engine):
    plan = make_plan(m3_env.session, [("b.txt", "Backup/b.txt")], action="copy")
    result = execute_plan(m3_env, plan)
    undo_engine.undo_execution(result.execution_id)

    assert (m3_env.root / "b.txt").read_text(encoding="utf-8") == "world"
    assert not (m3_env.root / "Backup" / "b.txt").exists()


def test_undo_conflict_when_dest_modified(m3_env, undo_engine):
    """安全不变量：Undo 不得覆盖后来被修改的文件（§10.3）。"""
    result = _move_a_to_out(m3_env)
    (m3_env.root / "Out" / "a.txt").write_text("user edited", encoding="utf-8", newline="\n")

    undo = undo_engine.undo_execution(result.execution_id)
    assert undo.status == "partially_undone"
    assert undo.operations[0].status == "undo_conflict"
    assert "被修改" in undo.operations[0].error
    # 现场保留：修改后的文件原样不动
    assert (m3_env.root / "Out" / "a.txt").read_text(encoding="utf-8") == "user edited"
    assert not (m3_env.root / "a.txt").exists()
    plan_row = m3_env.store.get_plan_by_hash(
        compute_plan_hash(make_plan(m3_env.session, [("a.txt", "Out/a.txt")]))
    )
    assert plan_row.status == "partially_undone"


def test_undo_conflict_when_source_occupied(m3_env, undo_engine):
    result = _move_a_to_out(m3_env)
    (m3_env.root / "a.txt").write_text("new occupant", encoding="utf-8", newline="\n")

    undo = undo_engine.undo_execution(result.execution_id)
    assert undo.operations[0].status == "undo_conflict"
    assert "原路径已被占用" in undo.operations[0].error
    assert (m3_env.root / "a.txt").read_text(encoding="utf-8") == "new occupant"
    assert (m3_env.root / "Out" / "a.txt").exists()  # 目标保留


def test_undo_only_succeeded_ops_in_partial_batch(m3_env, undo_engine):
    """集成场景 §13.2 第 6 条：3 项中第 2 项失败 → 如实报告，仅撤销已成功项。"""
    (m3_env.root / "Out").mkdir()
    (m3_env.root / "Out" / "b.txt").write_text("occupied", encoding="utf-8")
    (m3_env.root / "sub" / "d.txt").write_text("d", encoding="utf-8", newline="\n")
    # 让 sub/d.txt 进入注册表：重新扫描
    m3_env.scanner.scan(m3_env.root, session=m3_env.session)

    plan = make_plan(
        m3_env.session,
        [
            ("a.txt", "Out/a.txt"),
            ("b.txt", "Out/b.txt"),  # 目标已存在 → 必失败
            ("sub/d.txt", "Out/d.txt"),
        ],
    )
    result = execute_plan(m3_env, plan)
    assert result.status == "partial_failed"

    undo = undo_engine.undo_execution(result.execution_id)
    assert undo.status == "undone"
    statuses = {op.operation_index: op.status for op in undo.operations}
    assert statuses == {0: "undone", 2: "undone"}  # 只含成功项
    assert (m3_env.root / "a.txt").exists() and (m3_env.root / "sub" / "d.txt").exists()
    assert (m3_env.root / "Out" / "b.txt").read_text(encoding="utf-8") == "occupied"


def test_undo_nonexistent_batch_rejected(undo_engine):
    with pytest.raises(SortaError, match="不存在"):
        undo_engine.undo_execution("exec_nope")


def test_double_undo_rejected(m3_env, undo_engine):
    result = _move_a_to_out(m3_env)
    undo_engine.undo_execution(result.execution_id)
    with pytest.raises(SortaError, match="没有可撤销"):
        undo_engine.undo_execution(result.execution_id)
    # 文件仍处于已恢复状态
    assert (m3_env.root / "a.txt").exists()
