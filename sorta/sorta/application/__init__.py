"""application 包：跨界面业务服务层（CLI / API / 桌面端共用，v2 文档 §3.1）。"""

from sorta.application.jobs import Job, JobManager
from sorta.application.service import PlanDetail, SortaService, WorkspaceInfo

__all__ = ["Job", "JobManager", "PlanDetail", "SortaService", "WorkspaceInfo"]
