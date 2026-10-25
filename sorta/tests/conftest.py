"""共享测试工具：最小 PDF 构造器、平台能力探测、Mock LLM 与大规模评估集。

自动化测试一律基于 tmp_path 构造隔离目录（开发文档 §13.2），
不依赖 scripts/make_test_fixture.py 生成的静态目录。
"""

from __future__ import annotations

import copy
import io
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


# ---- Mock LLM（AgentController 测试与大规模管线测试共用） --------------------


class MockLLM:
    """脚本化替身：响应项为 LLMResponse 或 (request) -> LLMResponse 的函数。"""

    def __init__(self, script: list):
        self.script = list(script)
        self.requests: list[dict] = []

    def chat(self, *, model, messages, tools=None):
        self.requests.append({"model": model, "messages": copy.deepcopy(messages), "tools": tools})
        if not self.script:
            raise AssertionError("MockLLM 脚本耗尽，循环未按预期收敛")
        item = self.script.pop(0)
        return item(self.requests[-1]) if callable(item) else item


def tc(name: str, args: dict | None = None, call_id: str | None = None):
    from sorta.agent.llm import ToolCall

    return ToolCall(
        id=call_id or f"call_{name}",
        name=name,
        arguments=json.dumps(args or {}, ensure_ascii=False),
    )


def resp(*calls, content: str | None = None):
    from sorta.agent.llm import LLMResponse

    return LLMResponse(content=content, tool_calls=list(calls))


def tool_payloads(request: dict) -> list[dict]:
    return [json.loads(m["content"]) for m in request["messages"] if m.get("role") == "tool"]


def pdf_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_pdf_bytes(text: str) -> bytes:
    """手工构造只含一页文本的最小 PDF，避免为测试引入 reportlab 之类重依赖。

    文本限定 ASCII（Helvetica 基础字体不含 CJK 字形）。
    """
    content = f"BT /F1 18 Tf 72 720 Td ({pdf_escape(text)}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj ".encode() + obj + b" endobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    out.write(trailer.encode())
    return out.getvalue()


@pytest.fixture
def make_pdf():
    def _make(path: str | Path, text: str = "Hello PDF") -> None:
        Path(path).write_bytes(build_pdf_bytes(text))

    return _make


@pytest.fixture
def make_encrypted_pdf():
    def _make(path: str | Path, password: str = "secret") -> None:
        from pypdf import PdfReader, PdfWriter

        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(build_pdf_bytes("Top secret text"))))
        writer.encrypt(user_password=password, owner_password=password)
        buf = io.BytesIO()
        writer.write(buf)
        Path(path).write_bytes(buf.getvalue())

    return _make


@pytest.fixture
def can_symlink(tmp_path_factory) -> bool:
    """探测当前环境能否创建符号链接（Windows 需要开发者模式或管理员）。

    探测产物放在独立临时目录，避免污染测试用的 tmp_path。
    """
    base = tmp_path_factory.mktemp("probe-symlink")
    target = base / "probe-target.txt"
    target.write_text("x", encoding="utf-8")
    link = base / "probe-link"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        return False
    return link.is_symlink()


@pytest.fixture
def can_junction(tmp_path_factory) -> bool:
    """探测当前环境能否创建 Windows junction（Python 3.12+ os.junction）。"""
    if os.name != "nt":
        return False
    junction = getattr(os, "junction", None)
    isjunction = getattr(os.path, "isjunction", None)
    if junction is None or isjunction is None:
        return False
    base = tmp_path_factory.mktemp("probe-junction")
    target = base / "probe-target-dir"
    target.mkdir()
    link = base / "probe-junction"
    # os.junction 的参数顺序按 (link, target) 与 (target, link) 两种语义都试一次
    for call in ((str(link), str(target)), (str(target), str(link))):
        try:
            junction(*call)
        except OSError:
            continue
        if isjunction(str(link)):
            return True
    return False


# ---- M3 共享构件 -------------------------------------------------------------


@pytest.fixture
def m3_env(tmp_path):
    """端到端环境：真实目录 + 内存 SQLite + 已扫描会话（plan→apply→undo 全链路共用）。"""
    from types import SimpleNamespace

    from sorta.config import SortaConfig
    from sorta.scanner import FileRegistry, Scanner
    from sorta.storage import PlanStore, create_db_engine, init_db

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8", newline="\n")
    (root / "b.txt").write_text("world", encoding="utf-8", newline="\n")
    (root / "sub").mkdir()
    (root / "sub" / "c.txt").write_text("deep", encoding="utf-8", newline="\n")

    config = SortaConfig()
    engine = create_db_engine("sqlite://")
    init_db(engine)
    store = PlanStore(engine)
    registry = FileRegistry(ttl_seconds=3600)
    scanner = Scanner(registry, config)
    page = scanner.scan(root)
    store.save_session(registry.get_session(page.scan_id))
    session = registry.get_session(page.scan_id)
    return SimpleNamespace(
        root=root,
        config=config,
        store=store,
        registry=registry,
        scanner=scanner,
        session=session,
        scan_id=page.scan_id,
    )


def make_plan(
    session, moves, *, action="move", confidence="high", uncertain=None, summary="测试计划"
):
    """按 (源相对路径, 目标相对路径) 列表构造 ExecutionPlan。"""
    from sorta.agent.schemas import ExecutionPlan

    operations = []
    for src, dst in moves:
        record = session.find_by_path(src)
        assert record is not None, f"源文件未注册: {src}"
        operations.append(
            {
                "source_id": record.id,
                "destination": dst,
                "action": action,
                "reason": "测试依据",
                "confidence": confidence,
            }
        )
    return ExecutionPlan(
        scan_id=session.scan_id,
        summary=summary,
        operations=operations,
        uncertain_files=uncertain or [],
    )


def approve(env, plan):
    """走完 draft → validated → awaiting_approval → approved + 审批落库。"""
    from sorta.planner import compute_plan_hash

    plan_hash = compute_plan_hash(plan)
    plan_id = env.store.save_plan(plan.model_dump(mode="json"), plan_hash, status="draft")
    for status in ("validated", "awaiting_approval"):
        env.store.update_plan_status(plan_id, status)
    approval = env.store.save_approval(plan_id, plan_hash)
    env.store.update_plan_status(plan_id, "approved")
    return plan_hash, approval


def execute_plan(env, plan, *, approval=None):
    from sorta.executor import ExecutionEngine

    if approval is None:
        _, approval = approve(env, plan)
    engine = ExecutionEngine(
        registry=env.registry, scanner=env.scanner, config=env.config, store=env.store
    )
    return engine.execute(plan, approval_id=approval.id)
