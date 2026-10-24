"""PlanValidator 测试（开发文档 §7.2 十项检查）。"""

from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import make_plan

from sorta.planner import PlanValidator, compute_plan_hash, has_errors


@pytest.fixture
def validator(m3_env):
    return PlanValidator(m3_env.registry, m3_env.config)


def codes(issues):
    return {i.code for i in issues}


def test_valid_plan_passes(m3_env, validator):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt"), ("sub/c.txt", "Out/c.txt")])
    session, issues = validator.validate(plan)
    assert session is not None and issues == []


def test_unknown_scan_id_rejected(m3_env, validator):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    plan = plan.model_copy(update={"scan_id": "scan_nope"})
    session, issues = validator.validate(plan)
    assert session is None
    assert codes(issues) == {"SESSION_INVALID"}


def test_unknown_source_rejected(m3_env, validator):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    operations = [op.model_copy(update={"source_id": "file_ghost"}) for op in plan.operations]
    plan = plan.model_copy(update={"operations": operations})
    _, issues = validator.validate(plan)
    assert "UNKNOWN_SOURCE" in codes(issues)


def test_destination_exists_conflict(m3_env, validator):
    (m3_env.root / "Out").mkdir()
    (m3_env.root / "Out" / "a.txt").write_text("occupied", encoding="utf-8")
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    _, issues = validator.validate(plan)
    assert "DESTINATION_EXISTS" in codes(issues)


def test_duplicate_sources_and_destinations(m3_env, validator):
    plan = make_plan(
        m3_env.session,
        [("a.txt", "Out/a.txt"), ("a.txt", "Out2/a.txt")],  # 同源两个操作
    )
    _, issues = validator.validate(plan)
    assert "DUPLICATE_SOURCE" in codes(issues)


def test_duplicate_destination_case_insensitive(m3_env, validator):
    """NTFS 大小写不敏感：Out/a.txt 与 out/A.TXT 是同一目标（§9 应用层归一化）。"""
    plan = make_plan(
        m3_env.session,
        [("a.txt", "Out/a.txt"), ("b.txt", "out/A.TXT")],
    )
    _, issues = validator.validate(plan)
    assert "DUPLICATE_DESTINATION" in codes(issues)


def test_source_modified_and_missing(m3_env, validator):
    (m3_env.root / "a.txt").write_text("changed content", encoding="utf-8", newline="\n")
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    _, issues = validator.validate(plan)
    assert "SOURCE_MODIFIED" in codes(issues) and has_errors(issues)

    (m3_env.root / "b.txt").unlink()
    plan = make_plan(m3_env.session, [("b.txt", "Out/b.txt")])
    _, issues = validator.validate(plan)
    assert "SOURCE_MISSING" in codes(issues)


def test_source_equals_destination(m3_env, validator):
    plan = make_plan(m3_env.session, [("a.txt", "a.txt")])
    _, issues = validator.validate(plan)
    assert "SOURCE_EQUALS_DESTINATION" in codes(issues)


def test_reserved_name_rejected(m3_env, validator):
    plan = make_plan(m3_env.session, [("a.txt", "Security/CON.txt")])
    _, issues = validator.validate(plan)
    assert "DESTINATION_INVALID_NAME" in codes(issues)


def test_destination_parent_is_file(m3_env, validator):
    plan = make_plan(m3_env.session, [("a.txt", "a.txt/inner.txt")])
    _, issues = validator.validate(plan)
    assert "DESTINATION_PARENT_NOT_DIR" in codes(issues)


def test_swap_conflict_detected(m3_env, validator):
    """A→B 且 B→A：单看每项都合法，执行会相互覆盖。"""
    plan = make_plan(m3_env.session, [("a.txt", "b.txt"), ("b.txt", "a.txt")])
    _, issues = validator.validate(plan)
    assert "SWAP_CONFLICT" in codes(issues)


def test_low_confidence_is_warning_only(m3_env, validator):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")], confidence="low")
    _, issues = validator.validate(plan)
    assert "LOW_CONFIDENCE" in codes(issues)
    assert not has_errors(issues)


def test_too_many_operations(m3_env, validator):
    validator.config = replace(m3_env.config, max_plan_operations=1)
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt"), ("b.txt", "Out/b.txt")])
    _, issues = validator.validate(plan)
    assert "TOO_MANY_OPERATIONS" in codes(issues)


def test_edited_plan_changes_hash(m3_env):
    """人工编辑使 plan_hash 失效——不能拿审批 A 执行计划 B（§7.3）。"""
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    original_hash = compute_plan_hash(plan)
    edited = plan.model_copy(deep=True)
    edited.operations[0].destination = "Out2/a.txt"
    assert compute_plan_hash(edited) != original_hash
