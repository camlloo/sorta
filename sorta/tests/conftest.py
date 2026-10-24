"""共享测试工具：最小 PDF 构造器与平台能力探测。

自动化测试一律基于 tmp_path 构造隔离目录（开发文档 §13.2），
不依赖 scripts/make_test_fixture.py 生成的静态目录。
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest


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
