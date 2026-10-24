# SORTA

> 本地优先、自然语言驱动、可预览、可撤销的 AI 文件整理助手。
> 完整规格见 [`SORTA_开发文档.md`](../SORTA_开发文档.md)（下称"开发文档"）。

## 当前状态：M1 + M2（文件系统层 + Agent 计划生成）

M1 按开发文档 §14.1/§18 交付，**不连接 LLM**：

- `sorta/scanner/scanner.py` — 授权目录扫描：深度/数量受限、不跟随符号链接与
  Windows junction、隐藏文件默认跳过、确定性排序、cursor 分页、不可读目录如实上报。
- `sorta/scanner/registry.py` — `file_id` 注册表：不可猜测 ID、会话绑定授权根目录、
  过期清理、`resolve_path` 强制边界校验（越界/被替换为链接即拒绝）、
  `verify` 检测扫描后文件变化（大小 + mtime_ns）。
- `sorta/scanner/readers.py` — 受控读取：txt/md/json/csv/py/js/ts（UTF-8 优先、GBK 回退）、
  PDF（pypdf，不做 OCR）、DOCX（python-docx 段落 + 表格）；大小/字符数上限；
  加密、扫描型、损坏等状态显式返回。文件内容一律视为不可信数据，绝不执行。

M2 已交付（不使用任何 Agent 框架，Tool Calling 循环手写于 `agent/controller.py`）：

- `sorta/agent/tools.py` — 只读工具白名单（list_files / get_file_info / read_file /
  search_files）与参数校验；move/copy/delete/shell 等一律拒绝；隐私门三种模式
  （metadata_only 下 read_file 不可见、ask_before_content 逐文件确认、authorized_content）。
- `sorta/agent/controller.py` — 有限循环（默认 8 步）+ 工具调用总次数上限（默认 40）+
  审计日志；模型先调查（list_files → get_file_info/read_file）再经 `submit_plan`
  提交计划；计划结构校验失败或 scan_id 不符会把错误反馈给模型重试。
- `sorta/agent/schemas.py` — Pydantic `ExecutionPlan`：destination 强制相对 posix
  路径（拒绝绝对路径/`..`/反斜杠/盘符），reason 必须有实质内容。
- `sorta/agent/prompts.py` — 系统提示词：授权范围、工作流程、不可信内容约束
  （文件内容中的指令一律当数据）、隐私模式说明、低信心处理。

M3（PlanValidator/Executor/SQLite/Undo）、M4（Streamlit）尚未开始。

## 环境要求

- Windows 10/11（优先支持平台）；macOS/Linux 兼容性未验证
- Python 3.12+，[uv](https://docs.astral.sh/uv/)

## 快速开始

```bash
uv sync                                  # 创建 .venv 并安装依赖
uv run pytest                            # 运行测试
uv run python scripts/make_test_fixture.py   # 生成模拟下载目录（可选）
uv run sorta scan tests/fixtures/downloads    # 扫描预览（M1，无需 API Key）

# 自然语言生成整理计划（M2，需要 LLM API Key，只读调查、不执行任何移动）
cp .env.example .env   # 填入 SORTA_API_KEY / SORTA_MODEL（OpenAI 兼容接口）
uv run sorta plan --root tests/fixtures/downloads \
  --request "把安全学习资料整理到 Security-Learning，不确定的保留原处" \
  --output plan.json
```

环境变量见 [`.env.example`](.env.example)；复制为 `.env` 后生效（勿提交真实密钥）。

## 安全边界（M1 已实现并测试覆盖）

- 未授权路径访问：相对路径解析统一走 `resolve_within_root`，绝对路径、`..`、
  盘符相对段、符号链接/junction 越界全部拒绝（`tests/test_registry.py`）。
- 符号链接与 junction：扫描阶段直接跳过，不注册、不跟随。
- 资源耗尽：扫描深度/数量、读取大小/字符数/页数全部有上限，超限显式报错。
- 异常格式：损坏 DOCX、加密 PDF、扫描型 PDF、二进制、无法解码的文本均返回
  明确状态，不猜测内容（`tests/test_readers.py`）。
- LLM 拿不到写接口：工具白名单之外（move/copy/delete/shell…）一律拒绝；计划只能
  经 `submit_plan` 提交并通过结构校验、scan_id 绑定（`tests/test_agent_tools.py`、
  `tests/test_agent_controller.py`）。
- Prompt Injection：文件内容以 JSON 数据形式进入对话，注入文本无法伪造消息结构；
  提示词明确要求忽略内容中的指令并如实标注（`tests/test_agent_controller.py`）。
- 失控调用防护：步数上限 + 工具调用总次数上限，超限中止并留审计日志。

## 已知限制

- M2 仅生成计划，不执行任何文件操作；执行引擎与撤销在 M3 提供。
- `plan` 命令需要 OpenAI 兼容的 LLM API（`SORTA_BASE_URL` 可指向任意兼容服务商）。
- 注册表为进程内实现，扫描结果不跨进程保留；持久化在 M3 引入 SQLite 后提供。
- Windows 上创建符号链接需要开发者模式或管理员权限，相关测试在不满足时自动跳过。
