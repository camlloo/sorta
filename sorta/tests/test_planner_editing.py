"""planner.editing 测试（F-08：单项排除与目标路径修改）。"""

from __future__ import annotations

import pytest
from conftest import make_plan
from pydantic import ValidationError

from sorta.planner import compute_plan_hash
from sorta.planner.editing import apply_edits


def test_apply_edits_drops_unchecked(m3_env):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt"), ("b.txt", "Out/b.txt")])
    edited = apply_edits(plan, keep=[True, False], destinations=["", ""])
    assert len(edited.operations) == 1
    assert edited.operations[0].destination == "Out/a.txt"
    assert edited.scan_id == plan.scan_id and edited.summary == plan.summary


def test_apply_edits_overrides_destination(m3_env):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    edited = apply_edits(plan, keep=[True], destinations=["NewDir/renamed.txt"])
    assert edited.operations[0].destination == "NewDir/renamed.txt"


def test_apply_edits_keeps_same_destination_untouched(m3_env):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    edited = apply_edits(plan, keep=[True], destinations=["Out/a.txt"])
    assert edited.operations[0] == plan.operations[0]


def test_edited_plan_hash_differs(m3_env):
    """人工编辑使 plan_hash 失效——既有审批不能用于编辑后的计划（§7.3）。"""
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    edited = apply_edits(plan, keep=[True], destinations=["Out2/a.txt"])
    assert compute_plan_hash(edited) != compute_plan_hash(plan)


def test_apply_edits_rejects_unsafe_destination(m3_env):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    with pytest.raises(ValidationError):
        apply_edits(plan, keep=[True], destinations=["../escape.txt"])
    with pytest.raises(ValidationError):
        apply_edits(plan, keep=[True], destinations=["C:/win.txt"])


def test_apply_edits_length_mismatch(m3_env):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    with pytest.raises(ValueError, match="长度"):
        apply_edits(plan, keep=[], destinations=[])
