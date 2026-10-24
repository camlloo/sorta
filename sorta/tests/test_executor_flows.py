"""executor.flows 测试：approve_and_execute 共享审批执行流。"""

from __future__ import annotations

import pytest
from conftest import make_plan

from sorta.executor import PlanAlreadyExecutedError
from sorta.executor.flows import approve_and_execute
from sorta.scanner import Scanner


def _execute(m3_env, plan):
    return approve_and_execute(
        plan,
        registry=m3_env.registry,
        scanner=Scanner(m3_env.registry, m3_env.config),
        config=m3_env.config,
        store=m3_env.store,
    )


def test_approve_and_execute_happy_path(m3_env):
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    result = _execute(m3_env, plan)
    assert result.status == "completed"
    assert (m3_env.root / "Out" / "a.txt").exists()


def test_reapply_executed_plan_rejected(m3_env):
    """计划执行后处于终态，再次审批执行必须被拒绝（提示重新生成计划）。"""
    plan = make_plan(m3_env.session, [("a.txt", "Out/a.txt")])
    _execute(m3_env, plan)
    with pytest.raises(PlanAlreadyExecutedError, match="重新生成"):
        _execute(m3_env, plan)
