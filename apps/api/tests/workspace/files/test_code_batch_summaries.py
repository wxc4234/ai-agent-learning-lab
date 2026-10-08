"""根夹具隔离PostgreSQL与真实本地身份；摘要不访问模型、向量或项目文件。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from time import monotonic, sleep

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import Text, cast, event, func, select, text
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.local_boundary import local_access_boundary
from app.models import CodeEmbeddingBatch, Conversation, User, Workspace
from app.repositories.workspace.code_embedding_repository import (
    CODE_BATCH_METADATA_BYTES,
    read_owned_code_embedding_batch_summaries,
)
from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
    set_locked_workspace_root,
)
from app.routers.workspace.router import router
from app.services.auth.local_identity import LOCAL_USER_ID
from app.services.model import code_embeddings, embedding_config, query_embeddings
from app.services.workspace.files import code_batch_summaries as service, code_vector_storage
from tests.assertions import require_value
from tests.model.test_code_embeddings import config
from tests.workspace.files.test_code_batch_summaries_validation import failure, forbidden
from tests.workspace.files.test_code_inventory_api import HEADERS, TOKEN
from tests.workspace.files.test_code_vector_storage import context, counts, database, save, target
from tests.workspace.files.test_code_vector_storage_validation import batch

__all__ = ["context", "database", "target"]


@pytest.fixture
def summaries_database(database, monkeypatch):
    monkeypatch.setattr(service, "SessionLocal", code_vector_storage.SessionLocal)
    # 这些依赖若被误装配，测试立即失败；保存夹具消费受控内存DTO，无模型请求。
    for module, name in (
        (embedding_config, "load_embedding_config"),
        (code_embeddings, "generate_code_embeddings"),
        (query_embeddings, "generate_query_embedding"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    return database


def scope(context, **changes):
    return {key: getattr(context, key) for key in ("user_id", "workspace_id", "task_id")} | changes


def read(context, **changes):
    return service.list_code_embedding_batches(**scope(context, **changes))


@pytest.fixture
def api(summaries_database, context, target, monkeypatch):
    monkeypatch.setattr(settings, "local_runtime_token", SecretStr(TOKEN))
    monkeypatch.setattr(dependencies, "SessionLocal", service.SessionLocal)
    with Session(summaries_database) as session, session.begin():
        owner = require_value(session.get(User, target["user_id"]))
        owner.external_id = LOCAL_USER_ID
        owner.username = owner.password_hash = None
    app = FastAPI()
    app.middleware("http")(local_access_boundary)
    app.include_router(router)
    path = f"/workspaces/{context.workspace_id}/tasks/{context.task_id}/code-embedding-batches"
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client, path


def get(api):
    return api[0].get(api[1], headers=HEADERS)


def row(session, batch_id):
    return require_value(session.scalar(select(CodeEmbeddingBatch).where(CodeEmbeddingBatch.external_id == batch_id)))


def test_real_empty_and_summary_sql_projection_close_before_return(summaries_database, context, api):
    assert get(api).json()["batches"] == []
    value = replace(batch(), truncated=True, incomplete_reasons=("chunk_budget",))
    saved = save(context, value)
    before = counts(summaries_database)
    statements = []
    listener = lambda conn, cursor, statement, params, ctx, many: statements.append(statement.lower())
    event.listen(summaries_database, "before_cursor_execute", listener)
    try:
        response = get(api)
    finally:
        event.remove(summaries_database, "before_cursor_execute", listener)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["limit"] == 20 and payload["has_more"] is False
    assert payload["workspace_id"] == context.workspace_id and payload["task_id"] == context.task_id
    item = payload["batches"][0]
    assert item["batch_id"] == saved.batch_id and item["space_id"] == saved.space_id
    assert item["requested_model"] == "fixture-model" and item["response_model"] == "fixture-model-v1"
    assert item["dimensions"] == 3 and item["chunk_count"] == 1
    assert item["truncated"] is True and item["incomplete_reasons"] == ["chunk_budget"]
    assert "/preserved" not in response.text and "files" not in item and "prompt_tokens" not in item
    assert not any("code_embedding_vectors" in statement for statement in statements)
    writes = [statement for statement in statements if not statement.lstrip().startswith("select")]
    assert writes and all(statement.lstrip().startswith("insert into users") for statement in writes)
    assert any("case when" in statement and "limit" in statement for statement in statements)
    assert counts(summaries_database) == before and summaries_database.pool.checkedout() == 0


@pytest.mark.parametrize("amount", [20, 21, 23])
def test_real_fixed_limit_and_stable_creation_order(summaries_database, context, amount):
    saved = [save(context) for _ in range(amount)]
    instant = datetime(2026, 10, 8, tzinfo=timezone.utc)
    with Session(summaries_database) as session, session.begin():
        for index, value in enumerate(saved):
            row(session, value.batch_id).created_at = instant + timedelta(seconds=index // 3)
    expected = sorted([(instant + timedelta(seconds=index // 3), value.batch_id) for index, value in enumerate(saved)], reverse=True)
    result = read(context)
    assert [(item.created_at, item.batch_id) for item in result.batches] == expected[:20]
    assert result.has_more is (amount > 20) and result.limit == 20
    with Session(summaries_database) as session:
        assert len(read_owned_code_embedding_batch_summaries(session, **scope(context))) == min(amount, 21)


def test_current_task_and_model_spaces_are_kept_separate(summaries_database, context):
    first = save(context)
    active = config(dimensions=2)
    second = save(context, batch(active=active), active)
    sibling = code_vector_storage.capture_code_embedding_target(**scope(context, task_id="d" * 32))
    sibling_batch = save(sibling, replace(batch(), task_id=sibling.task_id))
    result = read(context)
    assert {item.batch_id for item in result.batches} == {first.batch_id, second.batch_id}
    assert {item.dimensions for item in result.batches} == {2, 3}
    assert {item.batch_id for item in read(sibling).batches} == {sibling_batch.batch_id}
    # 摘要无需当前供应商配置，不能声称这两个空间均可供当前配置查询。
    assert len({item.space_id for item in result.batches}) == 2


@pytest.mark.parametrize("kind", ["owner", "conversation", "missing_conversation", "missing_task", "workspace", "task"])
def test_real_current_authorization_denies_even_empty_lists(summaries_database, context, target, api, kind):
    changes = {}
    with Session(summaries_database) as session, session.begin():
        if kind == "owner":
            require_value(session.scalar(select(Workspace))).user_id = target["other_id"]
        elif kind == "conversation":
            require_value(session.get(Conversation, target["conversation_pk"])).user_id = target["other_id"]
        elif kind == "missing_conversation":
            session.delete(require_value(session.get(Conversation, target["conversation_pk"])))
        elif kind == "missing_task":
            session.delete(require_value(session.get(Conversation, target["conversation_pk"])))
            from app.models import Task
            session.delete(require_value(session.get(Task, target["task_pk"])))
        else:
            changes["workspace_id" if kind == "workspace" else "task_id"] = "f" * 32
    with pytest.raises(WorkspaceNotAccessibleError):
        read(context, **changes)
    path = api[1]
    for key, value in changes.items():
        path = path.replace(getattr(context, key), value)
    failure(api[0].get(path, headers=HEADERS), 404, "workspace_not_accessible")


@pytest.mark.parametrize("kind", ["revision", "path", "roundtrip", "unbind"])
def test_old_binding_hidden_and_new_binding_has_own_batches(summaries_database, context, api, kind):
    old = save(context)
    with Session(summaries_database) as session, session.begin():
        workspace = require_value(session.scalar(select(Workspace).with_for_update()))
        if kind == "revision":
            workspace.binding_revision += 1
        elif kind == "roundtrip":
            set_locked_workspace_root(workspace, "/other")
            set_locked_workspace_root(workspace, context.bound_root)
        else:
            set_locked_workspace_root(workspace, None if kind == "unbind" else "/other")
    if kind == "unbind":
        failure(get(api), 409, "code_embedding_project_unbound")
        return
    assert read(context).batches == () and get(api).json()["batches"] == []
    current = code_vector_storage.capture_code_embedding_target(**scope(context))
    new = save(current)
    assert [item.batch_id for item in read(current).batches] == [new.batch_id]
    assert old.batch_id != new.batch_id and counts(summaries_database)[1] == 2


@pytest.mark.parametrize("kind", [
    "scope", "space", "dimensions", "model", "coverage", "unknown_reason", "duplicate_reason",
    "usage", "file", "extra", "parser", "missing", "oversized",
])
def test_real_bad_metadata_is_failure_without_partial_list(summaries_database, context, api, kind):
    broken = save(context)
    save(context)  # 即使另一项合法，也不能先发布部分成功。
    with Session(summaries_database) as session, session.begin():
        stored = row(session, broken.batch_id)
        metadata = dict(stored.source_metadata)
        changes = {
            "scope": {"task_id": "f" * 32}, "space": {"embedding_space_id": "f" * 64},
            "dimensions": {"dimensions": 2}, "model": {"requested_model": "PRIVATE"},
            "coverage": {"truncated": True}, "unknown_reason": {"truncated": True, "incomplete_reasons": ["PRIVATE"]},
            "duplicate_reason": {"truncated": True, "incomplete_reasons": ["chunk_budget", "chunk_budget"]},
            "usage": {"prompt_tokens": 9, "total_tokens": 7}, "file": {"files": []},
            "extra": {"private_key": "PRIVATE"}, "parser": {"chunk_parser": "PRIVATE\n"},
            "oversized": {"private_padding": "PRIVATE" * CODE_BATCH_METADATA_BYTES},
        }
        if kind == "missing":
            metadata.pop("files")
        else:
            metadata.update(changes[kind])
        stored.source_metadata = metadata
    with pytest.raises(service.CodeBatchSummaryError, match="code_embedding_batch_list_failed"):
        read(context)
    failure(get(api), 500, "code_embedding_batch_list_failed")


@pytest.mark.parametrize("extra", [0, 1])
def test_database_metadata_transfer_byte_boundary(summaries_database, context, extra):
    saved = save(context)
    with Session(summaries_database) as session, session.begin():
        stored = row(session, saved.batch_id)
        metadata = dict(stored.source_metadata) | {"padding": ""}
        stored.source_metadata = metadata
        session.flush()
        size = require_value(session.scalar(select(func.octet_length(cast(CodeEmbeddingBatch.source_metadata, Text)))))
        stored.source_metadata = metadata | {"padding": "x" * (CODE_BATCH_METADATA_BYTES - size + extra)}
    with Session(summaries_database) as session:
        item = read_owned_code_embedding_batch_summaries(session, **scope(context))[0]
        assert item["metadata_bytes"] == CODE_BATCH_METADATA_BYTES + extra
        assert (item["source_metadata"] is None) is bool(extra)
    with pytest.raises(service.CodeBatchSummaryError):
        read(context)  # 等于上限仍因未知padding字段失败，不伪装有效来源。


def test_bad_lookahead_not_hidden_by_twenty_item_cut(summaries_database, context):
    saved = [save(context) for _ in range(21)]
    with Session(summaries_database) as session, session.begin():
        oldest = row(session, saved[0].batch_id)
        oldest.created_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        oldest.source_metadata = dict(oldest.source_metadata) | {"truncated": True}
    with pytest.raises(service.CodeBatchSummaryError):
        read(context)


def test_transaction_exit_failure_never_returns_success(summaries_database, context):
    save(context)
    def fail(session):
        raise RuntimeError("PRIVATE commit")
    session_class = service.SessionLocal.class_
    event.listen(session_class, "before_commit", fail)
    try:
        with pytest.raises(RuntimeError, match="PRIVATE commit"):
            read(context)
    finally:
        event.remove(session_class, "before_commit", fail)
    assert summaries_database.pool.checkedout() == 0


@pytest.mark.parametrize("kind", ["owner", "binding"])
def test_query_waits_for_current_lock_then_observes_committed_change(summaries_database, context, target, kind):
    save(context)
    with Session(summaries_database) as writer, writer.begin():
        workspace = require_value(writer.scalar(select(Workspace).with_for_update()))
        pid = writer.scalar(text("select pg_backend_pid()"))
        executor = ThreadPoolExecutor(max_workers=1)
        pending = executor.submit(read, context)
        try:
            deadline = monotonic() + 5
            blocked = False
            # 观察连接每次读取当前活动；不复用写事务中的统计快照猜测等待。
            with summaries_database.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                while monotonic() < deadline:
                    blocked = bool(observer.scalar(text("select exists(select 1 from pg_stat_activity where :pid = any(pg_blocking_pids(pid)))"), {"pid": pid}))
                    if blocked:
                        break
                    if pending.done():
                        pending.result()  # 非预期早退须显示原始失败，而不是只报告没观察到锁。
                    sleep(0.02)
            assert blocked and not pending.done()
            if kind == "owner":
                workspace.user_id = target["other_id"]
            else:
                set_locked_workspace_root(workspace, "/other")
            writer.commit()
            if kind == "owner":
                with pytest.raises(WorkspaceNotAccessibleError):
                    pending.result(timeout=5)
            else:
                assert pending.result(timeout=5).batches == ()
        finally:
            if writer.in_transaction():
                writer.rollback()
            executor.shutdown(wait=True)
