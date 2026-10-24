"""ExecutionPlan / FileOperation 结构校验测试（F-06 第一层）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sorta.agent.schemas import Confidence, ExecutionPlan, FileAction, FileOperation


def _op(**overrides) -> dict:
    base = {
        "source_id": "file_abc",
        "destination": "Security/Web/sqli.pdf",
        "action": "move",
        "reason": "文件名与内容均涉及 SQL 注入",
        "confidence": "high",
    }
    base.update(overrides)
    return base


def test_valid_plan_roundtrip():
    plan = ExecutionPlan.model_validate(
        {
            "scan_id": "scan_001",
            "summary": "按方向归类",
            "operations": [_op()],
            "uncertain_files": ["file_003"],
        }
    )
    assert plan.operations[0].action is FileAction.MOVE
    assert plan.operations[0].confidence is Confidence.HIGH
    assert plan.model_validate_json(plan.model_dump_json()).scan_id == "scan_001"


def test_defaults_allow_empty_plan():
    plan = ExecutionPlan.model_validate({"scan_id": "scan_001", "summary": "无操作"})
    assert plan.operations == []
    assert plan.uncertain_files == []


def test_destination_normalized():
    op = FileOperation.model_validate(_op(destination="a//b/./c.pdf"))
    assert op.destination == "a/b/c.pdf"


@pytest.mark.parametrize(
    "bad",
    [
        "/abs/path.pdf",  # 绝对路径
        "../escape.pdf",  # 越界
        "a/../../b.pdf",  # 中段越界
        "C:/win.pdf",  # 盘符
        "dir\\file.pdf",  # 反斜杠
        "",  # 空
        "   ",  # 空白
        "./",  # 无有效段
    ],
)
def test_destination_rejects_unsafe_paths(bad):
    with pytest.raises(ValidationError, match="destination"):
        FileOperation.model_validate(_op(destination=bad))


@pytest.mark.parametrize("bad", ["", "   "])
def test_reason_must_be_meaningful(bad):
    with pytest.raises(ValidationError, match="reason"):
        FileOperation.model_validate(_op(reason=bad))


def test_invalid_enum_values_rejected():
    with pytest.raises(ValidationError):
        FileOperation.model_validate(_op(action="delete"))
    with pytest.raises(ValidationError):
        FileOperation.model_validate(_op(confidence="certain"))


def test_blank_fields_rejected():
    with pytest.raises(ValidationError):
        FileOperation.model_validate(_op(source_id="  "))
    with pytest.raises(ValidationError):
        ExecutionPlan.model_validate({"scan_id": " ", "summary": "x"})
    with pytest.raises(ValidationError):
        ExecutionPlan.model_validate({"scan_id": "s", "summary": "x", "uncertain_files": [" "]})
