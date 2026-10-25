"""审批-执行流：CLI apply 与图形界面共用的标准路径（§4.1 第 6-8 步）。

计划落库 → 状态推进 → 保存审批 → 引擎执行。引擎内部还会做锁内重验，
此处不再重复校验逻辑。
"""

from __future__ import annotations

from sorta.agent.schemas import ExecutionPlan
from sorta.config import SortaConfig
from sorta.errors import SortaError
from sorta.executor.engine import ExecutionEngine
from sorta.executor.schemas import ExecutionResult
from sorta.planner.schemas import compute_plan_hash
from sorta.scanner.registry import FileRegistry
from sorta.scanner.scanner import Scanner
from sorta.storage import IllegalPlanTransition, PlanStore


class PlanAlreadyExecutedError(SortaError):
    """计划已执行完毕（终态），不能重复审批执行；请重新生成计划。"""


def approve_and_execute(
    plan: ExecutionPlan,
    *,
    registry: FileRegistry,
    scanner: Scanner,
    config: SortaConfig,
    store: PlanStore,
) -> ExecutionResult:
    plan_hash = compute_plan_hash(plan)
    plan_id = store.save_plan(plan.model_dump(mode="json"), plan_hash, status="draft")
    try:
        for status in ("validated", "awaiting_approval"):
            store.update_plan_status(plan_id, status)
    except IllegalPlanTransition as exc:
        raise PlanAlreadyExecutedError(
            f"该计划已处于终态（可能已执行过），请重新生成计划（plan_hash={plan_hash[:12]}…）"
        ) from exc
    approval = store.save_approval(plan_id, plan_hash)
    store.update_plan_status(plan_id, "approved")

    engine = ExecutionEngine(registry=registry, scanner=scanner, config=config, store=store)
    return engine.execute(plan, approval_id=approval.id)


__all__ = ["PlanAlreadyExecutedError", "approve_and_execute"]
