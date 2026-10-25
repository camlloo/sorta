"""计划编辑（F-08：用户可排除操作或调整目标路径）。

编辑产出全新计划对象：plan_hash 随之改变，既有审批自动失效（§4.2/§7.3），
执行前必须走完整审批流。目标路径的合法性由 FileOperation 校验器把关，
此处不做二次校验，非法输入会以 ValidationError 浮出。
"""

from __future__ import annotations

from sorta.agent.schemas import ExecutionPlan, FileOperation


def apply_edits(plan: ExecutionPlan, *, keep: list[bool], destinations: list[str]) -> ExecutionPlan:
    """按用户编辑重建计划。

    keep[i]=False 剔除第 i 项操作；destinations[i] 非空且与原值不同则覆盖。
    长度必须与操作数一致。
    """
    if len(keep) != len(plan.operations) or len(destinations) != len(plan.operations):
        raise ValueError("编辑列表长度与计划操作数不一致")

    operations: list[FileOperation] = []
    for index, op in enumerate(plan.operations):
        if not keep[index]:
            continue
        data = op.model_dump()
        new_destination = (destinations[index] or "").strip()
        if new_destination and new_destination != op.destination:
            if op.action.value == "rmdir":
                raise ValueError("空目录清理只能保留或排除，不能修改路径")
            data["destination"] = new_destination  # 非法路径在此触发 ValidationError
        operations.append(FileOperation.model_validate(data))
    return plan.model_copy(update={"operations": operations})


__all__ = ["apply_edits"]
