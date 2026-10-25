import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from sorta.agent.llm import LLMResponse
from sorta.application.jobs import JobManager
from sorta.application.service import SortaService
from sorta.config import SortaConfig
from sorta.errors import SortaError
from sorta.executor.engine import execution_id_for
from sorta.executor.safety import ensure_parent_dirs, safe_copy
from sorta.scanner.registry import FileRegistry
from sorta.scanner.scanner import Scanner

from .conftest import approve


@pytest.mark.parametrize("payload", [{}, {"root": ""}, {"root": 42}])
def test_invalid_requests_return_validation_envelope(client, payload):
    response = client.post("/api/v1/workspaces", json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_auth_required(client):
    response = client.get("/api/v1/settings", headers={"X-SORTA-Token": "wrong"})
    assert response.status_code == 401


def test_cross_site_session_rejected(client):
    assert (
        client.get("/api/v1/session", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    )


def test_bad_edit_lengths_return_client_error(client, make_plan):
    _, plan = make_plan()
    response = client.patch(
        f"/api/v1/plans/{plan.plan_id}",
        json={
            "keep": [],
            "destinations": [],
            "expected_version": plan.version,
        },
    )
    assert response.status_code == 400, response.text


@pytest.mark.parametrize("action", ["move", "copy"])
def test_execute_api_matches_history_contract(client, service, make_plan, action):
    root, plan = make_plan(action=action)
    response = client.post("/api/v1/executions", json={"approval_id": approve(service, plan)})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data == client.get(f"/api/v1/executions/{data['execution_id']}").json()
    assert data["operations"][0]["index"] == 0
    assert isinstance(data["started_at"], (float, int))
    assert (root / "organized/file0.txt").read_text() == "document 0"


@pytest.mark.parametrize("action", ["move", "copy"])
def test_undo_updates_execution_history(service, make_plan, action):
    root, plan = make_plan(action=action)
    result = service.execute_approved(approve(service, plan))
    undo = service.undo_execution(result.execution_id)
    assert undo.status == "undone"
    assert service.get_execution(result.execution_id)["status"] == "undone"
    assert (root / "file0.txt").read_text() == "document 0"
    assert not (root / "organized/file0.txt").exists()
    with pytest.raises(SortaError):
        service.undo_execution(result.execution_id)


def test_undo_keeps_modified_destination(service, make_plan):
    root, plan = make_plan()
    result = service.execute_approved(approve(service, plan))
    destination = root / "organized/file0.txt"
    destination.write_text("changed by user", encoding="utf-8")
    assert service.undo_execution(result.execution_id).status == "partially_undone"
    assert destination.read_text() == "changed by user"
    assert service.get_execution(result.execution_id)["status"] == "partially_undone"


def test_execution_after_service_restart_restores_scan(service, make_plan):
    _, plan = make_plan()
    approval_id = approve(service, plan)
    restarted = SortaService(config=service.config, store=service.store)
    assert restarted.execute_approved(approval_id).status == "completed"


def test_engine_uses_requested_approval(service, make_plan):
    _, plan = make_plan()
    first = approve(service, plan)
    approve(service, plan)
    result = service.execute_approved(first)
    assert result.execution_id == execution_id_for(plan.plan_hash, first)


def test_same_content_edit_invalidates_old_approval(service, make_plan):
    root, plan = make_plan()
    approval_id = approve(service, plan)
    service.edit_plan(
        plan.plan_id,
        keep=[True],
        destinations=[plan.operations[0]["destination"]],
        expected_version=plan.version,
    )
    with pytest.raises(SortaError):
        service.execute_approved(approval_id)
    assert (root / "file0.txt").exists()
    assert not service.list_executions()


def test_cancel_validated_plan(service, make_plan):
    _, plan = make_plan()
    service.store.update_plan_status(plan.plan_id, "validated")
    service.reject_plan(plan.plan_id)
    assert service.store.get_plan(plan.plan_id).status == "rejected"


def test_finished_plan_cannot_be_edited(service, make_plan):
    _, plan = make_plan()
    service.execute_approved(approve(service, plan))
    with pytest.raises(SortaError):
        service.edit_plan(
            plan.plan_id, keep=[True], destinations=["other.txt"], expected_version=plan.version
        )


def test_operation_oserror_does_not_abandon_batch(service, make_plan, monkeypatch):
    root, plan = make_plan(count=2)
    from sorta.executor import engine

    original = engine.safe_move

    def sometimes_fails(source, destination):
        if source.name == "file0.txt":
            raise PermissionError("file is locked")
        return original(source, destination)

    monkeypatch.setattr(engine, "safe_move", sometimes_fails)
    result = service.execute_approved(approve(service, plan))
    assert result.status == "partial_failed"
    assert [o.status for o in result.operations] == ["failed", "succeeded"]
    assert (root / "organized/file1.txt").exists()


def test_revision_prompt_includes_identifiers(service, make_plan):
    _, plan = make_plan()
    response = service.store.get_plan(plan.plan_id).plan_json
    llm = Mock()
    llm.chat.return_value = LLMResponse(content=json.dumps(response))
    detail = service.revise_plan(
        plan.plan_id, "keep names", model="test", llm=llm, expected_version=plan.version
    )
    prompt = json.dumps(llm.chat.call_args.kwargs["messages"])
    assert plan.scan_id in prompt
    assert plan.operations[0]["source_id"] in prompt
    assert detail.version == plan.version + 1


def test_canceled_queued_job_never_runs():
    manager = JobManager(max_workers=1)
    entered = threading.Event()
    release = threading.Event()
    called = threading.Event()

    def block(*_):
        entered.set()
        assert release.wait(3)

    try:
        manager.submit("blocking", block)
        assert entered.wait(3)
        queued = manager.submit("canceled", lambda *_: called.set())
        assert manager.cancel(queued)
    finally:
        release.set()
        manager._pool.shutdown(wait=True)
    assert not called.is_set()
    assert manager.get(queued)["status"] == "canceled"


def test_destination_directory_rechecks_boundary(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(SortaError):
        ensure_parent_dirs(root, "../outside/file.txt")
    assert not (tmp_path / "outside").exists()


def test_copy_never_overwrites_racing_destination(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    target = tmp_path / "target.txt"
    source.write_text("source")
    target.write_text("user data")
    monkeypatch.setattr("os.path.lexists", lambda _: False)
    with pytest.raises(SortaError):
        safe_copy(source, target)
    assert target.read_text() == "user data"


def test_duplicate_execution_never_runs_twice(service, make_plan):
    _, plan = make_plan()
    approval_id = approve(service, plan)
    service.execute_approved(approval_id)
    with pytest.raises(SortaError):
        service.execute_approved(approval_id)
    assert len(service.list_executions()) == 1


def test_modified_source_blocks_execution(service, make_plan):
    root, plan = make_plan()
    approval_id = approve(service, plan)
    (root / "file0.txt").write_text("modified after approval")
    with pytest.raises(SortaError):
        service.execute_approved(approval_id)
    assert not (root / "organized").exists()
    assert not service.list_executions()


def test_destination_created_after_approval_is_preserved(service, make_plan):
    root, plan = make_plan()
    approval_id = approve(service, plan)
    (root / "organized").mkdir()
    target = root / "organized/file0.txt"
    target.write_text("existing user document")
    result = service.execute_approved(approval_id)
    assert result.status == "failed"
    assert target.read_text() == "existing user document"
    assert (root / "file0.txt").exists()


def test_undo_does_not_overwrite_occupied_source(service, make_plan):
    root, plan = make_plan()
    result = service.execute_approved(approve(service, plan))
    original = root / "file0.txt"
    original.write_text("new user file")
    assert service.undo_execution(result.execution_id).status == "partially_undone"
    assert original.read_text() == "new user file"
    assert (root / "organized/file0.txt").exists()


def test_failed_copy_cleans_up_partial_target(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    target = tmp_path / "target.txt"
    source.write_text("source document")

    def fail_copy(incoming, outgoing):
        outgoing.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr("shutil.copyfileobj", fail_copy)
    with pytest.raises(OSError):
        safe_copy(source, target)
    assert source.read_text() == "source document"
    assert not target.exists()


@pytest.mark.parametrize("privacy", ["metadata_only", "ask_before_content"])
def test_summary_respects_content_consent(service, make_plan, privacy):
    _, plan = make_plan()
    service.save_settings(privacy=privacy)
    llm = Mock()
    with pytest.raises(SortaError) as error:
        service.summarize_file(plan.scan_id, plan.operations[0]["source_id"], model="test", llm=llm)
    assert error.value.code == "CONSENT_REQUIRED"
    llm.chat.assert_not_called()


def test_concurrent_edits_have_one_winner(service, make_plan):
    _, plan = make_plan()
    barrier = threading.Barrier(2)

    def edit(destination):
        barrier.wait(timeout=3)
        try:
            service.edit_plan(
                plan.plan_id, keep=[True], destinations=[destination], expected_version=plan.version
            )
            return "saved"
        except SortaError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ["a.txt", "b.txt"]))
    assert sorted(results) == ["PLAN_VERSION_CONFLICT", "saved"]
    assert service.store.get_plan(plan.plan_id).version == plan.version + 1


def test_canceled_revision_is_not_saved(service, make_plan):
    _, plan = make_plan()
    event = threading.Event()
    llm = Mock()

    def cancel_after_response(**_):
        event.set()
        return LLMResponse(content=json.dumps(service.store.get_plan(plan.plan_id).plan_json))

    llm.chat.side_effect = cancel_after_response
    with pytest.raises(SortaError) as error:
        service.revise_plan(
            plan.plan_id,
            "revise",
            expected_version=plan.version,
            model="test",
            llm=llm,
            cancel_event=event,
        )
    assert error.value.code == "OPERATION_CANCELED"
    assert service.store.get_plan(plan.plan_id).version == plan.version


def test_revision_job_contract_and_conversation(client, app, service, make_plan):
    _, plan = make_plan()
    conv = service.create_conversation(plan.scan_id)
    service.save_settings(model="test", api_key="local-test-key")
    llm = Mock()
    llm.chat.return_value = LLMResponse(
        content=json.dumps(service.store.get_plan(plan.plan_id).plan_json)
    )
    app.state.llm_factory = lambda *_: llm
    response = client.post(
        f"/api/v1/plans/{plan.plan_id}/revisions",
        json={
            "instruction": "keep original",
            "expected_version": plan.version,
            "conversation_id": conv["id"],
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)
    assert job["status"] == "completed", job
    assert job["result"]["task_type"] == "organize"
    assert job["result"]["version"] == plan.version + 1
    messages = service.get_messages(conv["id"])
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "keep original"


def test_stream_model_failure_is_reported_as_error_event(client, app, service, make_plan):
    _, plan = make_plan()
    service.save_settings(model="test", api_key="local-test-key")
    llm = Mock()
    llm.stream_chat.side_effect = RuntimeError("provider disconnected")
    app.state.llm_factory = lambda *_: llm
    response = client.post(
        "/api/v1/tasks/stream",
        json={
            "scan_id": plan.scan_id,
            "request": "describe files",
            "task_type": "ask",
        },
    )
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert events[-1]["type"] == "error"
    assert events[-1]["message"] == "provider disconnected"


def test_stream_rejects_unknown_task_type(client):
    response = client.post(
        "/api/v1/tasks/stream",
        json={
            "scan_id": "scan-test",
            "request": "test",
            "task_type": "invalid",
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize("answer", ["null", "{}", "[]", "not json"])
def test_unclear_classification_does_not_start_organizing(service, answer):
    llm = Mock()
    llm.chat.return_value = LLMResponse(content=answer)
    assert service._classify_task("ambiguous", model="test", llm=llm)["task_type"] == "clarify"


@pytest.mark.parametrize("answer", ["null", "42", "{}", "[null]"])
def test_malformed_search_keywords_do_not_crash(service, make_plan, answer):
    _, plan = make_plan()
    llm = Mock()
    llm.chat.return_value = LLMResponse(content=answer)
    result = service.run_task(
        plan.scan_id, "file0", task_type="find", privacy="metadata_only", model="test", llm=llm
    )
    assert len(result["matches"]) == 1


def test_unknown_api_route_is_not_spa_html(client):
    response = client.get("/api/v1/unknown")
    assert response.status_code == 404


def test_scan_pagination_keeps_ids_and_skips_hidden(tmp_path):
    (tmp_path / ".private.txt").write_text("private")
    for name in ["a.txt", "b.txt", "c.txt"]:
        (tmp_path / name).write_text(name)
    registry = FileRegistry()
    scanner = Scanner(registry, SortaConfig(max_scan_files=2))
    first = scanner.scan(tmp_path)
    second = scanner.scan(tmp_path, cursor=first.next_cursor)
    assert first.has_more and not second.has_more
    assert [f.relative_path for f in first.files + second.files] == ["a.txt", "b.txt", "c.txt"]
    assert len(registry.get_session(first.scan_id).files) == 3


def test_cleared_base_url_uses_official_endpoint(service, monkeypatch):
    monkeypatch.setenv("SORTA_BASE_URL", "https://old-provider.invalid/v1")
    assert service.get_llm_config()["base_url"] == "https://old-provider.invalid/v1"
    service.save_settings(base_url="")
    assert service.get_llm_config()["base_url"] == ""


@pytest.mark.parametrize("data", ["null", "[]", '"text"'])
def test_malformed_settings_file_does_not_break_startup(service, data):
    service._settings_file().write_text(data, encoding="utf-8")
    assert service.get_settings()["privacy"] == service.config.default_privacy
