# SORTA

> 本地优先、自然语言驱动、可预览、可撤销的 AI 文件整理助手。
> 完整规格见 [`SORTA_开发文档.md`](../SORTA_开发文档.md)（下称"开发文档"）。

## 当前状态：桌面版架构 D0–D3（FastAPI + React，Streamlit 已移除）

在 M1–M5 完整闭环（CLI）基础上，按《SORTA_桌面版升级开发文档_v2.md》完成架构升级：
**核心 Python 模块（agent/scanner/planner/executor/storage）不变**，新增：

- `sorta/application/` — 业务服务层：`SortaService` 统一编排（扫描/计划/校验/编辑/
  审批/执行/撤销），CLI 与 API 共用；`JobManager` 支持长耗时计划生成的轮询与取消。
- `sorta/api/` — FastAPI 服务（仅绑定 127.0.0.1）：健康检查、会话 token 鉴权、
  工作区/文件/计划/执行/历史/设置全部端点；统一错误码信封（§4.5）；同源托管
  前端构建产物。
- `frontend/` — React 18 + TypeScript + Vite + Tailwind CSS：对话（能力卡片 +
  chat 输入 + 右侧执行计划面板：三步时间线/预览/编辑/确认取消/撤销）、文件库
  （搜索/详情/本地预览/LLM 总结/重复检测/历史撤销）、设置（目录/隐私/模型状态）。
  完整对齐《SORTA智能文件整理助手界面示意.png》。
- 计划版本化：`plans.version` 乐观锁（编辑 +1、旧审批自动失效），显式迁移脚本
  （PRAGMA user_version）。
- **Streamlit 已移除**（应用户决策）；Tauri 2 桌面壳与 PyInstaller Sidecar 按 v2
  文档 D4/D5 阶段实施。

## 环境要求## 环境要求

- Windows 10/11（优先支持平台）；macOS/Linux 兼容性未验证
- Python 3.12+，[uv](https://docs.astral.sh/uv/)

## 快速开始

```bash
uv sync                                   # 安装 Python 依赖
uv run pytest                             # 运行 178 个测试
uv run python scripts/make_test_fixture.py    # 生成模拟下载目录（可选）
uv run sorta scan tests/fixtures/downloads    # CLI 扫描预览（无需 API Key）

# 完整闭环（需要 LLM API Key：复制 .env.example 为 .env 并填写）
uv run sorta ui                           # 启动本地 Web 界面（127.0.0.1:8502）并打开浏览器
# 浏览器中：设置 → 填入目录 → 保存并扫描 → 对话 → 确认执行 → 文件库撤销

# CLI 全集（调用同一 Service 层）
uv run sorta plan --root tests/fixtures/downloads --request "整理需求" --output plan.json
uv run sorta inspect-plan plan.json       # 校验预览
uv run sorta apply plan.json              # 交互确认后执行
uv run sorta history && uv run sorta undo <execution_id>

# 前端开发模式（可选）：cd frontend && npm install && npm run dev
npm run build                             # 构建产物由 FastAPI 同源托管
```

环境变量见 [`.env.example`](.env.example)；复制为 `.env` 后生效（勿提交真实密钥）。
开发版数据库默认 CWD 的 `sorta.db`；未设置 `SORTA_DB_URL` 时使用系统数据目录
（Windows: `%APPDATA%/sorta/sorta.db`）。

## 安全不变量

- 未审批零变更：审批绑定 plan_hash + version；编辑后旧审批失效；执行前锁内重验。
- 无静默覆盖：目标存在即拒绝；undo 冲突保留现场。
- LLM 无写接口：只读工具白名单；计划只能经 submit_plan 提交并校验。
- API 边界：仅 loopback；会话 token 鉴权；统一错误码；未授权目录拒绝。

## 已知限制## 质量指标（§13.3，qwen3.8-flash 实测基线 2026-10-24）

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
