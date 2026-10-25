"""Job 管理器：长耗时任务（计划生成）的进程内执行与轮询（v2 API §4.4）。

状态机 queued → running → completed / failed / canceled；
取消 = 置位 cancel_event，在下一个安全停点（模型调用前）生效。
不引入 Celery/Redis：单用户本地应用，线程池足够。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued / running / completed / failed / canceled
    result: Any = None
    error: dict[str, Any] | None = None
    tool_events: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "tool_events": list(self.tool_events),
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class JobManager:
    def __init__(self, max_workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sorta-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[str, threading.Event], Any]) -> str:
        """提交任务。fn 接收 (job_id, cancel_event)，返回结果或抛异常。

        job_id 提前生成，fn 内部可用它写工具事件（on_tool_record → add_event）。
        """
        job = Job(id=f"job_{uuid.uuid4().hex[:12]}", kind=kind)
        with self._lock:
            self._jobs[job.id] = job

        def _run() -> None:
            with self._lock:
                if job.cancel_event.is_set():
                    return
                job.status = "running"
            try:
                result = fn(job.id, job.cancel_event)
                with self._lock:
                    job.result = result
                    job.status = "completed"
                    job.finished_at = time.time()
            except Exception as exc:  # noqa: BLE001  Job 边界：异常转为失败状态
                with self._lock:
                    if job.cancel_event.is_set():
                        job.status = "canceled"
                        job.error = {"code": "OPERATION_CANCELED", "message": str(exc)}
                    else:
                        job.status = "failed"
                        code = getattr(exc, "code", "INTERNAL_ERROR")
                        job.error = {"code": code, "message": str(exc)}
                    job.finished_at = time.time()

        self._pool.submit(_run)
        return job.id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job else None

    def add_event(self, job_id: str, event: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.tool_events.append(event)

    def cancel(self, job_id: str) -> bool:
        """queued 直接取消；running 置位取消事件，在下一个安全停点生效。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in ("completed", "failed", "canceled"):
                return False
            job.cancel_event.set()
            if job.status == "queued":
                job.status = "canceled"
                job.finished_at = time.time()
                job.error = {"code": "OPERATION_CANCELED", "message": "任务在开始前被取消"}
            return True
