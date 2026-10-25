# SORTA

> 本地优先、自然语言驱动、可预览、可撤销的 AI 文件整理助手。
> 完整规格见 [`SORTA_开发文档.md`](../SORTA_开发文档.md)（下称"开发文档"）。

## 当前状态：本地 Web 应用（FastAPI + React）

核心 Python 模块（agent / scanner / planner / executor / storage）自 M1–M5 以来保持不变，
在此之上完成架构升级与体验优化：

- `sorta/application/` — 业务服务层：`SortaService` 统一编排（扫描/计划/校验/编辑/
  审批/执行/撤销/任务分流/多轮对话），CLI 与 API 共用；`JobManager` 支持长耗时任务的
  轮询与安全停点取消。
- `sorta/api/` — FastAPI 服务（仅绑定 127.0.0.1）：会话 token 鉴权、统一错误码信封、
  工作区/文件/计划/执行/历史/设置/会话全部端点；同源托管前端构建产物。
- `frontend/` — React 18 + TypeScript + Vite + Tailwind CSS，瑞士风设计系统
  （纸面/墨色/信号红 + Inter Tight + IBM Plex Mono；签名元素为"位移台账"）。
  对话（海报式空态 + 命令行 + 多轮上下文 + 计划修订）、文件库、历史、设置
  （模型配置兼容 OpenAI 接口 + 连接测试 + 整理模板）。
- 计划版本化：`plans.version` 乐观锁（编辑 +1、旧审批自动失效）；数据库迁移
  `scan_sessions / file_registry / plans / approvals / executions / operation_logs /
  templates / conversations / messages`（PRAGMA user_version 版本化）。
- 任务分流：查找（文件名匹配不读正文）/ 问答（仅基于清单）/ 整理（完整审批链）/
  意图不明先澄清；隐私三模式（metadata_only / ask_before_content / authorized_content）。
- 多轮对话：最近 8 轮上下文注入全部 LLM 请求（标注为数据防注入）；
  对话记录持久化到本机数据库，跨关闭可恢复、可切换历史会话。

## 环境要求

- Windows 10/11（优先支持平台）；macOS/Linux 兼容性未验证
- Python 3.12+，[uv](https://docs.astral.sh/uv/)
- Node.js 18+（仅前端构建时需要；日常使用不需要）

## 快速开始

```bash
uv sync                                       # 安装 Python 依赖
(cd frontend && npm install && npm run build) # 构建前端（首次）

# 完整闭环（需要 LLM API Key：复制 .env.example 为 .env 并填写，或在设置页配置）
uv run sorta ui                               # 启动本地 Web 界面（127.0.0.1:8502）并打开浏览器
# 浏览器中：设置 → 填入目录 → 保存并扫描 → 对话 → 确认执行 → 文件库/历史撤销

# CLI（与 Web 界面共用同一 Service 层）
uv run sorta scan <目录>                          # 扫描预览（无需 API Key）
uv run sorta plan --root <目录> --request "整理需求" --output plan.json
uv run sorta inspect-plan plan.json              # 校验预览
uv run sorta apply plan.json                     # 交互确认后执行
uv run sorta history && uv run sorta undo <execution_id>
```

环境变量见 [`.env.example`](.env.example)；复制为 `.env` 后生效（勿提交真实密钥）。
模型配置也可在「设置」页直接填写（Base URL / API Key / 模型名，兼容 OpenAI 接口，
保存即生效；密钥仅存本机、不回显）。开发版数据库默认 CWD 的 `sorta.db`；
未设置 `SORTA_DB_URL` 时使用系统数据目录（Windows: `%APPDATA%/sorta/sorta.db`）。

## 安全不变量

- 未审批零变更：审批绑定 plan_hash + version；编辑后旧审批失效；执行前锁内重验。
- 无静默覆盖：目标存在即拒绝；undo 冲突保留现场（尽力恢复，非事务回滚）。
- LLM 无写接口：只读工具白名单；模型计划经 submit_plan 提交并校验；
  文件正文和路径中的指令性文字只作为数据；同一会话中的真实用户约束会延续。
- 空目录清理：扫描器单独登记目录；明确的全工作区清理请求可在本地生成计划，
  审批后只用 rmdir 逐项移除空目录，先子后父；执行时变为非空就拒绝，支持撤销恢复。
  隐藏目录、含隐藏内容的目录、符号链接和授权根目录不参与自动清理。
- API 边界：仅 loopback；会话 token 鉴权；统一错误码；未授权目录拒绝。

## 开发验证

在 `sorta/` 目录运行：

```bash
uv run pytest -q
uv run ruff check .
cd frontend
npm run lint
npm run build
```

回归测试使用临时目录与独立数据库，模型调用使用替身，不需要真实 API Key。
覆盖扫描分页、API 参数与鉴权、计划修订和版本冲突、审批失效、执行幂等、
文件占用与同名冲突、移动/复制/空目录清理及撤销、正文读取授权、任务取消与流式错误，
以及会话关联计划、历史约束保留、实际执行结果入上下文和旧数据库迁移。
低置信度操作在界面中默认不勾选；调整选择后先保存并预览，再确认执行。

## 已知限制

- 撤销是"条件满足时的尽力恢复"，不是事务性回滚；跨盘移动不支持。
- 计划与扫描会话的有效期默认 1 小时（`SORTA_SCAN_SESSION_TTL`），过期需重新 plan。
- `apply --yes` 跳过交互确认，仅建议脚本场景使用。
