"""M5 大规模评估集测试（§13.1/§13.2/§13.3）：56 个标注文件上的安全不变量。

数据集由 scripts/make_eval_dataset.py 生成（build_dataset 可直接调用），
ground truth 标注期望分类；misc 类文件作为"误移动率"的基准。
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import execute_plan, make_plan
from make_eval_dataset import build_dataset

from sorta.config import SortaConfig
from sorta.executor import UndoEngine
from sorta.planner import PlanValidator, compute_plan_hash, has_errors
from sorta.scanner import FileRegistry, Scanner
from sorta.storage import PlanStore, create_db_engine, init_db

CATEGORY_DIR = {
    "web_security": "Security-Learning/Web安全",
    "postman": "Security-Learning/Postman作业",
    "reverse": "Security-Learning/逆向",
}
MISC = "misc"


@pytest.fixture
def eval_env(tmp_path):
    """56 个标注文件 + 内存 SQLite + 已扫描会话。"""
    root = tmp_path / "dataset"
    ground_truth = build_dataset(root)
    assert len(ground_truth) >= 50, "评估集必须至少 50 个文件（§14.1 M5）"

    config = SortaConfig(max_plan_operations=500)
    engine = create_db_engine("sqlite://")
    init_db(engine)
    store = PlanStore(engine)
    registry = FileRegistry(ttl_seconds=7200)
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
        ground_truth=ground_truth,
    )


def _ground_truth_plan(env):
    """按标注构造"理想计划"：非 misc 文件按类目移动，misc 不动。"""
    moves = []
    for rel, category in env.ground_truth.items():
        if category == MISC:
            continue
        moves.append((rel, f"{CATEGORY_DIR[category]}/{os.path.basename(rel)}"))
    return make_plan(env.session, moves, summary="按标注分类")


def _tree_snapshot(root: Path) -> set[tuple[str, int, int]]:
    snapshot = set()
    for base, _, files in os.walk(root):
        for name in files:
            path = Path(base) / name
            st = os.stat(path)
            rel = os.path.relpath(path, root).replace("\\", "/")
            snapshot.add((rel, st.st_size, st.st_mtime_ns))
    return snapshot


def _tree_files(root: Path) -> set[str]:
    return {
        os.path.relpath(Path(base) / name, root).replace("\\", "/")
        for base, _, files in os.walk(root)
        for name in files
    }


def test_scan_registers_whole_dataset(eval_env):
    """56 个文件全部注册：file_id 唯一、路径与标注一一对应。"""
    assert len(eval_env.session.files) == len(eval_env.ground_truth) == 56
    for record in eval_env.session.files.values():
        assert record.relative_path in eval_env.ground_truth
        assert record.is_regular_file


def test_ground_truth_plan_validates_clean(eval_env):
    """理想计划通过 §7.2 全部校验：标注体系本身无冲突。"""
    plan = _ground_truth_plan(eval_env)
    validator = PlanValidator(eval_env.registry, eval_env.config)
    _, issues = validator.validate(plan)
    assert not has_errors(issues), [i.message for i in issues if i.level == "error"]


def test_rejected_plan_changes_nothing(eval_env):
    """集成场景 §13.2-2：用户拒绝计划 → 目录哈希/文件树零变化。"""
    plan = _ground_truth_plan(eval_env)
    before = _tree_snapshot(eval_env.root)

    plan_id = eval_env.store.save_plan(
        plan.model_dump(mode="json"), compute_plan_hash(plan), status="draft"
    )
    for status in ("validated", "awaiting_approval"):
        eval_env.store.update_plan_status(plan_id, status)
    eval_env.store.update_plan_status(plan_id, "rejected")  # 用户在确认前拒绝

    assert _tree_snapshot(eval_env.root) == before
    assert eval_env.store.get_plan(plan_id).status == "rejected"


def test_full_pipeline_and_undo_on_large_dataset(eval_env):
    """34 项分类移动全部成功 → Undo 成功率 100%（无冲突场景，§13.3 目标）。

    同时验证安全不变量：22 个 misc 文件自始至终未被触碰；
    全量撤销后文件树（路径 + 大小 + mtime）与执行前完全一致。
    """
    plan = _ground_truth_plan(eval_env)
    misc_rels = [rel for rel, cat in eval_env.ground_truth.items() if cat == MISC]
    misc_before = {rel: (eval_env.root / rel).read_bytes() for rel in misc_rels}
    before = _tree_snapshot(eval_env.root)

    result = execute_plan(eval_env, plan)
    assert result.status == "completed" and len(result.operations) == 34

    # 分类落位正确，misc 未被触碰
    for op in result.operations:
        assert (eval_env.root / op.destination).exists()
    current_files = _tree_files(eval_env.root)
    for rel in misc_rels:
        assert rel in current_files, f"misc 文件被误移动: {rel}"
        assert (eval_env.root / rel).read_bytes() == misc_before[rel]

    # 全量撤销 → 文件树逐字节还原
    undo = UndoEngine(store=eval_env.store).undo_execution(result.execution_id)
    assert undo.status == "undone" and len(undo.operations) == 34
    assert _tree_snapshot(eval_env.root) == before


def test_misc_files_never_touched_during_execution(eval_env):
    """安全不变量专项：执行阶段的操作里不允许出现 misc（无证据）文件。"""
    plan = _ground_truth_plan(eval_env)
    result = execute_plan(eval_env, plan)
    for op in result.operations:
        assert eval_env.ground_truth.get(op.source) != MISC
