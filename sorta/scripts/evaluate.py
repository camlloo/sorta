"""质量指标评估脚本（M5，开发文档 §13.3）。

对标注数据集运行真实 LLM 调查，计算并输出：
- 有足够证据文件的分类准确率（目标 ≥ 90%）
- 误移动率（misc 文件被操作的比例，目标 0）
- 待确认比例（uncertain_files / 总文件数）
- 工具调用次数与 token 成本

用法：
    uv run python scripts/evaluate.py --out <数据集目录> [--report report.json]
    # 若数据集不存在会自动生成；ground truth 写在数据集目录旁边

评估约定：request 要求模型使用 Security-Learning/{Web安全,Postman作业,逆向}
三个固定子目录，从而可以自动与 ground truth 比对。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_eval_dataset import build_dataset  # noqa: E402

REQUEST = (
    "把与 Web 安全学习相关的文件整理到 Security-Learning/Web安全，"
    "把与 Postman 接口测试作业相关的文件整理到 Security-Learning/Postman作业，"
    "把与逆向工程学习相关的文件整理到 Security-Learning/逆向，"
    "其余文件保留原处；没有把握的文件不要移动。"
)
CATEGORY_BY_DIR = {"Web安全": "web_security", "Postman作业": "postman", "逆向": "reverse"}
MISC = "misc"


def classify_destination(destination: str) -> str | None:
    """从目标路径推断模型给出的分类；不符合约定目录返回 None。"""
    parts = destination.split("/")
    if len(parts) >= 3 and parts[0] == "Security-Learning":
        return CATEGORY_BY_DIR.get(parts[1])
    return None


def evaluate(root: Path, ground_truth: dict[str, str], model: str) -> dict:
    from sorta.agent import AgentController, OpenAICompatClient
    from sorta.config import SortaConfig
    from sorta.scanner import FileRegistry, Scanner

    config = SortaConfig(max_plan_operations=500)
    api_key = os.environ.get("SORTA_API_KEY")
    if not api_key or not model:
        raise SystemExit("缺少 SORTA_API_KEY / SORTA_MODEL（.env）")

    registry = FileRegistry(ttl_seconds=7200)
    llm = OpenAICompatClient(api_key=api_key, base_url=os.environ.get("SORTA_BASE_URL") or None)
    controller = AgentController(
        registry=registry,
        scanner=Scanner(registry, config),
        config=config,
        llm=llm,
    )
    started = time.time()
    result = controller.run(root, REQUEST, model=model)
    duration = time.time() - started
    plan = result.plan
    session = registry.get_session(result.scan_id)

    stats = Counter()
    wrong: list[dict] = []
    for op in plan.operations:
        record = session.get(op.source_id)
        rel = record.relative_path if record else None
        expected = ground_truth.get(rel)
        predicted = classify_destination(op.destination)
        if expected == MISC:
            stats["misprocessed"] += 1
            wrong.append(
                {
                    "file": rel,
                    "expected": expected,
                    "predicted": predicted,
                    "reason": "misc 文件被移动（误移动）",
                }
            )
        elif predicted == expected:
            stats["correct"] += 1
        else:
            stats["wrong"] += 1
            wrong.append({"file": rel, "expected": expected, "predicted": predicted})

    # 未处理文件：出现于 uncertain_files 或完全未提及
    handled = {
        session.get(op.source_id).relative_path
        for op in plan.operations
        if session.get(op.source_id)
    }
    for fid in plan.uncertain_files:
        record = session.get(fid)
        rel = record.relative_path if record else None
        if ground_truth.get(rel) == MISC:
            stats["misc_left_alone"] += 1  # 正确：无证据文件留在原处
    missed = [
        rel for rel, expected in ground_truth.items() if expected != MISC and rel not in handled
    ]
    stats["missed"] = len(missed)

    total = len(ground_truth)
    misc_total = sum(1 for c in ground_truth.values() if c == MISC)
    classified = stats["correct"] + stats["wrong"]
    return {
        "model": model,
        "total_files": total,
        "metrics": {
            "classification_accuracy": round(stats["correct"] / classified, 4)
            if classified
            else None,
            "classified_total": classified,
            "misprocess_rate": round(stats["misprocessed"] / misc_total, 4) if misc_total else 0.0,
            "misprocessed": stats["misprocessed"],
            "uncertainty_rate": round(len(plan.uncertain_files) / total, 4),
            "missed_evidenced_files": stats["missed"],
            "tool_calls": len(result.tool_log),
            "steps_used": result.steps_used,
            "duration_seconds": round(duration, 1),
            "prompt_tokens": llm.total_usage["prompt_tokens"],
            "completion_tokens": llm.total_usage["completion_tokens"],
        },
        "targets": {
            "classification_accuracy_min": 0.90,
            "misprocess_rate_max": 0.0,
        },
        "wrong": wrong,
        "missed": missed,
        "plan": plan.model_dump(mode="json"),
    }


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="数据集目录（不存在自动生成）")
    parser.add_argument("--report", type=Path, default=None, help="评估报告 JSON 输出路径")
    args = parser.parse_args(argv)

    gt_path = args.out.parent / f"{args.out.name}_ground_truth.json"
    if not args.out.exists() or not gt_path.exists():
        ground_truth = build_dataset(args.out)
        gt_path.write_text(json.dumps(ground_truth, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        ground_truth = json.loads(gt_path.read_text(encoding="utf-8"))

    model = os.environ.get("SORTA_MODEL", "")
    report = evaluate(args.out, ground_truth, model)
    print(f"\n== 评估结果（{model}，{report['total_files']} 个文件）==")
    m = report["metrics"]
    print(
        f"分类准确率: {m['classification_accuracy']}"
        f"（目标 ≥ 0.90，参与分类 {m['classified_total']} 项）"
    )
    print(f"误移动率:   {m['misprocess_rate']}（目标 0，misc 被移动 {m['misprocessed']} 个）")
    print(f"待确认比例: {m['uncertainty_rate']}")
    print(f"漏判文件:   {m['missed_evidenced_files']} 个")
    print(f"工具调用:   {m['tool_calls']} 次 / {m['steps_used']} 步")
    print(
        f"Token 成本: prompt={m['prompt_tokens']}, completion={m['completion_tokens']}"
        f"（{m['duration_seconds']}s）"
    )
    if report["wrong"]:
        print("\n分类错误明细:")
        for item in report["wrong"][:10]:
            print(f"  {item['file']}: 期望 {item['expected']}，实际 {item['predicted']}")
    if report["missed"]:
        print("\n漏判（有证据但未移动）:")
        for rel in report["missed"][:10]:
            print(f"  {rel}")
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写入 {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
