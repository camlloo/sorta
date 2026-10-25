"""ExecutionEngine：按批准的计划串行执行（开发文档 §8.1）。

执行流程（每个操作）：
  锁内重验计划 → 日志 PENDING → 边界/冲突复检 → 日志 RUNNING →
  受控 move/copy → 指纹与结果落库（SUCCEEDED/FAILED）→ 批次状态汇总。
磁盘操作与 SQLite 写入不是原子事务，因此逐项留痕、崩溃可核对。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from sorta.agent.schemas import ExecutionPlan
from sorta.config import SortaConfig
from sorta.errors import (
    ApprovalMismatchError,
    DuplicateExecutionError,
    PlanStaleError,
    SortaError,
)
from sorta.executor.journal import OperationJournal
from sorta.executor.safety import (
    acquire_execution_lock,
    ensure_parent_dirs,
    fingerprints_match,
    metadata_fingerprint,
    safe_copy,
    safe_move,
)
from sorta.executor.schemas import ExecutionResult, OperationOutcome
from sorta.planner.schemas import compute_plan_hash
from sorta.planner.validator import PlanValidator
from sorta.scanner.registry import FileRegistry
from sorta.scanner.scanner import Scanner
from sorta.storage import IllegalPlanTransition, PlanStore


def execution_id_for(plan_hash: str, approval_id: int) -> str:
    """由审批内容确定性派生，重复执行同一审批会被主键约束拦下（§9）。"""
    digest = hashlib.sha256(f"{plan_hash}:{approval_id}".encode()).hexdigest()[:12]
    return f"exec_{digest}"


class ExecutionEngine:
    def __init__(
        self,
        *,
        registry: FileRegistry,
        scanner: Scanner,
        config: SortaConfig,
        store: PlanStore,
    ):
        self.registry = registry
        self.scanner = scanner
        self.config = config
        self.store = store
        self.validator = PlanValidator(registry, config)

    def execute(self, plan: ExecutionPlan, *, approval_id: int) -> ExecutionResult:
        plan_hash = compute_plan_hash(plan)
        started = datetime.now(UTC)

        with acquire_execution_lock():
            plan_row = self.store.get_plan_by_hash(plan_hash)
            if plan_row is None:
                raise ApprovalMismatchError(
                    "计划未入库或与审批记录不一致（plan_hash 不匹配），拒绝执行"
                )
            approval = self.store.get_approval(plan_hash)
            if approval is None:
                raise ApprovalMismatchError(
                    "找不到与该计划内容一致的审批记录；请先确认计划（plan_hash 不匹配）"
                )
            execution_id = execution_id_for(plan_hash, approval.id)
            if not self.store.create_execution(execution_id, plan_row.id, plan_hash):
                raise DuplicateExecutionError(
                    f"该审批已执行过（execution_id={execution_id}），拒绝重复执行"
                )

            try:
                self._revalidate_locked(plan, plan_hash)
            except PlanStaleError:
                self.store.update_execution(execution_id, "failed", finished_at=time.time())
                raise
            self.store.update_plan_status(plan_row.id, "executing")

            session = self.registry.get_session(plan.scan_id)
            journal = OperationJournal(self.store, execution_id)
            outcomes: list[OperationOutcome] = []
            failed = 0

            for index, op in enumerate(plan.operations):
                journal.mark_pending(
                    index=index,
                    source_relpath=self._relpath_of(session, op.source_id),
                    destination_relpath=op.destination,
                    action=op.action.value,
                )
                try:
                    outcome = self._execute_one(plan, session, index, op, journal)
                except SortaError as exc:
                    journal.mark_failed(index, str(exc))
                    failed += 1
                    outcome = OperationOutcome(
                        operation_index=index,
                        source=self._relpath_of(session, op.source_id),
                        destination=op.destination,
                        action=op.action.value,
                        status="failed",
                        error=str(exc),
                    )
                outcomes.append(outcome)

            batch_status = "completed"
            if failed == len(plan.operations) and failed > 0:
                batch_status = "failed"
            elif failed > 0:
                batch_status = "partial_failed"

            finished = datetime.now(UTC)
            self.store.update_execution(
                execution_id, batch_status, finished_at=finished.timestamp()
            )
            self.store.update_plan_status(plan_row.id, batch_status)

            return ExecutionResult(
                execution_id=execution_id,
                plan_hash=plan_hash,
                status=batch_status,
                started_at=started,
                finished_at=finished,
                operations=outcomes,
            )

    # ---- 内部 -------------------------------------------------------------

    def _revalidate_locked(self, plan: ExecutionPlan, plan_hash: str) -> None:
        """锁内重验：审批绑定 + 文件状态（防止扫描/审批后文件变化，§4.1 第 8 步）。

        DESTINATION_EXISTS 例外：目标冲突由 §8.1 第 4 步在逐操作时检测并
        单项失败（不覆盖、其余继续），不作为整批 STALE 的依据。
        """
        approval = self.store.get_approval(plan_hash)
        if approval is None:
            raise ApprovalMismatchError(
                "找不到与该计划内容一致的审批记录；请先确认计划（plan_hash 不匹配）"
            )
        _, issues = self.validator.validate(plan)
        stale_issues = [i for i in issues if i.level == "error" and i.code != "DESTINATION_EXISTS"]
        if stale_issues:
            plan_row = self.store.get_plan_by_hash(plan_hash)
            if plan_row is not None:
                # 计划已在终态（如已执行完成）时不再改状态
                with contextlib.suppress(IllegalPlanTransition):
                    self.store.update_plan_status(plan_row.id, "stale")
            details = "; ".join(f"[{i.code}] {i.message}" for i in stale_issues)
            raise PlanStaleError(f"执行前重验失败，计划已置为 STALE: {details}")

    def _execute_one(self, plan: ExecutionPlan, session, index: int, op, journal):
        journal.mark_running(index)
        record, source_abs = self.registry.resolve_path(plan.scan_id, op.source_id)

        source_fingerprint = metadata_fingerprint(source_abs)
        if source_fingerprint is None:
            raise SortaError(f"源文件已消失: {record.relative_path}")
        if not fingerprints_match(source_abs, f"{record.size}:{record.modified_ns}"):
            raise SortaError(f"源文件在扫描后发生变化: {record.relative_path}")

        dest_abs = ensure_parent_dirs(session.root, op.destination)
        if op.action.value == "move":
            safe_move(source_abs, dest_abs)
        else:
            safe_copy(source_abs, dest_abs)

        dest_fingerprint = metadata_fingerprint(dest_abs)
        journal.mark_succeeded(
            index,
            source_fingerprint_before=source_fingerprint,
            dest_fingerprint_after=dest_fingerprint,
        )
        return OperationOutcome(
            operation_index=index,
            source=record.relative_path,
            destination=op.destination,
            action=op.action.value,
            status="succeeded",
        )

    @staticmethod
    def _relpath_of(session, source_id: str) -> str:
        record = session.get(source_id)
        return record.relative_path if record else f"<unknown:{source_id}>"


def recover_interrupted(store: PlanStore) -> list[tuple[str, int, str]]:
    """崩溃恢复：用真实文件状态核对 RUNNING/NEEDS_RECOVERY 日志（§8.3）。

    只核对与补记，不盲目重放。返回 [(execution_id, operation_index, 处理结果)]。
    """
    from sorta.scanner.paths import resolve_within_root

    recovered: list[tuple[str, int, str]] = []
    for record in store.list_operations_by_status(["running", "needs_recovery"]):
        eid = record.execution_id
        try:
            root = Path(store.get_root_for_execution(eid))
        except SortaError as exc:
            recovered.append((eid, record.operation_index, f"无法定位授权根目录: {exc}"))
            continue
        try:
            source_abs = resolve_within_root(root, record.source_relpath)
            dest_abs = resolve_within_root(root, record.destination_relpath)
        except Exception as exc:  # 路径被替换/越界：保持原状态交人工核对
            recovered.append((eid, record.operation_index, f"路径解析失败: {exc}"))
            continue
        source_gone = not os.path.lexists(source_abs)
        dest_exists = os.path.lexists(dest_abs)
        if record.status == "running" and source_gone and dest_exists:
            if fingerprints_match(dest_abs, record.dest_fingerprint_after):
                record.status = "succeeded"
                store.upsert_operation(eid, record)
                recovered.append((eid, record.operation_index, "实际已成功，补记 SUCCEEDED"))
            else:
                record.status = "needs_recovery"
                store.upsert_operation(eid, record)
                recovered.append((eid, record.operation_index, "目标指纹不符，保持 NEEDS_RECOVERY"))
        elif record.status == "running" and not source_gone and not dest_exists:
            record.status = "failed"
            record.error = "执行中断：源未动、目标未生成"
            store.upsert_operation(eid, record)
            recovered.append((eid, record.operation_index, "执行中断，标记 FAILED"))
        else:
            recovered.append((eid, record.operation_index, "状态不明（需人工核对），保持原状态"))
    return recovered
