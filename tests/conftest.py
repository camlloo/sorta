from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sorta.agent.schemas import ExecutionPlan, FileOperation
from sorta.api.main import create_app
from sorta.config import SortaConfig
from sorta.planner.schemas import compute_plan_hash


@pytest.fixture
def app(tmp_path):
    config = SortaConfig(db_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    application = create_app(config=config)
    yield application
    application.state.jobs._pool.shutdown(wait=True)
    application.state.service.store.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as connection:
        connection.headers["X-SORTA-Token"] = app.state.api_token
        yield connection


@pytest.fixture
def service(app):
    return app.state.service


@pytest.fixture
def make_plan(service, tmp_path):
    def create(*, count=1, action="move", confidence="high"):
        root = tmp_path / "files"
        root.mkdir(exist_ok=True)
        for i in range(count):
            (root / f"file{i}.txt").write_text(f"document {i}", encoding="utf-8")
        workspace = service.create_workspace(str(root))
        rows = service.list_files(workspace.scan_id)
        plan = ExecutionPlan(
            scan_id=workspace.scan_id,
            summary="Organize test documents",
            operations=[
                FileOperation(
                    source_id=row["file_id"],
                    destination=f"organized/{row['path']}",
                    action=action,
                    reason="test",
                    confidence=confidence,
                )
                for row in rows
            ],
        )
        plan_id = service.store.save_plan(
            plan.model_dump(mode="json"), compute_plan_hash(plan), status="draft"
        )
        return Path(workspace.root), service.get_plan_detail(plan_id)

    return create


def approve(service, plan):
    return service.approve_plan(
        plan.plan_id, expected_version=plan.version, expected_plan_hash=plan.plan_hash
    )["approval_id"]
