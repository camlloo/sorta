"""SQLAlchemy Engine/Session 工厂、建表与初始化（开发文档 §9 / §12）。

MVP 仅支持 SQLite；内存库用 StaticPool 共享同一连接供测试使用。
SQLite 默认不强制外键，必须逐连接开启 PRAGMA foreign_keys。
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from sorta.errors import SortaError

_MEMORY_URLS = ("sqlite://", "sqlite:///:memory:")


def default_db_url() -> str:
    """未设置 SORTA_DB_URL 时，解析到系统数据目录（Windows: %APPDATA%/sorta/sorta.db）。"""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    db_path = base / "sorta" / "sorta.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path.as_posix()}"


def create_db_engine(url: str | None) -> Engine:
    """创建 Engine。url 为 None 时使用系统数据目录默认值。"""
    resolved = url or default_db_url()
    if not resolved.startswith("sqlite"):
        raise SortaError(f"MVP 仅支持 SQLite 存储，当前 SORTA_DB_URL: {resolved!r}")
    if resolved in _MEMORY_URLS:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        engine = create_engine(resolved)
    _enable_sqlite_foreign_keys(engine)
    return engine


def init_db(engine: Engine) -> None:
    from sorta.storage.models import Base

    Base.metadata.create_all(engine)


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover - 绑定回调
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
