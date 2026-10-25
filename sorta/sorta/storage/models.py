"""SQLAlchemy ORM 表模型（开发文档 §9）。

只负责持久化；领域/接口层数据结构是各包的 Pydantic 模型，两者在
storage 层显式转换，ORM 对象不越过 storage 层。
"""

from __future__ import annotations

from sqlalchemy import JSON, BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ScanSessionRow(Base):
    """扫描授权范围与会话。"""

    __tablename__ = "scan_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # scan_id
    root_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(BigInteger)
    expires_at: Mapped[float] = mapped_column(BigInteger)


class FileRegistryRow(Base):
    """Agent 文件引用映射：file_id 只在所属扫描会话内有效。"""

    __tablename__ = "file_registry"
    __table_args__ = (UniqueConstraint("scan_id", "file_id", name="uq_file_registry_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("scan_sessions.id"), index=True, nullable=False
    )
    file_id: Mapped[str] = mapped_column(String(32), nullable=False)
    relative_path: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(String(32), default="")
    size: Mapped[int] = mapped_column(BigInteger)
    modified_ns: Mapped[int] = mapped_column(BigInteger)


class PlanRow(Base):
    """计划及版本。plan_hash 是规范化序列化的 SHA-256，审批的绑定锚点。

    version 从 1 起递增：每次人工编辑 +1 并生成新 plan_hash，旧审批随之失效
    （v2 桌面版 API 的乐观锁依据）。
    """

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("scan_sessions.id"), index=True, nullable=False
    )
    plan_json: Mapped[dict] = mapped_column(JSON)
    plan_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    created_at: Mapped[float] = mapped_column(BigInteger)


class ApprovalRow(Base):
    """明确的用户审批：精确绑定 plan_hash（§7.3）。"""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), index=True, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    approved_at: Mapped[float] = mapped_column(BigInteger)


class ExecutionRow(Base):
    """执行批次。id 由 (plan_hash, approval_id) 确定性派生，防止重复执行。"""

    __tablename__ = "executions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # execution_id
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), index=True, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running")
    started_at: Mapped[float] = mapped_column(BigInteger)
    finished_at: Mapped[float | None] = mapped_column(BigInteger, nullable=True)


class OperationLogRow(Base):
    """逐项执行及撤销依据（§8.2）。持久化时不得写入文件正文。"""

    __tablename__ = "operation_logs"
    __table_args__ = (
        UniqueConstraint("execution_id", "operation_index", name="uq_operation_index"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("executions.id"), index=True, nullable=False
    )
    operation_index: Mapped[int] = mapped_column(nullable=False)
    source_relpath: Mapped[str] = mapped_column(Text)
    destination_relpath: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    source_fingerprint_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    dest_fingerprint_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(BigInteger)
    updated_at: Mapped[float] = mapped_column(BigInteger)
