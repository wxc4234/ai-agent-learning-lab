"""候选选择的配置、空间与真实授权窗口；不调用供应商或开发业务表。"""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from pydantic import SecretStr
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import Conversation, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError, set_locked_workspace_root
from app.services.model.code_embeddings import code_embedding_space_id
from app.services.workspace.files import code_batch_selection as service
from app.services.workspace.files import code_batch_summaries as summaries
from tests.assertions import require_value
from tests.model.test_code_embeddings import config
from tests.workspace.files.test_code_batch_summaries import row, scope, summaries_database
from tests.workspace.files.test_code_vector_storage import context, counts, database, save, target
from tests.workspace.files.test_code_vector_storage_validation import batch

__all__ = ["context", "database", "summaries_database", "target"]


def choose(context, active=None, **changes):
    return service.select_code_embedding_batch(**scope(context, **changes), config=active or config())


def summary(index, active=None, version="fixture-model-v1", **changes):
    active = active or config()
    return summaries.CodeEmbeddingBatchSummary(**({
        "batch_id": f"{index:032x}", "space_id": code_embedding_space_id(active, version),
        "requested_model": active.model, "response_model": version, "dimensions": active.dimensions,
        "chunk_count": 1, "truncated": False, "incomplete_reasons": (),
        "created_at": datetime(2026, 10, 9, tzinfo=timezone.utc) + timedelta(seconds=index),
    } | changes))


def install(monkeypatch, items, has_more=False):
    value = summaries.CodeEmbeddingBatchList(workspace_id="a" * 32, task_id="b" * 32, batches=tuple(items), has_more=has_more)
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return value

    monkeypatch.setattr(service, "list_code_embedding_batches", read)
    return value, calls


def select_memory(active=None):
    return service.select_code_embedding_batch(user_id=1, workspace_id="a" * 32, task_id="b" * 32, config=active or config())


@pytest.mark.parametrize("changes", [
    {"base_url": "https://another.example/v1"}, {"model": "another-model"},
    {"dimensions": 2}, {"request_dimensions": True},
])
def test_incompatible_newest_is_skipped(monkeypatch, changes):
    value, calls = install(monkeypatch, [summary(3, config(**changes)), summary(2), summary(1)])
    result = select_memory()
    assert result.status == "selected" and result.selected == value.batches[1]
    assert result.selected is not value.batches[1] and result.candidate_count == 3
    assert calls == [{"user_id": 1, "workspace_id": "a" * 32, "task_id": "b" * 32}]


def test_each_batch_report_version_and_first_match(monkeypatch):
    newest = summary(3, version="fixture-model-v3", truncated=True, incomplete_reasons=("chunk_budget",))
    install(monkeypatch, [newest, summary(2, version="fixture-model-v2")])
    result = select_memory(config(api_key=SecretStr("rotated-key")))
    assert result.selected == newest and result.selected is not newest
    assert require_value(result.selected).incomplete_reasons == ("chunk_budget",)
    assert "api_key" not in repr(asdict(result)) and "base_url" not in repr(asdict(result))


def test_report_version_must_match_fingerprint(monkeypatch):
    install(monkeypatch, [summary(1, response_model="other-version")])
    assert select_memory().status == "not_found_in_window"


@pytest.mark.parametrize("amount,has_more", [(0, False), (1, False), (20, False), (20, True)])
def test_no_match_is_scoped_to_window(monkeypatch, amount, has_more):
    install(monkeypatch, [summary(i, config(model="other")) for i in range(amount, 0, -1)], has_more)
    result = select_memory()
    assert result.status == "not_found_in_window" and result.selected is None
    assert result.candidate_count == amount and result.has_more is has_more
    assert result.limit == 20 and result.source == "code_embedding_batch_selection"


@pytest.mark.parametrize("changes", [
    {"dimensions": 0}, {"dimensions": True}, {"dimensions": 4097}, {"model": ""}, {"model": " model"},
    {"base_url": "https://user:password@example.com"}, {"api_key": SecretStr("")},
    {"request_dimensions": "yes"}, {"timeout_seconds": float("nan")},
])
def test_invalid_constructed_config_before_read(monkeypatch, changes):
    def forbidden(**kwargs):
        pytest.fail("invalid configuration reached summary read")

    monkeypatch.setattr(service, "list_code_embedding_batches", forbidden)
    with pytest.raises(service.CodeBatchSelectionError) as error:
        select_memory(config().model_copy(update=changes))
    assert str(error.value) == "invalid_code_embedding_batch_selection_config"


