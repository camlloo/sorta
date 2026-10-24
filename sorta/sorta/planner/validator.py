"""PlanValidator：执行前的确定性校验（开发文档 §7.2 十项）。

Validator 只报告问题，绝不悄悄"修复"不安全计划（§3.2）；
error 阻止执行，warning 需要用户查看。
"""

from __future__ import annotations

import os

from sorta.agent.schemas import ExecutionPlan
from sorta.config import SortaConfig
from sorta.errors import ScanSessionError
from sorta.planner.conflicts import (
    check_reserved_name,
    destination_depth,
    destination_length_warning,
    find_duplicate_destinations,
    find_duplicate_sources,
    same_path,
)
from sorta.planner.schemas import ValidationIssue
from sorta.scanner.paths import resolve_within_root
from sorta.scanner.registry import FileRegistry, ScanSession, VerificationStatus


class PlanValidator:
    def __init__(self, registry: FileRegistry, config: SortaConfig):
        self.registry = registry
        self.config = config

    def validate(self, plan: ExecutionPlan) -> tuple[ScanSession | None, list[ValidationIssue]]:
        """返回 (session, issues)。session 在会话有效时提供，供后续流程复用。"""
        issues: list[ValidationIssue] = []
        try:
            session = self.registry.get_session(plan.scan_id)
        except ScanSessionError as exc:
            return None, [
                ValidationIssue(
                    level="error", operation_index=None, code="SESSION_INVALID", message=str(exc)
                )
            ]

        # 第 9 条（部分）：操作条数上限
        if len(plan.operations) > self.config.max_plan_operations:
            issues.append(
                ValidationIssue(
                    level="error",
                    operation_index=None,
                    code="TOO_MANY_OPERATIONS",
                    message=(
                        f"操作条数 {len(plan.operations)} 超过上限 "
                        f"{self.config.max_plan_operations}"
                    ),
                )
            )

        seen_sources = [op.source_id for op in plan.operations]
        seen_destinations = [op.destination for op in plan.operations]
        dup_sources = find_duplicate_sources(seen_sources)
        dup_destinations = find_duplicate_destinations(seen_destinations)

        for index, op in enumerate(plan.operations):
            issues.extend(self._validate_operation(index, op, session))
            if op.source_id in dup_sources:
                issues.append(
                    ValidationIssue(
                        level="error",
                        operation_index=index,
                        code="DUPLICATE_SOURCE",
                        message=f"同一源文件 {op.source_id} 被安排了多个操作",
                    )
                )
            if os.path.normcase(op.destination) in dup_destinations:
                issues.append(
                    ValidationIssue(
                        level="error",
                        operation_index=index,
                        code="DUPLICATE_DESTINATION",
                        message=f"多个操作指向同一目标 {op.destination}",
                    )
                )

        # 跨操作：源与目标互换冲突（A→B 且 B→A 会相互覆盖）
        issues.extend(self._check_source_dest_swap(plan, session))
        return session, issues

    def _validate_operation(self, index: int, op, session: ScanSession) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        # 第 2 条：source_id 必须来自本次扫描
        record = session.get(op.source_id)
        if record is None:
            issues.append(
                ValidationIssue(
                    level="error",
                    operation_index=index,
                    code="UNKNOWN_SOURCE",
                    message=f"source_id {op.source_id!r} 不在本次扫描会话中",
                )
            )
            return issues
        if not record.is_regular_file:
            issues.append(
                ValidationIssue(
                    level="error",
                    operation_index=index,
                    code="SOURCE_NOT_REGULAR",
                    message=f"{record.relative_path} 不是普通文件",
                )
            )

        # 第 3 条：源与目标都必须能安全解析到授权范围内（含符号链接拒绝）
        try:
            source_abs = resolve_within_root(session.root, record.relative_path)
        except Exception as exc:
            issues.append(
                ValidationIssue(
                    level="error",
                    operation_index=index,
                    code="SOURCE_UNSAFE",
                    message=str(exc),
                )
            )
            return issues
        try:
            dest_abs = resolve_within_root(session.root, op.destination)
        except Exception as exc:
            issues.append(
                ValidationIssue(
                    level="error",
                    operation_index=index,
                    code="DESTINATION_UNSAFE",
                    message=str(exc),
                )
            )
            return issues

        # 第 4 条：源 == 目标；目录命名平台规则
        if same_path(str(source_abs), str(dest_abs)):
            issues.append(
                ValidationIssue(
                    level="error",
                    operation_index=index,
                    code="SOURCE_EQUALS_DESTINATION",
                    message="源路径与目标路径相同",
                )
            )
        for component in op.destination.split("/"):
            problem = check_reserved_name(component)
            if problem:
                issues.append(
                    ValidationIssue(
                        level="error",
                        operation_index=index,
                        code="DESTINATION_INVALID_NAME",
                        message=f"目标路径段 {component!r}: {problem}",
                    )
                )

        # 第 7 条：同名目标已存在 → 默认冲突，不自动覆盖
        if os.path.lexists(dest_abs):
            issues.append(
                ValidationIssue(
                    level="error",
                    operation_index=index,
                    code="DESTINATION_EXISTS",
                    message=f"目标已存在，不自动覆盖: {op.destination}",
                )
            )

        # 目标父级中存在文件占用（如 "a.txt/x.pdf" 中 a.txt 是文件）
        parent = dest_abs.parent
        while True:
            if os.path.lexists(parent) and not parent.is_dir():
                issues.append(
                    ValidationIssue(
                        level="error",
                        operation_index=index,
                        code="DESTINATION_PARENT_NOT_DIR",
                        message=f"目标父级被文件占用: {parent.name}",
                    )
                )
                break
            if parent == parent.parent:
                break
            parent = parent.parent

        # 第 9 条（部分）：目标目录深度与路径长度
        if destination_depth(op.destination) > 8:
            issues.append(
                ValidationIssue(
                    level="warning",
                    operation_index=index,
                    code="DESTINATION_TOO_DEEP",
                    message=f"目标目录深度 {destination_depth(op.destination)} 超过建议值 8",
                )
            )
        if destination_length_warning(str(session.root), op.destination):
            issues.append(
                ValidationIssue(
                    level="warning",
                    operation_index=index,
                    code="DESTINATION_TOO_LONG",
                    message="目标绝对路径接近 Windows MAX_PATH(260) 限制",
                )
            )

        # 第 8 条：文件是否在扫描后变化
        verification = self.registry.verify(session.scan_id, op.source_id)
        if verification.status is VerificationStatus.MODIFIED:
            issues.append(
                ValidationIssue(
                    level="error",
                    operation_index=index,
                    code="SOURCE_MODIFIED",
                    message=f"{record.relative_path} 在扫描后被修改，计划已过期（STALE）",
                )
            )
        elif verification.status is VerificationStatus.MISSING:
            issues.append(
                ValidationIssue(
                    level="error",
                    operation_index=index,
                    code="SOURCE_MISSING",
                    message=f"{record.relative_path} 在扫描后消失",
                )
            )

        # 第 10 条：低置信度操作需用户显式确认
        if op.confidence.value == "low":
            issues.append(
                ValidationIssue(
                    level="warning",
                    operation_index=index,
                    code="LOW_CONFIDENCE",
                    message=f"低置信度操作，请逐项确认: {record.relative_path} -> {op.destination}",
                )
            )
        return issues

    def _check_source_dest_swap(
        self, plan: ExecutionPlan, session: ScanSession
    ) -> list[ValidationIssue]:
        """A→B 且 B→A 的互换对：两个操作单独看都合法，执行会相互覆盖。"""
        issues: list[ValidationIssue] = []
        source_rel = {}
        for index, op in enumerate(plan.operations):
            record = session.get(op.source_id)
            if record is not None:
                source_rel[index] = record.relative_path
        for i in range(len(plan.operations)):
            for j in range(i + 1, len(plan.operations)):
                if i not in source_rel or j not in source_rel:
                    continue
                if same_path(source_rel[i], plan.operations[j].destination) and same_path(
                    source_rel[j], plan.operations[i].destination
                ):
                    for index in (i, j):
                        issues.append(
                            ValidationIssue(
                                level="error",
                                operation_index=index,
                                code="SWAP_CONFLICT",
                                message=f"操作 {i} 与 {j} 互为目标路径，会相互覆盖",
                            )
                        )
        return issues
