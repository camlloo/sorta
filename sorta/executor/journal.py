"""OperationJournal：逐项操作日志（开发文档 §8.1/§8.2）。

状态机：PENDING → RUNNING → SUCCEEDED / FAILED / NEEDS_RECOVERY；
SUCCEEDED → UNDO_REQUESTED → UNDONE / UNDO_CONFLICT。
每一步先落库再动文件，崩溃后可据此核对真实状态。
"""

from __future__ import annotations

from sorta.storage import OperationLogRecord, PlanStore


class OperationJournal:
    def __init__(self, store: PlanStore, execution_id: str):
        self.store = store
        self.execution_id = execution_id
        self._current: dict[int, OperationLogRecord] = {}

    def adopt(self, record: OperationLogRecord) -> None:
        """撤销/恢复流程：从持久化日志接管既有条目，继续状态迁移。"""
        self._current[record.operation_index] = record

    def mark_pending(
        self, *, index: int, source_relpath: str, destination_relpath: str, action: str
    ) -> None:
        record = OperationLogRecord(
            operation_index=index,
            source_relpath=source_relpath,
            destination_relpath=destination_relpath,
            action=action,
            status="pending",
        )
        self.store.upsert_operation(self.execution_id, record)
        self._current[index] = record

    def mark_running(self, index: int) -> None:
        self._update(index, status="running")

    def mark_succeeded(
        self,
        index: int,
        *,
        source_fingerprint_before: str | None,
        dest_fingerprint_after: str | None,
    ) -> None:
        self._update(
            index,
            status="succeeded",
            source_fingerprint_before=source_fingerprint_before,
            dest_fingerprint_after=dest_fingerprint_after,
        )

    def mark_failed(self, index: int, error: str) -> None:
        self._update(index, status="failed", error=error)

    def mark_undo_requested(self, index: int) -> None:
        self._update(index, status="undo_requested")

    def mark_undone(self, index: int) -> None:
        self._update(index, status="undone")

    def mark_undo_conflict(self, index: int, error: str) -> None:
        self._update(index, status="undo_conflict", error=error)

    def mark_needs_recovery(self, index: int, error: str | None = None) -> None:
        self._update(index, status="needs_recovery", error=error)

    def _update(
        self,
        index: int,
        *,
        status: str,
        error: str | None = None,
        source_fingerprint_before: str | None = None,
        dest_fingerprint_after: str | None = None,
    ) -> None:
        record = self._current.get(index)
        if record is None:
            raise RuntimeError(f"日志项 {index} 未初始化，状态机被破坏")
        record.status = status
        if error is not None:
            record.error = error
        if source_fingerprint_before is not None:
            record.source_fingerprint_before = source_fingerprint_before
        if dest_fingerprint_after is not None:
            record.dest_fingerprint_after = dest_fingerprint_after
        self.store.upsert_operation(self.execution_id, record)
