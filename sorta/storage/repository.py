"""PlanStore：持久化仓储的唯一入口（开发文档 §9）。

入参/出参一律是普通 dataclass 或领域 Pydantic 模型的字段，
SQLAlchemy ORM 对象不越过本层（§3.2 storage 职责边界）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from sorta.errors import (
    IllegalPlanTransition,
    PlanNotFoundError,
    PlanVersionConflictError,
    ScanSessionError,
    SortaError,
)
from sorta.locking import acquire_execution_lock
from sorta.models import FileRecord
from sorta.scanner.registry import ScanSession
from sorta.storage.models import (
    ApprovalRow,
    ExecutionRow,
    FileRegistryRow,
    OperationLogRow,
    PlanRow,
    ScanSessionRow,
)

# 计划状态机（开发文档 §4.2）
PLAN_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"validated", "rejected"},
    "validated": {"awaiting_approval", "rejected"},
    "awaiting_approval": {"approved", "rejected"},
    "approved": {"executing", "stale"},
    "executing": {"completed", "partial_failed", "failed"},
    "completed": {"undo_requested"},
    "partial_failed": {"undo_requested"},
    "undo_requested": {"undone", "partially_undone"},
    "stale": {"awaiting_approval"},
}


@dataclass(frozen=True)
class PlanRecord:
    id: int
    scan_id: str
    plan_json: dict[str, Any]
    plan_hash: str
    version: int
    status: str
    created_at: float


@dataclass(frozen=True)
class ApprovalRecord:
    id: int
    plan_id: int
    plan_hash: str
    approved_at: float


@dataclass(frozen=True)
class ExecutionRecord:
    id: str
    plan_id: int
    plan_hash: str
    status: str
    started_at: float
    finished_at: float | None


@dataclass
class OperationLogRecord:
    execution_id: str = ""
    operation_index: int = 0
    source_relpath: str = ""
    destination_relpath: str = ""
    action: str = ""
    status: str = "pending"
    source_fingerprint_before: str | None = None
    dest_fingerprint_after: str | None = None
    error: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0


class PlanStore:
    """所有 SQLite 访问的唯一入口。"""

    def __init__(self, engine: Any):
        self._engine = engine

    @property
    def engine(self) -> Any:
        """底层 SQLAlchemy Engine（供应用层需要独立会话时使用）。"""
        return self._engine

    # ---- 扫描会话持久化 ---------------------------------------------------

    def save_session(self, session: ScanSession) -> None:
        """会话与文件注册表整体写入（先清后插，保持与内存态一致）。"""
        with Session(self._engine) as db, db.begin():
            db.merge(
                ScanSessionRow(
                    id=session.scan_id,
                    root_path=str(session.root),
                    created_at=session.created_at,
                    expires_at=session.expires_at,
                )
            )
            db.execute(delete(FileRegistryRow).where(FileRegistryRow.scan_id == session.scan_id))
            for record in session.files.values():
                db.add(
                    FileRegistryRow(
                        scan_id=session.scan_id,
                        file_id=record.id,
                        relative_path=record.relative_path,
                        extension=record.extension,
                        size=record.size,
                        modified_ns=record.modified_ns,
                        is_regular_file=record.is_regular_file,
                    )
                )

    def load_session(self, scan_id: str, *, allow_expired: bool = False) -> ScanSession:
        """重建内存会话；不存在或已过期抛 ScanSessionError。"""
        with Session(self._engine) as db:
            row = db.get(ScanSessionRow, scan_id)
            if row is None:
                raise ScanSessionError(f"扫描会话不存在: {scan_id!r}")
            if not allow_expired and time.time() > row.expires_at:
                raise ScanSessionError(f"扫描会话已过期: {scan_id!r}，请重新扫描")
            records = db.scalars(
                select(FileRegistryRow).where(FileRegistryRow.scan_id == scan_id)
            ).all()
            files = [
                FileRecord(
                    id=r.file_id,
                    scan_id=scan_id,
                    relative_path=r.relative_path,
                    extension=r.extension,
                    size=r.size,
                    modified_ns=r.modified_ns,
                    is_regular_file=r.is_regular_file,
                )
                for r in records
            ]
            return ScanSession.from_records(
                scan_id=scan_id,
                root=row.root_path,
                created_at=row.created_at,
                expires_at=row.expires_at,
                records=files,
            )

    def get_session_root(self, scan_id: str) -> str:
        """撤销场景使用：只取授权根目录，不受会话有效期限制。"""
        with Session(self._engine) as db:
            row = db.get(ScanSessionRow, scan_id)
            if row is None:
                raise ScanSessionError(f"扫描会话不存在: {scan_id!r}")
            return row.root_path

    # ---- 计划 -------------------------------------------------------------

    def save_plan(
        self, plan_json: dict[str, Any], plan_hash: str, status: str, *, version: int = 1
    ) -> int:
        """按 hash 幂等保存，返回 plan_id。"""
        with Session(self._engine) as db, db.begin():
            existing = db.scalar(select(PlanRow).where(PlanRow.plan_hash == plan_hash))
            if existing is not None:
                return existing.id
            row = PlanRow(
                scan_id=plan_json["scan_id"],
                plan_json=plan_json,
                plan_hash=plan_hash,
                version=version,
                status=status,
                created_at=time.time(),
            )
            db.add(row)
            db.flush()
            return row.id

    def update_plan_content(
        self,
        plan_id: int,
        *,
        expected_version: int,
        plan_json: dict[str, Any],
        new_plan_hash: str,
    ) -> int:
        """人工编辑计划：乐观锁校验版本，覆盖内容并 version+1（旧审批随之失效）。

        返回新版本号。new_plan_hash 若与其他计划行冲突则拒绝（内容完全相同的计划）。
        """
        with acquire_execution_lock(), Session(self._engine) as db, db.begin():
            row = db.get(PlanRow, plan_id)
            if row is None:
                raise PlanNotFoundError(f"计划不存在: plan_id={plan_id}")
            if row.version != expected_version:
                raise PlanVersionConflictError(
                    f"计划版本已变化（当前 v{row.version}，请求基于 v{expected_version}）",
                    current_version=row.version,
                )
            if row.status not in {"draft", "validated", "awaiting_approval", "approved", "stale"}:
                raise IllegalPlanTransition(f"计划状态为 {row.status!r}，不能继续编辑")
            clash = db.scalar(
                select(PlanRow).where(PlanRow.plan_hash == new_plan_hash, PlanRow.id != plan_id)
            )
            if clash is not None:
                raise SortaError("编辑后的计划与另一份既有计划内容相同，拒绝覆盖")
            changed = db.execute(
                update(PlanRow)
                .where(PlanRow.id == plan_id, PlanRow.version == expected_version)
                .values(
                    plan_json=plan_json,
                    plan_hash=new_plan_hash,
                    version=expected_version + 1,
                    status="draft",
                )
            )
            if changed.rowcount != 1:
                raise PlanVersionConflictError(
                    "计划版本已变化，请刷新后重试", current_version=expected_version + 1
                )
            # 即使修改未改变 hash，旧审批也必须失效。
            db.execute(delete(ApprovalRow).where(ApprovalRow.plan_id == plan_id))
            return expected_version + 1

    def get_plan_by_hash(self, plan_hash: str) -> PlanRecord | None:
        with Session(self._engine) as db:
            row = db.scalar(select(PlanRow).where(PlanRow.plan_hash == plan_hash))
            return self._to_plan_record(row) if row else None

    def update_plan_status(self, plan_id: int, new_status: str) -> None:
        with Session(self._engine) as db, db.begin():
            row = db.get(PlanRow, plan_id)
            if row is None:
                raise PlanNotFoundError(f"计划不存在: plan_id={plan_id}")
            allowed = PLAN_TRANSITIONS.get(row.status, set())
            if new_status != row.status and new_status not in allowed:
                raise IllegalPlanTransition(
                    f"计划状态不允许从 {row.status!r} 迁移到 {new_status!r}（§4.2 状态机）"
                )
            row.status = new_status

    # ---- 审批 -------------------------------------------------------------

    def save_approval(self, plan_id: int, plan_hash: str) -> ApprovalRecord:
        with Session(self._engine) as db, db.begin():
            row = ApprovalRow(plan_id=plan_id, plan_hash=plan_hash, approved_at=time.time())
            db.add(row)
            db.flush()
            return ApprovalRecord(
                id=row.id, plan_id=plan_id, plan_hash=plan_hash, approved_at=row.approved_at
            )

    def get_approval_by_id(self, approval_id: int) -> ApprovalRecord | None:
        with Session(self._engine) as db:
            row = db.get(ApprovalRow, approval_id)
            if row is None:
                return None
            return ApprovalRecord(
                id=row.id, plan_id=row.plan_id, plan_hash=row.plan_hash, approved_at=row.approved_at
            )

    def get_approval(self, plan_hash: str) -> ApprovalRecord | None:
        with Session(self._engine) as db:
            row = db.scalar(
                select(ApprovalRow)
                .where(ApprovalRow.plan_hash == plan_hash)
                .order_by(ApprovalRow.id.desc())
            )
            if row is None:
                return None
            return ApprovalRecord(
                id=row.id, plan_id=row.plan_id, plan_hash=row.plan_hash, approved_at=row.approved_at
            )

    # ---- 执行批次 ----------------------------------------------------------

    def create_execution(self, execution_id: str, plan_id: int, plan_hash: str) -> bool:
        """幂等创建：同一审批重复执行返回 False（防确认按钮重复点击，§9）。"""
        with Session(self._engine) as db, db.begin():
            if db.get(ExecutionRow, execution_id) is not None:
                return False
            db.add(
                ExecutionRow(
                    id=execution_id,
                    plan_id=plan_id,
                    plan_hash=plan_hash,
                    status="running",
                    started_at=time.time(),
                )
            )
            return True

    def update_execution(
        self, execution_id: str, status: str, finished_at: float | None = None
    ) -> None:
        with Session(self._engine) as db, db.begin():
            row = db.get(ExecutionRow, execution_id)
            if row is None:
                raise SortaError(f"执行批次不存在: {execution_id!r}")
            row.status = status
            if finished_at is not None:
                row.finished_at = finished_at

    def get_execution(self, execution_id: str) -> ExecutionRecord | None:
        with Session(self._engine) as db:
            row = db.get(ExecutionRow, execution_id)
            if row is None:
                return None
            return ExecutionRecord(
                id=row.id,
                plan_id=row.plan_id,
                plan_hash=row.plan_hash,
                status=row.status,
                started_at=row.started_at,
                finished_at=row.finished_at,
            )

    def list_executions(self) -> list[ExecutionRecord]:
        with Session(self._engine) as db:
            rows = db.scalars(select(ExecutionRow).order_by(ExecutionRow.started_at.desc())).all()
            return [
                ExecutionRecord(
                    id=r.id,
                    plan_id=r.plan_id,
                    plan_hash=r.plan_hash,
                    status=r.status,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                )
                for r in rows
            ]

    # ---- 操作日志 ----------------------------------------------------------

    def upsert_operation(self, execution_id: str, record: OperationLogRecord) -> None:
        now = time.time()
        with Session(self._engine) as db, db.begin():
            row = db.scalar(
                select(OperationLogRow).where(
                    OperationLogRow.execution_id == execution_id,
                    OperationLogRow.operation_index == record.operation_index,
                )
            )
            if row is None:
                row = OperationLogRow(
                    execution_id=execution_id,
                    operation_index=record.operation_index,
                    source_relpath=record.source_relpath,
                    destination_relpath=record.destination_relpath,
                    action=record.action,
                    status=record.status,
                    source_fingerprint_before=record.source_fingerprint_before,
                    dest_fingerprint_after=record.dest_fingerprint_after,
                    error=record.error,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.status = record.status
                row.source_fingerprint_before = record.source_fingerprint_before
                row.dest_fingerprint_after = record.dest_fingerprint_after
                row.error = record.error
                row.updated_at = now

    def get_root_for_execution(self, execution_id: str) -> str:
        """撤销/恢复场景：execution -> plan -> session 授权根目录（不受 TTL 限制）。"""
        with Session(self._engine) as db:
            row = db.get(ExecutionRow, execution_id)
            if row is None:
                raise SortaError(f"执行批次不存在: {execution_id!r}")
            plan = db.get(PlanRow, row.plan_id)
            if plan is None:
                raise SortaError(f"执行批次 {execution_id!r} 关联的计划不存在")
            session = db.get(ScanSessionRow, plan.scan_id)
            if session is None:
                raise SortaError(f"计划关联的扫描会话不存在: {plan.scan_id!r}")
            return session.root_path

    def list_operations(self, execution_id: str) -> list[OperationLogRecord]:
        with Session(self._engine) as db:
            rows = db.scalars(
                select(OperationLogRow)
                .where(OperationLogRow.execution_id == execution_id)
                .order_by(OperationLogRow.operation_index)
            ).all()
            return [self._to_operation_record(r) for r in rows]

    def list_operations_by_status(self, statuses: list[str]) -> list[OperationLogRecord]:
        with Session(self._engine) as db:
            rows = db.scalars(
                select(OperationLogRow).where(OperationLogRow.status.in_(statuses))
            ).all()
            return [self._to_operation_record(r) for r in rows]

    def get_plan(self, plan_id: int) -> PlanRecord | None:
        with Session(self._engine) as db:
            row = db.get(PlanRow, plan_id)
            return self._to_plan_record(row) if row else None

    @staticmethod
    def _to_plan_record(row: PlanRow) -> PlanRecord:
        return PlanRecord(
            id=row.id,
            scan_id=row.scan_id,
            plan_json=row.plan_json,
            plan_hash=row.plan_hash,
            version=row.version,
            status=row.status,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_operation_record(row: OperationLogRow) -> OperationLogRecord:
        return OperationLogRecord(
            execution_id=row.execution_id,
            operation_index=row.operation_index,
            source_relpath=row.source_relpath,
            destination_relpath=row.destination_relpath,
            action=row.action,
            status=row.status,
            source_fingerprint_before=row.source_fingerprint_before,
            dest_fingerprint_after=row.dest_fingerprint_after,
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
