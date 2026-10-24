"""运行配置：默认值 + 环境变量覆盖（环境变量名见开发文档 §12.2 与 .env.example）。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

PRIVACY_MODES = ("metadata_only", "ask_before_content", "authorized_content")
DEFAULT_MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MiB（§6.3）


class ConfigError(ValueError):
    """配置值非法。"""


@dataclass(frozen=True)
class SortaConfig:
    """不可变运行配置。所有限制都是上限保护，供 scanner/readers 强制执行。"""

    max_scan_depth: int = 2
    max_scan_files: int = 200
    max_read_chars: int = 5_000
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_agent_steps: int = 8
    max_tool_calls: int = 40
    default_privacy: str = "ask_before_content"
    include_hidden: bool = False
    scan_session_ttl_seconds: int = 3_600

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SortaConfig:
        env = os.environ if env is None else env

        def get_int(name: str, default: int) -> int:
            raw = env.get(name)
            if raw is None or raw == "":
                return default
            try:
                value = int(raw)
            except ValueError as exc:
                raise ConfigError(f"{name} 必须是整数，当前值: {raw!r}") from exc
            if value <= 0:
                raise ConfigError(f"{name} 必须为正整数，当前值: {value}")
            return value

        def get_bool(name: str, default: bool) -> bool:
            raw = env.get(name)
            if raw is None or raw == "":
                return default
            return raw.strip().lower() in ("1", "true", "yes", "on")

        privacy = env.get("SORTA_DEFAULT_PRIVACY") or cls.default_privacy
        if privacy not in PRIVACY_MODES:
            raise ConfigError(
                f"SORTA_DEFAULT_PRIVACY 必须是 {'/'.join(PRIVACY_MODES)} 之一，当前值: {privacy!r}"
            )

        return cls(
            max_scan_depth=get_int("SORTA_MAX_SCAN_DEPTH", cls.max_scan_depth),
            max_scan_files=get_int("SORTA_MAX_SCAN_FILES", cls.max_scan_files),
            max_read_chars=get_int("SORTA_MAX_READ_CHARS", cls.max_read_chars),
            max_file_bytes=get_int("SORTA_MAX_FILE_BYTES", cls.max_file_bytes),
            max_agent_steps=get_int("SORTA_MAX_AGENT_STEPS", cls.max_agent_steps),
            max_tool_calls=get_int("SORTA_MAX_TOOL_CALLS", cls.max_tool_calls),
            default_privacy=privacy,
            include_hidden=get_bool("SORTA_INCLUDE_HIDDEN", cls.include_hidden),
            scan_session_ttl_seconds=get_int(
                "SORTA_SCAN_SESSION_TTL", cls.scan_session_ttl_seconds
            ),
        )
