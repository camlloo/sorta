"""Agent 输出的整理计划模型（开发文档 §5.1 / §7.1）。

这是领域与接口层的 Pydantic 模型；结构校验在此完成（F-06"结构化输出通过
业务验证"的第一层），跨文件冲突、路径合法性等完整校验由 M3 的 PlanValidator 负责。
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath

from pydantic import BaseModel, Field, field_validator

_DRIVE_PART = re.compile(r"^[A-Za-z]:$")


class FileAction(StrEnum):
    MOVE = "move"
    COPY = "copy"
    RMDIR = "rmdir"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FileOperation(BaseModel):
    """对单个文件的一项操作。destination 恒为相对根目录的 posix 风格路径。"""

    source_id: str
    destination: str
    action: FileAction
    reason: str = Field(min_length=1)
    confidence: Confidence

    @field_validator("source_id")
    @classmethod
    def _source_id_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("source_id 不能为空")
        return v

    @field_validator("reason")
    @classmethod
    def _reason_meaningful(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("reason 不能为空白，必须给出判断依据")
        return v

    @field_validator("destination")
    @classmethod
    def _destination_safe_relative(cls, v: str) -> str:
        raw = v.strip()
        if not raw:
            raise ValueError("destination 不能为空")
        if "\\" in raw:
            raise ValueError("destination 必须使用正斜杠 / 分隔，不允许反斜杠")
        p = PurePosixPath(raw)
        if p.is_absolute() or raw.startswith("/"):
            raise ValueError(f"destination 不允许绝对路径: {raw!r}")
        if not p.parts:
            raise ValueError("destination 不能为空")
        for part in p.parts:
            if part in ("..", ".") or _DRIVE_PART.match(part):
                raise ValueError(f"destination 含非法路径段 {part!r}: {raw!r}")
        return str(p)


class ExecutionPlan(BaseModel):
    """一次整理任务的结构化计划。"""

    scan_id: str
    summary: str
    operations: list[FileOperation] = Field(default_factory=list)
    uncertain_files: list[str] = Field(default_factory=list)

    @field_validator("scan_id")
    @classmethod
    def _scan_id_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("scan_id 不能为空")
        return v

    @field_validator("summary")
    @classmethod
    def _summary_meaningful(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("summary 不能为空白")
        return v

    @field_validator("uncertain_files")
    @classmethod
    def _uncertain_not_blank(cls, v: list[str]) -> list[str]:
        cleaned = [item.strip() for item in v]
        if any(not item for item in cleaned):
            raise ValueError("uncertain_files 不允许空白项")
        return cleaned
