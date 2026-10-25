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
    _run_migrations(engine)


_LATEST_SCHEMA_VERSION = 1


def _run_migrations(engine: Engine) -> None:
    """有版本号的显式迁移（v2 文档 §3.3）：基于 PRAGMA user_version 顺序执行。

    新建库由 create_all 直接得到最新结构，迁移步骤内部需自行判断列是否存在。
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        current = conn.execute(text("PRAGMA user_version")).scalar_one()
    while current < _LATEST_SCHEMA_VERSION:
        target = current + 1
        step = _MIGRATIONS.get(target)
        if step is not None:
            step(engine)
        with engine.begin() as conn:
            conn.execute(text(f"PRAGMA user_version = {target}"))
        current = target


def _migrate_1_add_plan_version(engine: Engine) -> None:
    """v1：plans 表增加 version 列（桌面版 API 的乐观锁）。"""
    from sqlalchemy import text

    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(plans)"))}
        if "version" not in columns:
            conn.execute(text("ALTER TABLE plans ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))


_MIGRATIONS = {1: _migrate_1_add_plan_version}


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover - 绑定回调
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