def test_wrong_config_type(monkeypatch):
    _, calls = install(monkeypatch, [])
    with pytest.raises(service.CodeBatchSelectionError):
        select_memory(object())
    assert calls == []


@pytest.mark.parametrize("error", [summaries.CodeBatchSummaryError("code_embedding_batch_list_failed"), RuntimeError("transaction failed")])
def test_read_errors_are_not_empty_results(monkeypatch, error):
    def fail(**kwargs):
        raise error

    monkeypatch.setattr(service, "list_code_embedding_batches", fail)
    with pytest.raises(type(error)) as caught:
        select_memory()
    assert caught.value is error


def test_real_read_only_and_connection_released(summaries_database, context, monkeypatch):
    saved = save(context, replace(batch(), truncated=True, incomplete_reasons=("chunk_budget",)))
    before = counts(summaries_database)
    original = service.code_embedding_space_id

    def fingerprint(active, version):
        assert summaries_database.pool.checkedout() == 0
        return original(active, version)

    monkeypatch.setattr(service, "code_embedding_space_id", fingerprint)
    statements = []

    def observe(conn, cursor, statement, parameters, execution_context, many):
        statements.append(statement.lower().lstrip())

    event.listen(summaries_database, "before_cursor_execute", observe)
    try:
        result = choose(context)
    finally:
        event.remove(summaries_database, "before_cursor_execute", observe)
    assert require_value(result.selected).batch_id == saved.batch_id
    assert require_value(result.selected).truncated is True
    assert all(sql.startswith("select") for sql in statements)
    assert not any("code_embedding_vectors" in sql for sql in statements)
    assert counts(summaries_database) == before


@pytest.mark.parametrize("compatible_in_window", [False, True])
def test_real_twenty_batch_window(summaries_database, context, compatible_in_window):
    oldest = save(context)
    other = config(model="other")
    for _ in range(20):
        save(context, batch(active=other), other)
    with Session(summaries_database) as session, session.begin():
        row(session, oldest.batch_id).created_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    newest = save(context) if compatible_in_window else None
    result = choose(context)
    assert result.candidate_count == 20 and result.has_more is True
    if newest is not None:
        assert require_value(result.selected).batch_id == newest.batch_id
    else:
        assert result.selected is None and result.status == "not_found_in_window"


@pytest.mark.parametrize("kind", ["owner", "conversation", "workspace", "task"])
def test_real_current_authorization_failure(summaries_database, context, target, kind):
    save(context)
    changes = {}
    with Session(summaries_database) as session, session.begin():
        if kind == "owner":
            require_value(session.scalar(select(Workspace))).user_id = target["other_id"]
        elif kind == "conversation":
            require_value(session.get(Conversation, target["conversation_pk"])).user_id = target["other_id"]
        else:
            changes["workspace_id" if kind == "workspace" else "task_id"] = "f" * 32
    with pytest.raises(WorkspaceNotAccessibleError):
        choose(context, **changes)


@pytest.mark.parametrize("kind", ["revision", "roundtrip", "unbind"])
def test_real_binding_changes(summaries_database, context, kind):
    save(context)
    assert choose(context).status == "selected"
    with Session(summaries_database) as session, session.begin():
        workspace = require_value(session.scalar(select(Workspace).with_for_update()))
        if kind == "revision":
            workspace.binding_revision += 1
        elif kind == "roundtrip":
            set_locked_workspace_root(workspace, "/other")
            set_locked_workspace_root(workspace, context.bound_root)
        else:
            set_locked_workspace_root(workspace, None)
    if kind == "unbind":
        with pytest.raises(summaries.CodeBatchSummaryError, match="code_embedding_project_unbound"):
            choose(context)
    else:
        result = choose(context)
        assert result.status == "not_found_in_window" and result.candidate_count == 0


def test_real_bad_lookahead_rejects_matching_first(summaries_database, context):
    saved = [save(context) for _ in range(21)]
    with Session(summaries_database) as session, session.begin():
        oldest = row(session, saved[0].batch_id)
        oldest.created_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        oldest.source_metadata = dict(oldest.source_metadata) | {"truncated": True}
    with pytest.raises(summaries.CodeBatchSummaryError):
        choose(context)


def test_real_transaction_exit_failure(summaries_database, context):
    save(context)

    def fail(session):
        raise RuntimeError("commit failed")

    session_class = summaries.SessionLocal.class_
    event.listen(session_class, "before_commit", fail)
    try:
        with pytest.raises(RuntimeError, match="commit failed"):
            choose(context)
    finally:
        event.remove(session_class, "before_commit", fail)
    assert summaries_database.pool.checkedout() == 0
