"""临时源码、真实隔离PostgreSQL与受控HTTPX；不调用真实供应商。"""

import asyncio
from dataclasses import asdict, replace
from hashlib import sha256
import json

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import Conversation, Task, Workspace
from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
    set_locked_workspace_root,
)
from app.services.model import code_embeddings
from app.services.model.embedding_config import EmbeddingError
from app.services.workspace.directory import workspace_path
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.services.workspace.files import (
    code_batch_generation as service,
    code_vector_storage as storage,
    workspace_file,
    workspace_listing,
)
from app.services.workspace.files.python_symbols import PythonSymbolsError
from tests.assertions import require_value
from tests.model.test_code_embeddings import TrackedStream, config, response
from tests.model.test_query_embeddings import closed_clients
from tests.workspace.files.test_code_vector_storage import database, counts, target

# 复用根随机数据库/私有schema及会话/客户端追踪，不连接开发业务表。
__all__ = ["closed_clients", "database", "target"]


@pytest.fixture
def project(database, target, tmp_path, monkeypatch):
    root = tmp_path.resolve() / "project"
    root.mkdir()
    (root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (root / "ignored.py").write_text("INVALID SOURCE_PRIVATE", encoding="utf-8")
    (root / ".env").write_text("KEY_PRIVATE", encoding="utf-8")
    (root / "safe.py").write_bytes(
        b"import os\r\ndef sample():\r\n    return 'SOURCE_PRIVATE'\r\n"
    )
    with Session(database) as session, session.begin():
        set_locked_workspace_root(
            require_value(session.scalar(select(Workspace))), str(root)
        )
    monkeypatch.setattr(workspace_path, "SessionLocal", storage.SessionLocal)
    return root


def scope(target):
    return {key: target[key] for key in ("user_id", "workspace_id", "task_id")}


def invoke(target, *, handler=None, active=None, **overrides):
    def default(request):
        return httpx.Response(
            200, json=response(len(json.loads(request.content)["input"]))
        )

    return asyncio.run(
        service.generate_and_save_code_batch(
            **(scope(target) | overrides),
            config=active or config(),
            transport=httpx.MockTransport(handler or default),
        )
    )


def forbidden(*args, **kwargs):
    raise AssertionError("rejected generation reached filesystem/HTTP/storage")


def mutate(database, target, root, kind):
    # 独立连接真实提交，证明模型等待没有持有归属锁或事务。
    with Session(database) as session, session.begin():
        workspace = require_value(session.scalar(select(Workspace).with_for_update()))
        if kind == "owner":
            workspace.user_id = target["other_id"]
        elif kind == "conversation":
            require_value(
                session.get(Conversation, target["conversation_pk"])
            ).user_id = target["other_id"]
        elif kind == "missing-conversation":
            session.delete(
                require_value(session.get(Conversation, target["conversation_pk"]))
            )
        elif kind == "task-delete":
            session.delete(require_value(session.get(Task, target["task_pk"])))
        elif kind == "revision":
            workspace.binding_revision += 1
        elif kind == "roundtrip":
            set_locked_workspace_root(workspace, str(root.parent / "other"))
            set_locked_workspace_root(workspace, str(root))
        else:
            set_locked_workspace_root(
                workspace, None if kind == "unbind" else str(root.parent / "other")
            )


@pytest.mark.parametrize("usage", [True, False, "zero"])
def test_read_generate_commit_returns_public_facts_and_real_coverage(
    database, target, project, closed_clients, monkeypatch, usage
):
    original_read = workspace_file._read_resolved_file
    original_list = workspace_listing._list_resolved_directory
    before = {file.name: file.read_bytes() for file in project.iterdir()}
    calls, statements = [], []

    def read(path):
        assert database.pool.checkedout() == 0
        assert path.name not in {"ignored.py", ".env"}
        return original_read(path)

    def listing(path):
        assert database.pool.checkedout() == 0
        return original_list(path)

    def collect(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT"):
            assert closed_clients and all(client.is_closed for client in closed_clients)
        statements.append(statement)

    async def handler(request):
        assert database.pool.checkedout() == 0
        await asyncio.sleep(0)
        assert database.pool.checkedout() == 0
        payload = json.loads(request.content)
        assert payload == {
            "model": "fixture-model",
            "input": ["def sample():\n    return 'SOURCE_PRIVATE'\n"],
            "encoding_format": "float",
        }
        assert str(project) not in request.content.decode()
        assert "workspace_id" not in payload and "task_id" not in payload
        calls.append(request)
        value = response(usage=usage is True)
        if usage == "zero":
            value["usage"] = {"prompt_tokens": 0, "total_tokens": 0}
        return httpx.Response(200, json=value)

    monkeypatch.setattr(workspace_file, "_read_resolved_file", read)
    monkeypatch.setattr(workspace_listing, "_list_resolved_directory", listing)
    event.listen(database, "before_cursor_execute", collect)
    try:
        result = invoke(target, handler=handler)
    finally:
        event.remove(database, "before_cursor_execute", collect)
    assert len(calls) == result.request_count == len(closed_clients) == 1
    assert counts(database) == (1, 1, 1)
    assert result.batch.chunk_count == 1 and not result.batch.truncated
    assert result.source == "authorized_code_batch_generation"
    assert (
        result.coverage.parsed_files
        == result.coverage.examined_symbols
        == result.coverage.generated_chunks
        == 1
    )
    assert (
        result.coverage.definition_lines == 2
        and result.coverage.excluded_module_lines == 1
    )
    assert result.coverage.excluded_counts == {"hidden": 2, "gitignore": 1}
    expected = (
        (2, 2) if usage is True else ((0, 0) if usage == "zero" else (None, None))
    )
    assert (result.prompt_tokens, result.total_tokens) == expected
    captured = storage.capture_code_embedding_target(**scope(target))
    stored = storage.load_code_embedding_batch(
        **scope(target),
        batch_id=result.batch.batch_id,
        config=config(),
        response_model=result.response_model,
    )
    assert stored.batch == result.batch
    assert stored.chunks[0]["symbol"]["sha256"] == sha256(before["safe.py"]).hexdigest()
    assert stored.chunks[0]["text"] == "def sample():\n    return 'SOURCE_PRIVATE'\n"
    assert captured.bound_root == str(project)
    assert "SOURCE_PRIVATE" not in repr(asdict(result)) and "KEY_PRIVATE" not in repr(
        result
    )
    assert str(project) not in repr(result) and "provider.invalid" not in repr(result)
    assert "EMBED_PRIVATE" not in repr(result) and "vector" not in asdict(result)
    assert any(
        "code_embedding_vectors" in sql and sql.lstrip().upper().startswith("INSERT")
        for sql in statements
    )
    assert {file.name: file.read_bytes() for file in project.iterdir()} == before


@pytest.mark.parametrize("dimensions", [1, 4096])
def test_dimension_boundaries_are_generated_and_persisted(
    database, target, project, dimensions
):
    def handler(request):
        value = response()
        value["data"][0]["embedding"] = [0.25] * dimensions
        return httpx.Response(200, json=value)

    result = invoke(target, active=config(dimensions=dimensions), handler=handler)
    assert result.batch.dimensions == dimensions and counts(database) == (1, 1, 1)


@pytest.mark.parametrize("count", [9, 20, 21])
def test_multiple_requests_and_truncated_selection_keep_generated_counts(
    database, target, project, count
):
    (project / "safe.py").write_text(
        "import os\n"
        + "".join(f"def item_{index}(): return {index}\n" for index in range(count)),
        encoding="utf-8",
    )
    calls = []

    def handler(request):
        assert database.pool.checkedout() == 0
        calls.append(request)
        return httpx.Response(
            200, json=response(len(json.loads(request.content)["input"]))
        )

    result = invoke(target, handler=handler)
    assert result.batch.chunk_count == min(count, 20)
    assert result.coverage.generated_chunks == count
    assert result.request_count == len(calls) == (2 if count == 9 else 3)
    assert result.prompt_tokens == result.total_tokens == min(count, 20) * 2
    assert result.batch.truncated is (count == 21)
    assert result.coverage.incomplete_reasons == (
        ("chunk_budget",) if count == 21 else ()
    )
    assert counts(database) == (1, 1, min(count, 20))


def test_explicit_calls_append_without_overwriting_old_batches(
    database, target, project
):
    first = invoke(target)
    (project / "safe.py").write_text("def changed(): return 2\n", encoding="utf-8")
    second = invoke(target)
    assert first.batch.batch_id != second.batch.batch_id
    assert first.batch.space_id == second.batch.space_id and counts(database) == (
        1,
        2,
        2,
    )
    stored = storage.load_code_embedding_batch(
        **scope(target),
        batch_id=first.batch.batch_id,
        config=config(),
        response_model=first.response_model,
    )
    assert "SOURCE_PRIVATE" in stored.chunks[0]["text"]


@pytest.mark.parametrize(
    "kind",
    [
        "foreign-user",
        "missing-workspace",
        "missing-task",
        "owner",
        "conversation",
        "missing-conversation",
        "unbind",
    ],
)
def test_initial_authorization_failure_never_reads_or_sends(
    database, target, project, monkeypatch, kind
):
    overrides = {}
    if kind == "foreign-user":
        overrides["user_id"] = target["other_id"]
    elif kind == "missing-workspace":
        overrides["workspace_id"] = "f" * 32
    elif kind == "missing-task":
        overrides["task_id"] = "f" * 32
    else:
        mutate(database, target, project, kind)
    monkeypatch.setattr(workspace_path, "_resolve_bound_path", forbidden)
    error = (
        storage.CodeVectorStorageError
        if kind == "unbind"
        else WorkspaceNotAccessibleError
    )
    with pytest.raises(error):
        invoke(target, handler=forbidden, **overrides)
    assert counts(database) == (0, 0, 0)


@pytest.mark.parametrize("kind", ["syntax", "empty", "config"])
def test_source_or_configuration_failure_does_not_construct_http_or_store(
    database, target, project, monkeypatch, closed_clients, kind
):
    if kind == "config":

        def fail_config():
            raise EmbeddingError("embedding_not_configured", "Embedding未配置")

        monkeypatch.setattr(service, "load_embedding_config", fail_config)
        monkeypatch.setattr(service, "build_python_code_chunks", forbidden)
        with pytest.raises(EmbeddingError, match="Embedding未配置"):
            asyncio.run(service.generate_and_save_code_batch(**scope(target)))
    else:
        (project / "safe.py").write_text(
            "def SOURCE_PRIVATE(:\n" if kind == "syntax" else "import os\n",
            encoding="utf-8",
        )
        error = (
            PythonSymbolsError if kind == "syntax" else storage.CodeVectorStorageError
        )
        with pytest.raises(error):
            invoke(target, handler=forbidden)
    assert closed_clients == [] and counts(database) == (0, 0, 0)


@pytest.mark.parametrize("kind", ["root", "revision", "roundtrip", "owner"])
@pytest.mark.parametrize("stage", ["before-root", "before-source", "after-chunks"])
def test_target_changes_during_reading_or_before_send_refuse_entire_generation(
    database, target, project, monkeypatch, kind, stage
):
    changed = False
    if stage == "before-root":
        original = service.capture_code_embedding_target

        def capture(**kwargs):
            nonlocal changed
            result = original(**kwargs)
            if not changed:
                changed = True
                mutate(database, target, project, kind)
            return result

        monkeypatch.setattr(service, "capture_code_embedding_target", capture)
        monkeypatch.setattr(workspace_path, "_resolve_bound_path", forbidden)
    elif stage == "before-source":
        original = workspace_file.read_task_text_file

        def read(**kwargs):
            nonlocal changed
            if kwargs["relative_path"] == "safe.py" and not changed:
                changed = True
                mutate(database, target, project, kind)
            return original(**kwargs)

        from app.services.workspace.files import code_inventory

        monkeypatch.setattr(code_inventory, "read_task_text_file", read)
        original_open = workspace_file._read_resolved_file

        def opening(path):
            assert path.name != "safe.py"
            return original_open(path)

        monkeypatch.setattr(workspace_file, "_read_resolved_file", opening)
    else:
        original = service.build_python_code_chunks

        def build(**kwargs):
            nonlocal changed
            result = original(**kwargs)
            changed = True
            mutate(database, target, project, kind)
            return result

        monkeypatch.setattr(service, "build_python_code_chunks", build)
    error = (
        WorkspaceNotAccessibleError
        if kind == "owner"
        else (
            storage.CodeVectorStorageError
            if stage == "after-chunks"
            else WorkspacePathError
        )
    )
    with pytest.raises(error):
        invoke(target, handler=forbidden)
    assert changed and counts(database) == (0, 0, 0)


@pytest.mark.parametrize(
    "kind",
    [
        "owner",
        "conversation",
        "missing-conversation",
        "task-delete",
        "root",
        "revision",
        "unbind",
        "roundtrip",
    ],
)
def test_changes_while_model_waits_prevent_commit(database, target, project, kind):
    calls = []

    async def handler(request):
        assert database.pool.checkedout() == 0
        await asyncio.sleep(0)
        mutate(database, target, project, kind)
        calls.append(request)
        return httpx.Response(200, json=response())

    error = (
        WorkspaceNotAccessibleError
        if kind in {"owner", "conversation", "missing-conversation", "task-delete"}
        else storage.CodeVectorStorageError
    )
    with pytest.raises(error):
        invoke(target, handler=handler)
    assert len(calls) == 1 and counts(database) == (0, 0, 0)


@pytest.mark.parametrize("kind", ["owner", "roundtrip"])
def test_revocation_after_first_http_batch_prevents_next_send_and_partial_storage(
    database, target, project, kind
):
    (project / "safe.py").write_text(
        "".join(f"def item_{i}(): return {i}\n" for i in range(9)), encoding="utf-8"
    )
    calls = []

    def handler(request):
        calls.append(request)
        assert database.pool.checkedout() == 0
        mutate(database, target, project, kind)
        return httpx.Response(200, json=response(8))

    with pytest.raises(EmbeddingError) as caught:
        invoke(target, handler=handler)
    assert caught.value.code == "embedding_request_failed"
    assert len(calls) == 1 and counts(database) == (0, 0, 0)
    assert str(project) not in str(caught.value)


@pytest.mark.parametrize(
    "kind", ["status", "malformed", "overflow", "transport", "later-model"]
)
def test_model_failure_keeps_existing_batch_and_never_retries(
    database, target, project, kind
):
    first = invoke(target)
    (project / "safe.py").write_text(
        "".join(f"def item_{i}(): return {i}\n" for i in range(9)), encoding="utf-8"
    )
    calls = []

    def handler(request):
        calls.append(request)
        if kind == "status":
            return httpx.Response(503, text="KEY_PRIVATE SOURCE_PRIVATE")
        if kind == "transport":
            raise httpx.ConnectError("KEY_PRIVATE SOURCE_PRIVATE")
        value = response(len(json.loads(request.content)["input"]))
        if kind == "malformed":
            value["data"] = []
        elif kind == "overflow":
            value["data"][0]["embedding"][0] = 1e100
        elif len(calls) == 2:
            value["model"] = "changed-version"
        return httpx.Response(200, json=value)

    error = storage.CodeVectorStorageError if kind == "overflow" else EmbeddingError
    with pytest.raises(error) as caught:
        invoke(target, handler=handler)
    assert len(calls) == (2 if kind in {"later-model", "overflow"} else 1)
    assert counts(database) == (1, 1, 1)
    assert first.batch.chunk_count == 1
    assert "KEY_PRIVATE" not in str(caught.value) and "SOURCE_PRIVATE" not in str(
        caught.value
    )


@pytest.mark.parametrize("stage", ["insert", "commit"])
def test_late_storage_failure_rolls_back_new_batch_but_preserves_existing(
    database, target, project, monkeypatch, stage
):
    invoke(target)
    original = storage.insert_code_embedding_batch

    def fail_insert(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected insertion failure")

    def fail_commit(session):
        if session.new or session.dirty:
            raise RuntimeError("injected commit failure")
        # 保存仓储显式flush后session.new为空；用提交事件中的事务标记识别写事务。
        if session.info.get("generation_write"):
            raise RuntimeError("injected commit failure")

    def mark_insert(session, **kwargs):
        result = original(session, **kwargs)
        session.info["generation_write"] = True
        return result

    if stage == "insert":
        monkeypatch.setattr(storage, "insert_code_embedding_batch", fail_insert)
    else:
        monkeypatch.setattr(storage, "insert_code_embedding_batch", mark_insert)
        event.listen(storage.SessionLocal.class_, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            invoke(target)
    finally:
        if stage == "commit":
            event.remove(storage.SessionLocal.class_, "before_commit", fail_commit)
    assert counts(database) == (1, 1, 1)


def test_timeout_closes_response_and_never_saves(
    database, target, project, closed_clients
):
    stream = TrackedStream(b"{", delay=2)
    with pytest.raises(EmbeddingError) as caught:
        invoke(
            target,
            active=config(timeout_seconds=1.0),
            handler=lambda request: httpx.Response(
                200,
                stream=stream,
                headers={"content-type": "application/json"},
            ),
        )
    assert caught.value.code == "embedding_timeout"
    assert stream.closed and len(closed_clients) == 1 and counts(database) == (0, 0, 0)


@pytest.mark.parametrize("swallow", [False, True])
def test_external_cancellation_or_swallowed_late_response_never_saves(
    database, target, project, swallow
):
    async def scenario():
        started = asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request)
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if not swallow:
                    raise
            return httpx.Response(200, json=response())

        task = asyncio.create_task(
            service.generate_and_save_code_batch(
                **scope(target),
                config=config(),
                transport=httpx.MockTransport(handler),
            )
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        assert database.pool.checkedout() == 0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == 1

    asyncio.run(scenario())
    assert counts(database) == (0, 0, 0)


def test_valid_but_unrelated_generation_output_cannot_be_saved(
    database, target, project, monkeypatch
):
    original = code_embeddings.generate_code_embeddings

    async def changed(chunks, **kwargs):
        result = await original(chunks, **kwargs)
        return replace(result, chunk_parser="other-valid-parser")

    monkeypatch.setattr(service, "generate_code_embeddings", changed)
    with pytest.raises(
        storage.CodeVectorStorageError, match="code_embedding_generation_result_invalid"
    ):
        invoke(target)
    assert counts(database) == (0, 0, 0)


@pytest.mark.parametrize("stage", ["capture", "before-http"])
def test_read_transaction_exit_failure_never_sends_or_saves(
    database, target, project, monkeypatch, stage
):
    ready = stage == "capture"
    original = service.build_python_code_chunks

    def build(**kwargs):
        nonlocal ready
        result = original(**kwargs)
        ready = True
        return result

    def fail(session):
        if ready:
            raise RuntimeError("injected read transaction exit failure")

    monkeypatch.setattr(service, "build_python_code_chunks", build)
    if stage == "capture":
        monkeypatch.setattr(workspace_path, "_resolve_bound_path", forbidden)
    event.listen(storage.SessionLocal.class_, "before_commit", fail)
    try:
        with pytest.raises(RuntimeError, match="injected read transaction"):
            invoke(target, handler=forbidden)
    finally:
        event.remove(storage.SessionLocal.class_, "before_commit", fail)
    assert counts(database) == (0, 0, 0)


def test_client_exit_failure_does_not_save_completed_response(
    database, target, project, monkeypatch
):
    original = httpx.AsyncClient
    clients = []

    class FailingExitClient(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            clients.append(self)

        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            raise RuntimeError("KEY_PRIVATE SOURCE_PRIVATE")

    monkeypatch.setattr(code_embeddings.httpx, "AsyncClient", FailingExitClient)
    with pytest.raises(EmbeddingError) as caught:
        invoke(target)
    assert caught.value.code == "embedding_request_failed"
    assert clients and all(client.is_closed for client in clients)
    assert "KEY_PRIVATE" not in str(caught.value)
    assert counts(database) == (0, 0, 0)


def test_unknown_generation_result_is_fixed_refusal_not_attribute_error(
    database, target, project, monkeypatch
):
    async def unknown(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "generate_code_embeddings", unknown)
    with pytest.raises(
        storage.CodeVectorStorageError, match="code_embedding_generation_result_invalid"
    ):
        invoke(target)
    assert counts(database) == (0, 0, 0)
