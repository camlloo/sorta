"""生成带 ground truth 标注的大规模评估数据集（M5，§13.3）。

用法：
    uv run python scripts/make_eval_dataset.py --out <目录>
    # 生成 56 个文件 + ground_truth.json（relative_path -> 期望分类）

分类体系（destination 约定，便于自动比对）：
    web_security -> Security-Learning/Web安全/…
    postman      -> Security-Learning/Postman作业/…
    reverse      -> Security-Learning/逆向/…
    misc         -> 不应被移动（误移动率基准）
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from docx import Document


def build_pdf_bytes(text: str) -> bytes:
    """最小单页文本 PDF（ASCII 内容），与 make_test_fixture 相同的实现。"""
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    content = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
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


PNG_BYTES = b"\x89PNG\r\n\x1a\n fake image bytes for sorta eval dataset"

# (相对路径, 分类, 文件内容说明) —— 文本内容与主题一致，保证"有足够证据"
_FILES: list[tuple[str, str, str]] = [
    # ---- web_security (16) ----
    (
        "sqli_01_笔记.txt",
        "web_security",
        "SQL 注入学习笔记：union select、information_schema、盲注与时间注入。",
    ),
    (
        "sqli_02_union速查.md",
        "web_security",
        "# UNION 注入速查\n- order by 判断列数\n- union select 联合查询",
    ),
    (
        "sqli_03_盲注笔记.txt",
        "web_security",
        "布尔盲注与时间盲注：sleep(5)、if(condition,1,sleep(5))。",
    ),
    (
        "xss_01_笔记.txt",
        "web_security",
        "XSS 跨站脚本：反射型、存储型、DOM 型；script alert payload。",
    ),
    (
        "xss_02_payloads.json",
        "web_security",
        '{"payloads": ["<script>alert(1)</script>", "<img src=x onerror=alert(1)>"]}',
    ),
    ("csrf_01_笔记.txt", "web_security", "CSRF 跨站请求伪造：token 防御、SameSite Cookie。"),
    (
        "websec_01_burp导出.json",
        "web_security",
        '{"tool": "burp-suite", "requests": [{"url": "/login", "method": "POST"}]}',
    ),
    (
        "websec_02_渗透流程.md",
        "web_security",
        "# 渗透测试流程\n信息收集 -> 漏洞扫描 -> 漏洞利用（SQL 注入/XSS）-> 报告",
    ),
    (
        "websec_03_笔记.txt",
        "web_security",
        "OWASP Top 10 学习记录：注入、失效的访问控制、安全配置错误。",
    ),
    ("ssrf_01_笔记.txt", "web_security", "SSRF 服务端请求伪造：file:// 协议读取、内网探测。"),
    ("upload_01_文件上传绕过.txt", "web_security", "文件上传漏洞：MIME 绕过、00 截断、解析漏洞。"),
    (
        "websec_04_靶场记录.csv",
        "web_security",
        "靶场,漏洞类型,状态\nsqli-labs,SQL注入,完成\nxss-labs,XSS,进行中",
    ),
    ("xss_03_防御笔记.md", "web_security", "# XSS 防御\n输出编码、CSP、HttpOnly。"),
    ("websec_05_笔记.txt", "web_security", "Web 安全基础：HTTP 协议、同源策略、CORS。"),
    (
        "cookie_01_会话安全.txt",
        "web_security",
        "会话安全：Session 固定攻击、Cookie 属性 secure/samesite。",
    ),
    ("websec_06_思维导图.md", "web_security", "# Web 安全知识树\n注入类 / 逻辑类 / 配置类"),
    # ---- postman (8) ----
    (
        "任务书-软件测试-A.docx",
        "postman",
        "DOCX:软件测试任务书：使用 Postman 完成接口测试并导出 Collection，"
        "交付 collection.json 与运行截图。",
    ),
    (
        "任务书-软件测试-B.docx",
        "postman",
        "DOCX:任务书：用 Postman 对用户接口做增删改查测试并截图。",
    ),
    (
        "postman_collection_A.json",
        "postman",
        '{"info": {"name": "homework-A", "_postman_id": "a1"}, "item": [{"name": "login"}]}',
    ),
    (
        "postman_collection_B.json",
        "postman",
        '{"info": {"name": "homework-B", "_postman_id": "b2"}, "item": [{"name": "query"}]}',
    ),
    (
        "接口测试说明.md",
        "postman",
        "# 接口测试说明\n使用 Postman 完成接口测试，导出 Collection 并附运行截图。",
    ),
    (
        "api_test_01.py",
        "postman",
        "# 用 newman 跑批 Postman Collection\n"
        "import subprocess\n"
        "subprocess.run(['newman', 'run', 'collection.json'])",
    ),
    ("test_report.csv", "postman", "用例,结果\nlogin,pass\nquery,pass\ncreate,fail"),
    ("screenshot_demo.png", "postman", "PNG:文件名即证据（任务书要求运行截图），内容不可读"),
    # ---- reverse (10) ----
    (
        "reverse_01_ida笔记.txt",
        "reverse",
        "IDA Pro 逆向分析笔记：反汇编视图、F5 伪代码、字符串窗口。",
    ),
    ("reverse_02_汇编基础.md", "reverse", "# 汇编基础\nmov/push/call/ret；栈帧结构。"),
    ("reverse_03_笔记.txt", "reverse", "逆向工程流程：查壳、脱壳、动态调试、补丁。"),
    (
        "crackme_01_keygen.py",
        "reverse",
        "# keygen 练习：逆向 crackme 的注册机\nname = 'user'; print(hash(name) ^ 0x1234)",
    ),
    ("disasm_01_入门.pdf", "reverse", "PDF:Reverse engineering basics with IDA"),
    ("disasm_02_进阶.pdf", "reverse", "PDF:Advanced disassembly and patching"),
    ("ollydbg_01_笔记.txt", "reverse", "OllyDbg 动态调试：断点、单步、内存窗口。"),
    ("reverse_04_脱壳笔记.txt", "reverse", "UPX 脱壳：esp 定律、dump 与修复 IAT。"),
    ("asm_01_寄存器.txt", "reverse", "x86 寄存器：eax/ebx/ecx/edx、esp/ebp、eip。"),
    ("reverse_05_笔记.md", "reverse", "# 逆向笔记\n静态分析 + 动态调试结合。"),
    # ---- misc (22) —— 不应被移动 ----
    ("菜谱-红烧肉.txt", "misc", "红烧肉做法：焯水、炒糖色、炖四十分钟。"),
    ("旅行计划-云南.md", "misc", "# 云南七日游\n大理 -> 丽江 -> 泸沽湖。"),
    ("发票-2025-06.csv", "misc", "日期,金额,项目\n2025-06-03,299,键盘"),
    ("会议记录-0401.txt", "misc", "四月一日例会：同步进度、排期下周发布。"),
    ("购物清单.txt", "misc", "牛奶、鸡蛋、纸巾、洗衣液。"),
    ("健身计划.md", "misc", "# 每周训练\n周一胸、周三背、周五腿。"),
    ("日记-春天.txt", "misc", "三月的风很软，樱花开了。"),
    ("bob.png", "misc", "PNG:无主题线索"),
    ("chart.png", "misc", "PNG:无主题线索"),
    ("wallpaper.png", "misc", "PNG:无主题线索"),
    ("readme_软件安装.txt", "misc", "安装步骤：解压后运行 setup.exe，一路下一步。"),
    ("快递单号.txt", "misc", "SF1234567890，预计周四送达。"),
    ("生日提醒.txt", "misc", "妈妈生日：五月初八。"),
    ("random_data.csv", "misc", "id,value\n1,23\n2,47"),
    ("笔记-未分类.txt", "misc", "随手记：修水龙头、换灯泡。"),
    ("temp_download.pdf", "misc", "PDF:Random downloadable document content"),
    ("unknown_01.txt", "misc", "hello world"),
    ("unknown_02.md", "misc", "# 未命名笔记"),
    ("天气记录.csv", "misc", "日期,温度\n04-01,18\n04-02,21"),
    ("歌单.txt", "misc", "喜欢的歌：夜空中最亮的星。"),
    ("地铁线路.txt", "misc", "1号线 -> 2号线 -> 人民广场。"),
    ("todo.txt", "misc", "TODO:交水费、取快递、写周报。"),
]


def build_dataset(root: Path) -> dict[str, str]:
    """在 root 下生成全部文件，返回 ground truth 映射。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    ground_truth: dict[str, str] = {}
    for rel, category, content in _FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if content.startswith("DOCX:"):
            doc = Document()
            doc.add_paragraph(content[5:])
            table = doc.add_table(rows=1, cols=2)
            table.cell(0, 0).text = "交付物"
            table.cell(0, 1).text = "collection.json"
            doc.save(path)
        elif content.startswith("PDF:"):
            path.write_bytes(build_pdf_bytes(content[4:]))
        elif content.startswith("PNG:"):
            path.write_bytes(PNG_BYTES)
        elif rel.endswith((".json", ".csv", ".py", ".md")):
            path.write_text(content, encoding="utf-8", newline="\n")
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
        ground_truth[rel] = category
    return ground_truth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="数据集输出目录")
    args = parser.parse_args(argv)

    ground_truth = build_dataset(args.out)
    # ground truth 写在数据集目录之外，避免被扫描注册为未知文件
    gt_path = args.out.parent / f"{args.out.name}_ground_truth.json"
    gt_path.write_text(json.dumps(ground_truth, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {}
    for cat in ground_truth.values():
        counts[cat] = counts.get(cat, 0) + 1
    print(f"数据集已生成: {args.out}（{len(ground_truth)} 个文件 + ground_truth.json）")
    for cat, n in sorted(counts.items()):
        print(f"  {cat}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
