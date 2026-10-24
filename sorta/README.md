# SORTA

> 本地优先、自然语言驱动、可预览、可撤销的 AI 文件整理助手。
> 完整规格见 [`SORTA_开发文档.md`](../SORTA_开发文档.md)（下称"开发文档"）。

## 当前状态：M1（基础文件系统层）

M1 按开发文档 §14.1/§18 交付，**不连接 LLM**：

- `sorta/scanner/scanner.py` — 授权目录扫描：深度/数量受限、不跟随符号链接与
  Windows junction、隐藏文件默认跳过、确定性排序、cursor 分页、不可读目录如实上报。
- `sorta/scanner/registry.py` — `file_id` 注册表：不可猜测 ID、会话绑定授权根目录、
  过期清理、`resolve_path` 强制边界校验（越界/被替换为链接即拒绝）、
  `verify` 检测扫描后文件变化（大小 + mtime_ns）。
- `sorta/scanner/readers.py` — 受控读取：txt/md/json/csv/py/js/ts（UTF-8 优先、GBK 回退）、
  PDF（pypdf，不做 OCR）、DOCX（python-docx 段落 + 表格）；大小/字符数上限；
  加密、扫描型、损坏等状态显式返回。文件内容一律视为不可信数据，绝不执行。

M2（Agent 只读工具 + LLM 计划生成）、M3（Validator/Executor/Undo）尚未开始。

## 环境要求

- Windows 10/11（优先支持平台）；macOS/Linux 兼容性未验证
- Python 3.12+，[uv](https://docs.astral.sh/uv/)

## 快速开始

```bash
uv sync                                  # 创建 .venv 并安装依赖
uv run pytest                            # 运行测试
uv run python scripts/make_test_fixture.py   # 生成模拟下载目录（可选）
uv run sorta scan tests/fixtures/downloads    # 扫描预览
uv run sorta scan tests/fixtures/downloads --depth 1 --limit 5
```

环境变量见 [`.env.example`](.env.example)；复制为 `.env` 后生效（勿提交真实密钥）。

## 安全边界（M1 已实现并测试覆盖）

- 未授权路径访问：相对路径解析统一走 `resolve_within_root`，绝对路径、`..`、
  盘符相对段、符号链接/junction 越界全部拒绝（`tests/test_registry.py`）。
- 符号链接与 junction：扫描阶段直接跳过，不注册、不跟随。
- 资源耗尽：扫描深度/数量、读取大小/字符数/页数全部有上限，超限显式报错。
- 异常格式：损坏 DOCX、加密 PDF、扫描型 PDF、二进制、无法解码的文本均返回
  明确状态，不猜测内容（`tests/test_readers.py`）。

## 已知限制

- 注册表为进程内实现，CLI 的 `scan` 结果不跨进程保留；持久化在 M3 引入 SQLite 后提供。
- Windows 上创建符号链接需要开发者模式或管理员权限，相关测试在不满足时自动跳过。
