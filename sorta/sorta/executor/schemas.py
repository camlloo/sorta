"""Executor 领域模型：执行结果（开发文档 §5.1）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OperationOutcome(BaseModel):
    """单个操作的执行/撤销结果。"""

    operation_index: int
    source: str
    destination: str
    action: str
    status: str  # succeeded / failed（或撤销：undone / undo_conflict）
    error: str | None = None


class ExecutionResult(BaseModel):
    execution_id: str
    plan_hash: str
    status: str  # completed / partial_failed / failed
    started_at: datetime
    finished_at: datetime | None = None
    operations: list[OperationOutcome] = Field(default_factory=list)


class UndoResult(BaseModel):
    execution_id: str
    status: str  # undone / partially_undone
    operations: list[OperationOutcome] = Field(default_factory=list)
