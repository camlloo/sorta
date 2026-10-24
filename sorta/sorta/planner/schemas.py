"""Planner 领域模型：校验问题与计划哈希（开发文档 §5.1 / §7.3）。"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel

from sorta.agent.schemas import ExecutionPlan


class ValidationIssue(BaseModel):
    """一项校验结果。level=error 阻止执行，warning 需要用户查看（§7.2）。"""

    level: str  # error / warning
    operation_index: int | None
    code: str
    message: str


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(issue.level == "error" for issue in issues)


def compute_plan_hash(plan: ExecutionPlan) -> str:
    """规范化序列化后取 SHA-256（§7.3 审批绑定）。

    任何人工编辑都会改变 hash，使既有审批失效——不能拿审批 A 执行计划 B。
    """
    canonical = json.dumps(
        plan.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
