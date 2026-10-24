"""UndoEngine：条件满足时的尽力恢复（开发文档 §8.3）。

- 撤销 move：目标仍存在且未被修改、原路径未被占用 → 移回原位
- 撤销 copy：仅删除本次执行创建且后来未被修改的目标副本
- 冲突：停止该项撤销并报告，绝不覆盖已有内容
- 撤销顺序：按操作序号倒序（LIFO）
"""

from __future__ import annotations

import os
from pathlib import Path

from sorta.errors import SortaError
from sorta.executor.journal import OperationJournal
from sorta.executor.safety import fingerprints_match
from sorta.executor.schemas import OperationOutcome, UndoResult
from sorta.scanner.paths import resolve_within_root
from sorta.storage import OperationLogRecord, PlanStore

_UNDOABLE_EXECUTION_STATUS = ("completed", "partial_failed")


class UndoEngine:
    def __init__(self, *, store: PlanStore):
        self.store = store

    def undo_execution(self, execution_id: str) -> UndoResult:
        execution = self.store.get_execution(execution_id)
        if execution is None:
            raise SortaError(f"执行批次不存在: {execution_id!r}")
        if execution.status not in _UNDOABLE_EXECUTION_STATUS:
            raise SortaError(
                f"批次状态为 {execution.status!r}，只有 completed/partial_failed 可撤销"
            )
        records = [r for r in self.store.list_operations(execution_id) if r.status == "succeeded"]
        if not records:
            raise SortaError("该批次没有可撤销的成功操作")
        plan_row = self.store.get_plan(execution.plan_id)
        if plan_row is None:
            raise SortaError(f"批次关联的计划不存在: plan_id={execution.plan_id}")
        root = Path(self.store.get_root_for_execution(execution_id))

        self.store.update_plan_status(plan_row.id, "undo_requested")
        journal = OperationJournal(self.store, execution_id)
        outcomes: list[OperationOutcome] = []
        conflicts = 0
        for record in reversed(records):  # LIFO
            journal.adopt(record)
            journal.mark_undo_requested(record.operation_index)
            try:
                self._undo_one(root, record)
                journal.mark_undone(record.operation_index)
                outcomes.append(
                    OperationOutcome(
                        operation_index=record.operation_index,
                        source=record.source_relpath,
                        destination=record.destination_relpath,
                        action=record.action,
                        status="undone",
                    )
                )
            except SortaError as exc:
                journal.mark_undo_conflict(record.operation_index, str(exc))
                conflicts += 1
                outcomes.append(
                    OperationOutcome(
                        operation_index=record.operation_index,
                        source=record.source_relpath,
                        destination=record.destination_relpath,
                        action=record.action,
                        status="undo_conflict",
                        error=str(exc),
                    )
                )
        batch_status = "undone" if conflicts == 0 else "partially_undone"
        self.store.update_plan_status(plan_row.id, batch_status)
        return UndoResult(execution_id=execution_id, status=batch_status, operations=outcomes)

    def _undo_one(self, root: Path, record: OperationLogRecord) -> None:
        dest_abs = resolve_within_root(root, record.destination_relpath)
        source_abs = resolve_within_root(root, record.source_relpath)
        if not fingerprints_match(dest_abs, record.dest_fingerprint_after):
            raise SortaError(
                f"目标 {record.destination_relpath} 在执行后被修改，拒绝自动撤销（保留现场）"
            )
        if record.action == "move":
            if os.path.lexists(source_abs):
                raise SortaError(f"原路径已被占用: {record.source_relpath}，拒绝覆盖")
            try:
                os.rename(dest_abs, source_abs)
            except OSError as exc:
                raise SortaError(f"移回原位失败: {exc}") from exc
        elif record.action == "copy":
            try:
                os.remove(dest_abs)  # 仅删除本次创建且未被修改的副本（§8.3）
            except OSError as exc:
                raise SortaError(f"删除副本失败: {exc}") from exc
        else:
            raise SortaError(f"未知操作类型: {record.action!r}")
