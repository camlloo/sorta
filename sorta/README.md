# SORTA

> 本地优先、自然语言驱动、可预览、可撤销的 AI 文件整理助手。
> 完整规格见 [`SORTA_开发文档.md`](../SORTA_开发文档.md)（下称"开发文档"）。

## 当前状态：M1 + M2 + M3 + M4（CLI 与图形界面双入口，核心闭环完整）

M1 文件系统层（详见下方模块说明）；M2 Agent 只读调查与计划生成（不使用任何
Agent 框架，Tool Calling 循环手写于 `agent/controller.py`）；M3 安全执行与撤销。

M3 已交付（开发文档 §7/§8/§9）：

- `sorta/planner/validator.py` — 执行前确定性校验（§7.2 十项）：源/目标边界、
  符号链接、重复源、重复目标（NTFS 大小写归一化）、同名目标冲突、源==目标、
  扫描后文件变化（STALE）、Windows 保留名、互换覆盖对（A→B 且 B→A）、
  深度/路径长度预警、操作条数上限。
- `sorta/planner/schemas.py` — `compute_plan_hash`：规范化序列化 SHA-256，
  人工编辑即失效既有审批——不能拿审批 A 执行计划 B。
- `sorta/storage/` — SQLAlchemy 2.x + SQLite：`models.py`（六张表 + 外键 +
  唯一约束）、`database.py`（`PRAGMA foreign_keys=ON` 逐连接开启）、
  `repository.py`（PlanStore 仓储；ORM 对象不越过 storage 层）。
- `sorta/executor/engine.py` — 审批绑定校验、进程内互斥锁、锁内重验、
  确定性 execution_id（同一审批无法重复执行）、串行逐项执行、
  `recover_interrupted` 崩溃恢复（按真实文件状态核对，不盲目重放）。
- `sorta/executor/safety.py` — 无覆盖 move/copy（目标存在即拒绝）、
  跨设备移动显式报错、大小+mtime_ns 指纹（§17.4）。
- `sorta/executor/undo.py` — 条件撤销：目标未被修改且原路径空闲才移回；
  copy 撤销仅删除本次创建且未改动的副本；冲突保留现场绝不覆盖（§8.3）。

M4 已交付（开发文档 §11.1）：

- `sorta/ui/app.py` — Streamlit 图形界面（`uv run sorta ui` 启动）：侧栏工作目录与
  隐私模式选择（含扫描预览）、自然语言需求输入、实时只读工具调用日志、
  可编辑计划表格（勾选剔除操作 / 修改目标路径，内容变更自动重新校验）、
  批准执行与逐项结果、历史与撤销。与 CLI 共用同一套业务模块（调查/编辑/
  审批执行/撤销），不复制任何安全逻辑。
- 可测试逻辑独立于 UI：`planner/editing.py`（计划编辑重建）、`executor/flows.py`
  （approve_and_execute 共享审批执行流）、`agent/controller.py` 的
  `on_tool_record` 回调（实时工具日志）。

M5（大规模测试集与打磨）尚未开始。

## 环境要求

- Windows 10/11（优先支持平台）；macOS/Linux 兼容性未验证
- Python 3.12+，[uv](https://docs.astral.sh/uv/)

## 快速开始

```bash
uv sync                                  # 创建 .venv 并安装依赖
uv run pytest                            # 运行测试
uv run python scripts/make_test_fixture.py   # 生成模拟下载目录（可选）
uv run sorta scan tests/fixtures/downloads    # 扫描预览（无需 API Key）

# 完整闭环：计划 → 校验 → 执行 → 撤销（需要 LLM API Key）
cp .env.example .env   # 填入 SORTA_API_KEY / SORTA_MODEL（OpenAI 兼容接口）
uv run sorta ui                             # 图形界面（推荐，无需写命令）
uv run sorta plan --root tests/fixtures/downloads \
  --request "把安全学习资料整理到 Security-Learning，不确定的保留原处" \
  --output plan.json
uv run sorta inspect-plan plan.json      # 校验预览（不执行）
uv run sorta apply plan.json             # 交互确认后执行
uv run sorta history                     # 执行历史
uv run sorta undo <execution_id>         # 无冲突时尽力撤销
```

环境变量见 [`.env.example`](.env.example)；复制为 `.env` 后生效（勿提交真实密钥）。
数据库默认 `sqlite:///sorta.db`（CWD）；删除该行则用系统数据目录
（Windows: `%APPDATA%/sorta/sorta.db`）。

## 安全边界（已实现并测试覆盖）

- 未授权路径访问：相对路径解析统一走 `resolve_within_root`，绝对路径、`..`、
  盘符相对段、符号链接/junction 越界全部拒绝（`tests/test_registry.py`、
  `tests/test_plan_validator.py`）。
- 未审批零变更：审批通过 `plan_hash` 精确绑定，执行前锁内重验；未审批、
  计划被编辑、源文件被改动均拒绝执行（`tests/test_executor.py`）。
- 无静默覆盖：目标已存在一律拒绝（逐操作失败，其余继续），undo 冲突时
  保留现场（`tests/test_executor.py`、`tests/test_undo.py`）。
- 重复执行防护：确定性 execution_id + 数据库主键约束，同一审批不可执行两次。
- LLM 拿不到写接口：工具白名单之外（move/copy/delete/shell…）一律拒绝；
  计划只能经 `submit_plan` 提交并通过结构校验、scan_id 绑定。
- Prompt Injection：文件内容以 JSON 数据形式进入对话；执行权限完全独立于 LLM。
- 失控调用防护：步数上限 + 工具调用总次数上限，超限中止并留审计日志。

## 质量指标（§13.3，qwen3.8-flash 实测基线 2026-10-24）

对 56 个标注文件（`scripts/make_eval_dataset.py` 生成，含 22 个"无证据 misc"文件）
运行 `uv run python scripts/evaluate.py --out <目录>` 的实测结果：

| 指标 | 实测 | 目标 | 结论 |
|---|---|---|---|
| 分类准确率（有足够证据文件） | **100%**（30/30） | ≥ 90% | ✅ |
| 误移动率（misc 被移动） | **0%** | 0 | ✅ |
| 待确认比例 | 17.9% | 越保守越好 | 符合"不确定就保留"原则 |
| 漏判（有证据但未移动） | 4 个（Postman 任务书等） | 单独观察 | 偏保守，不产生风险 |
| 成本 | 11 次工具调用 / 5 步 / 26k prompt + 11k completion tokens / 172s | 持续降低 | — |

安全指标由测试集强制保证（`uv run pytest`）：未授权写操作 = 0、静默覆盖 = 0、
无冲突场景 Undo 成功率 = 100%（`tests/test_large_dataset.py`）。
评估可复现：脚本会在数据集缺失时自动重建（确定性生成，无随机成分）。

## 已知限制

- 撤销是"条件满足时的尽力恢复"（§8.3），不是事务性回滚；跨盘移动不支持。
- 计划与扫描会话的有效期默认 1 小时（`SORTA_SCAN_SESSION_TTL`），过期需重新 plan。
- Windows 上创建符号链接需要开发者模式或管理员权限，相关测试在不满足时自动跳过。
- `apply --yes` 跳过交互确认，仅建议脚本场景使用。
