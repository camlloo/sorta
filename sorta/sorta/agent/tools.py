"""只读工具白名单、参数校验与调度（开发文档 §6）。

权限模型（§6.5）：
- LLM 可调用：list_files / get_file_info / read_file / search_files（read_file 受隐私模式约束）
- LLM 永不可调用：move / copy / delete / shell / network_upload —— 不在白名单即拒绝
- submit_plan 是计划提交通道，由 controller 处理并记录，不属于文件工具

所有工具结果一律 JSON 序列化后返回：文件内容是数据，不能伪造消息结构（Prompt
Injection 防护）。read_file 的内容在会话内缓存，供 search_files 做内容匹配；
未读取过的文件不得声称已通过全文检索验证（§6.4）。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from sorta.config import SortaConfig
from sorta.errors import SortaError
from sorta.models import FileRecord
from sorta.scanner.readers import ReadStatus, read_file
from sorta.scanner.registry import FileRegistry, ScanSession
from sorta.scanner.scanner import Scanner

READ_ONLY_TOOLS = ("list_files", "get_file_info", "read_file", "search_files")
PLAN_TOOL = "submit_plan"
_SUPPORTED_PARSE_FORMATS = frozenset({"pdf", "docx"}) | {
    "txt",
    "md",
    "json",
    "csv",
    "py",
    "js",
    "ts",
}
_MAX_SEARCH_LIMIT = 50
_ARG_TRUNC = 500


class ToolCallRecord(BaseModel):
    """一次工具调用的审计记录（含返回给模型的结果）。"""

    index: int
    name: str
    arguments: str  # 截断后的原始参数 JSON
    ok: bool
    summary: str
    result_json: str  # 仅用于本轮对话消息；持久化时不得写入（可能含文件正文）
    error: str | None = None
    duration_ms: int = 0


class ListFilesArgs(BaseModel):
    depth: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=1)
    cursor: str | None = None


class GetFileInfoArgs(BaseModel):
    file_id: str = Field(min_length=1)


class ReadFileArgs(BaseModel):
    file_id: str = Field(min_length=1)
    max_chars: int | None = Field(default=None, ge=1)


class SearchFilesArgs(BaseModel):
    query: str
    scope: str = "auto"
    limit: int = Field(default=20, ge=1, le=_MAX_SEARCH_LIMIT)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query 不能为空白")
        return v

    @field_validator("scope")
    @classmethod
    def _scope_valid(cls, v: str) -> str:
        if v not in ("name", "content", "auto"):
            raise ValueError("scope 必须是 name/content/auto 之一")
        return v


_ARG_MODELS: dict[str, type[BaseModel]] = {
    "list_files": ListFilesArgs,
    "get_file_info": GetFileInfoArgs,
    "read_file": ReadFileArgs,
    "search_files": SearchFilesArgs,
}

ConsentCallback = Callable[[FileRecord], bool]


def _function_spec(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_OPERATION_SCHEMA = {
    "type": "object",
    "properties": {
        "source_id": {"type": "string", "description": "list_files 返回的 file_id"},
        "destination": {
            "type": "string",
            "description": "相对根目录的目标文件路径（正斜杠分隔，禁止绝对路径与 ..）",
        },
        "action": {"type": "string", "enum": ["move", "copy", "rmdir"]},
        "reason": {
            "type": "string",
            "description": "判断依据（中文，基于真实观察到的名称/内容/类型）",
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["source_id", "destination", "action", "reason", "confidence"],
}


class ToolDispatcher:
    """只读工具调度器：白名单、参数校验、隐私门、审计日志的唯一入口。"""

    def __init__(
        self,
        *,
        registry: FileRegistry,
        session: ScanSession,
        scanner: Scanner,
        config: SortaConfig,
        consent_callback: ConsentCallback | None = None,
    ):
        self.registry = registry
        self.session = session
        self.scanner = scanner
        self.config = config
        self.privacy = config.default_privacy
        self.consent_callback = consent_callback
        self.log: list[ToolCallRecord] = []
        self._content_cache: dict[str, str] = {}  # file_id -> 已读取文本
        self._consents: dict[str, bool] = {}
        self._specs = self._build_specs()

    # ---- 工具清单（供 LLM 选择） ------------------------------------------

    def _build_specs(self) -> list[dict[str, Any]]:
        specs = [
            _function_spec(
                "list_files",
                "列出授权目录内的文件元数据（相对路径/扩展名/大小/修改时间），分页返回。",
                {
                    "depth": {
                        "type": "integer",
                        "description": "子目录层数上限，0 表示仅根目录下的文件",
                    },
                    "limit": {"type": "integer", "description": "本页文件数量"},
                    "cursor": {"type": "string", "description": "上一页返回的 next_cursor"},
                },
                [],
            ),
            _function_spec(
                "get_file_info",
                "查看指定 file_id 文件的类型、大小、时间、当前状态与可读性。",
                {"file_id": {"type": "string", "description": "list_files 返回的 file_id"}},
                ["file_id"],
            ),
            _function_spec(
                "search_files",
                "在已发现的文件中按名称检索，并可匹配本会话已读取过的内容。",
                {
                    "query": {"type": "string", "description": "关键词，空格分隔多个"},
                    "scope": {
                        "type": "string",
                        "enum": ["name", "content", "auto"],
                        "description": "匹配范围：仅名称/仅已缓存内容/自动",
                    },
                    "limit": {"type": "integer", "description": "最多返回匹配数"},
                },
                ["query"],
            ),
        ]
        if self.privacy != "metadata_only":
            specs.append(
                _function_spec(
                    "read_file",
                    "读取指定 file_id 的文本内容（txt/md/json/csv/代码/pdf/docx），受长度限制。",
                    {
                        "file_id": {"type": "string", "description": "list_files 返回的 file_id"},
                        "max_chars": {"type": "integer", "description": "返回文本长度上限"},
                    },
                    ["file_id"],
                )
            )
        # submit_plan 是计划提交通道：必须出现在工具清单中，模型才能调用；
        # 它不是文件工具，dispatch 层仍会拒绝直接调度（由 controller 截获处理）。
        specs.append(
            _function_spec(
                PLAN_TOOL,
                "提交最终整理计划。调查完成后必须通过本工具提交；禁止在文本回复中输出计划 JSON。",
                {
                    "scan_id": {"type": "string", "description": "当前扫描会话 ID"},
                    "summary": {"type": "string", "description": "计划摘要（中文）"},
                    "operations": {
                        "type": "array",
                        "items": _OPERATION_SCHEMA,
                        "description": "逐文件操作列表；没有把握的文件不要放入",
                    },
                    "uncertain_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "无法确定关联性的 file_id 列表（不移动，交由用户决定）",
                    },
                },
                ["scan_id", "summary", "operations", "uncertain_files"],
            )
        )
        return specs

    @property
    def specs(self) -> list[dict[str, Any]]:
        return self._specs

    # ---- 调度入口 ---------------------------------------------------------

    def dispatch(self, name: str, args_json: str = "{}") -> ToolCallRecord:
        started = time.perf_counter()
        index = len(self.log) + 1
        record = ToolCallRecord(
            index=index,
            name=name,
            arguments=(args_json or "")[:_ARG_TRUNC],
            ok=False,
            summary="",
            result_json="{}",
        )
        try:
            result, ok, summary = self._handle(name, args_json)
        except SortaError as exc:
            result, ok, summary = {"error": str(exc)}, False, f"{name}: {exc}"
        record.result_json = json.dumps(result, ensure_ascii=False)
        record.ok = ok
        record.summary = summary
        record.error = None if ok else summary
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        self.log.append(record)
        return record

    def log_external(
        self, name: str, args_json: str, *, ok: bool, summary: str, result: dict[str, Any]
    ) -> ToolCallRecord:
        """记录非文件类调用（如 submit_plan 的校验结果），保持审计日志完整。"""
        record = ToolCallRecord(
            index=len(self.log) + 1,
            name=name,
            arguments=(args_json or "")[:_ARG_TRUNC],
            ok=ok,
            summary=summary,
            result_json=json.dumps(result, ensure_ascii=False),
            error=None if ok else summary,
        )
        self.log.append(record)
        return record

    def _handle(self, name: str, args_json: str) -> tuple[dict[str, Any], bool, str]:
        if name not in READ_ONLY_TOOLS:
            allowed = ", ".join(READ_ONLY_TOOLS)
            return (
                {"error": f"工具 {name!r} 不在只读白名单内，已拒绝。只读工具：{allowed}"},
                False,
                f"拒绝非白名单工具 {name}",
            )
        if name == "read_file" and self.privacy == "metadata_only":
            return (
                {"error": "当前隐私模式为 metadata_only，禁止读取文件内容"},
                False,
                "隐私模式拒绝 read_file",
            )

        args_model = _ARG_MODELS[name]
        try:
            args = args_model.model_validate_json(args_json or "{}")
        except ValidationError as exc:
            issues = [
                f"{'/'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                for err in exc.errors(include_url=False)
            ]
            return {"error": "参数校验失败", "details": issues}, False, f"{name} 参数非法"

        if name == "list_files":
            return self._list_files(args)  # type: ignore[arg-type]
        if name == "get_file_info":
            return self._get_file_info(args)  # type: ignore[arg-type]
        if name == "read_file":
            return self._read_file(args)  # type: ignore[arg-type]
        return self._search_files(args)  # type: ignore[arg-type]

    # ---- 各工具实现 -------------------------------------------------------

    def _list_files(self, args: ListFilesArgs) -> tuple[dict[str, Any], bool, str]:
        page = self.scanner.scan(
            self.session.root,
            session=self.session,
            max_depth=args.depth,
            limit=args.limit,
            cursor=args.cursor,
        )
        result = {
            "scan_id": page.scan_id,
            "files": [f.model_dump() for f in page.files],
            "empty_directories": [f.model_dump() for f in page.empty_directories],
            "directory_scan_has_more": page.directory_scan_has_more,
            "has_more": page.has_more,
            "next_cursor": page.next_cursor,
            "skipped_directories": page.skipped_directories,
        }
        summary = f"列出 {len(page.files)} 个文件" + ("（还有更多）" if page.has_more else "")
        return result, True, summary

    def _get_file_info(self, args: GetFileInfoArgs) -> tuple[dict[str, Any], bool, str]:
        record, _ = self.registry.resolve_path(self.session.scan_id, args.file_id)
        verification = self.registry.verify(self.session.scan_id, args.file_id)
        supported = record.extension in _SUPPORTED_PARSE_FORMATS
        size_ok = record.size <= self.config.max_file_bytes
        readable = supported and size_ok
        notes: list[str] = []
        if not supported:
            notes.append("格式不支持文本提取")
        if not size_ok:
            notes.append(f"大小超过上限 {self.config.max_file_bytes} 字节")
        result = {
            "file_id": record.id,
            "relative_path": record.relative_path,
            "extension": record.extension,
            "size": record.size,
            "modified_ns": record.modified_ns,
            "parse_format": record.extension if supported else None,
            "readable": readable,
            "state": str(verification.status),
            "notes": notes,
            "content_already_read": record.id in self._content_cache,
        }
        summary = (
            f"{record.relative_path}: "
            f"{'可读' if readable else '不可读'}({record.extension or '无扩展名'})"
        )
        return result, True, summary

    def _read_file(self, args: ReadFileArgs) -> tuple[dict[str, Any], bool, str]:
        record, path = self.registry.resolve_path(self.session.scan_id, args.file_id)
        if not record.is_regular_file:
            return {"error": "目录不是可读取正文的文件"}, False, "拒绝读取目录正文"
        if not self._consented(record):
            return (
                {
                    "file_id": record.id,
                    "error": "用户拒绝读取该文件内容，请改用文件名等依据或列入 uncertain_files",
                },
                False,
                f"用户拒绝读取 {record.relative_path}",
            )
        requested = args.max_chars or self.config.max_read_chars
        effective = min(requested, self.config.max_read_chars)
        outcome = read_file(
            path,
            max_chars=effective,
            max_bytes=self.config.max_file_bytes,
            file_id=record.id,
        )
        if outcome.status is ReadStatus.OK and outcome.content is not None:
            self._content_cache[record.id] = outcome.content
        result = outcome.model_dump()
        if requested > self.config.max_read_chars:
            result["note"] = f"max_chars 已收敛到上限 {self.config.max_read_chars}"
        summary = f"读取 {record.relative_path}: {outcome.status.value}"
        return result, outcome.status is ReadStatus.OK, summary

    def _search_files(self, args: SearchFilesArgs) -> tuple[dict[str, Any], bool, str]:
        query_key = args.query.casefold()
        keywords = [k.casefold() for k in args.query.split() if k] or [query_key]
        matches: list[dict[str, Any]] = []
        scanned_names = 0
        for record in sorted(self.session.files.values(), key=lambda r: r.relative_path):
            scanned_names += 1
            name_hit = args.scope in ("name", "auto") and (
                all(k in record.relative_path.casefold() for k in keywords)
                or query_key in record.relative_path.casefold()
            )
            content_hit = False
            snippet = None
            content = self._content_cache.get(record.id)
            if content is not None and args.scope in ("content", "auto"):
                content_key = content.casefold()
                content_hit = all(k in content_key for k in keywords) or query_key in content_key
                if content_hit:
                    first = keywords[0] if keywords[0] in content_key else query_key
                    pos = content_key.find(first)
                    start = max(0, pos - 20)
                    snippet = content[start : start + 120]
            if not (name_hit or content_hit):
                continue
            existing = next((m for m in matches if m["file_id"] == record.id), None)
            if existing is None:
                match_types = (["name"] if name_hit else []) + (["content"] if content_hit else [])
                matches.append(
                    {
                        "file_id": record.id,
                        "relative_path": record.relative_path,
                        "match_types": match_types,
                        "snippet": snippet,
                    }
                )
            elif content_hit and "content" not in existing["match_types"]:
                existing["match_types"].append("content")
                existing["snippet"] = snippet or existing["snippet"]
            if len(matches) >= args.limit:
                break
        result = {
            "query": args.query,
            "matches": matches,
            "scanned_names": scanned_names,
            "scanned_contents": len(self._content_cache),
            "note": "内容匹配仅覆盖本会话已读取过的文件；未读取的文件不能声称已通过全文检索验证",
        }
        return result, True, f"搜索 {args.query!r}: {len(matches)} 个匹配"

    # ---- 隐私门 -----------------------------------------------------------

    def _consented(self, record: FileRecord) -> bool:
        if self.privacy == "authorized_content":
            return True
        if record.id in self._consents:  # ask_before_content：同一文件只问一次
            return self._consents[record.id]
        allowed = bool(self.consent_callback(record)) if self.consent_callback else False
        self._consents[record.id] = allowed
        return allowed
