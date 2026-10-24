"""executor 包：计划校验后的安全执行、逐项日志、崩溃恢复与撤销。"""

from sorta.executor.engine import (
    ApprovalMismatchError,
    DuplicateExecutionError,
    ExecutionEngine,
    PlanStaleError,
    execution_id_for,
    recover_interrupted,
)
from sorta.executor.flows import PlanAlreadyExecutedError, approve_and_execute
from sorta.executor.schemas import ExecutionResult, OperationOutcome, UndoResult
from sorta.executor.undo import UndoEngine

__all__ = [
    "ApprovalMismatchError",
    "DuplicateExecutionError",
    "ExecutionEngine",
    "ExecutionResult",
    "OperationOutcome",
    "PlanAlreadyExecutedError",
    "PlanStaleError",
    "UndoEngine",
    "UndoResult",
    "approve_and_execute",
    "execution_id_for",
    "recover_interrupted",
]
