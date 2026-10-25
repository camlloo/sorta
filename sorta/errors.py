"""统一异常层次：调用方只需捕获 SortaError 即可区分业务失败与程序缺陷。

code 是稳定的机器可读错误码（v2 桌面版 API §4.5），前端据此决定反馈方式；
中文 message 仍用于 CLI 与日志。
"""

from __future__ import annotations


class SortaError(Exception):
    """SORTA 业务异常基类。code 为稳定错误码，details 可携带结构化上下文。"""

    code = "SORTA_ERROR"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class PathSafetyError(SortaError, ValueError):
    """路径越界、绝对路径、非法路径段等安全问题（开发文档 §7.2 / §10.2）。"""

    code = "PATH_UNSAFE"


class ScanError(SortaError, ValueError):
    """扫描参数非法或根目录不可用。"""

    code = "SCAN_INVALID"


class ScanSessionError(SortaError, RuntimeError):
    """scan_id 不存在、已过期或与当前授权根目录不符。"""

    code = "SCAN_EXPIRED"


class UnknownFileError(SortaError, ValueError):
    """file_id 未在当前扫描会话中注册。"""

    code = "FILE_UNKNOWN"


class WorkspaceNotAuthorizedError(SortaError, RuntimeError):
    """尚未选择或无权访问工作目录（v2 API §4.5）。"""

    code = "WORKSPACE_NOT_AUTHORIZED"


class PlanNotFoundError(SortaError, LookupError):
    """计划不存在。"""

    code = "PLAN_NOT_FOUND"


class PlanVersionConflictError(SortaError, RuntimeError):
    """计划版本与请求携带的 expected_version 不一致（乐观锁）。"""

    code = "PLAN_VERSION_CONFLICT"

    def __init__(self, message: str, *, current_version: int, details: dict | None = None):
        merged = {"current_version": current_version, **(details or {})}
        super().__init__(message, details=merged)
        self.current_version = current_version


class PlanStaleError(SortaError):
    """执行前重验发现 error 级问题，计划进入 STALE。"""

    code = "PLAN_STALE"


class ApprovalMismatchError(SortaError):
    """计划内容与审批记录的 plan_hash 不一致（§7.3）。"""

    code = "APPROVAL_REQUIRED"


class DuplicateExecutionError(SortaError):
    """同一审批已被执行过（确定性 execution_id 已存在）。"""

    code = "EXECUTION_IN_PROGRESS"


class FileConflictError(SortaError):
    """目标已存在，拒绝覆盖（§10.3 安全不变量）。"""

    code = "PLAN_CONFLICT"


class CrossDeviceMoveError(SortaError):
    """跨文件系统移动，MVP 不自动降级（§8.1）。"""

    code = "CROSS_DEVICE_MOVE"


class UndoConflictError(SortaError):
    """撤销目标发生变化，保留现场（§8.3）。"""

    code = "UNDO_CONFLICT"


class ConsentRequiredError(SortaError):
    """需要用户授权读取文件正文（v2 改进文档 UX-03）。"""

    code = "CONSENT_REQUIRED"


class ModelUnavailableError(SortaError):
    """模型接口不可用或未配置（v2 API §4.5）。"""

    code = "MODEL_UNAVAILABLE"


class OperationCanceledError(SortaError):
    """长耗时任务被用户取消（v2 API §4.4：停止接下来的工作）。"""

    code = "OPERATION_CANCELED"


class IllegalPlanTransition(SortaError):
    """计划状态机不允许的迁移。"""

    code = "PLAN_INVALID_TRANSITION"
