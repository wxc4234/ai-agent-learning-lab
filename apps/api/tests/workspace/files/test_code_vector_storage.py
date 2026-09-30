"""真实PostgreSQL向量类型、归属隔离、提交/回滚及删除；不调用真实模型。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
import struct

from pydantic import SecretStr
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import (
    CodeEmbeddingBatch,
    CodeEmbeddingSpace,
    CodeEmbeddingVector,
    Conversation,
    Task,
    Workspace,
)
from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
    set_locked_workspace_root,
)
from app.repositories.workspace.code_embedding_repository import (
    read_owned_code_embedding_batch,
)
from app.services.tasks.task_deletion_service import delete_workspace_task
from app.services.workspace.files import code_vector_storage as service
from tests.assertions import require_value
from tests.model.test_code_embeddings import config, run, source
from tests.tasks.test_task_deletion_service import target
from tests.workspace.files.test_code_vector_storage_validation import batch, split_batch

__all__ = ["target"]


@pytest.fixture
def database(engine, monkeypatch):
    sessions = []

    class TrackedSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)

    monkeypatch.setattr(settings, "app_mode", "local")
    monkeypatch.setattr(
        service,
        "SessionLocal",
        sessionmaker(
            bind=engine, class_=TrackedSession, autoflush=False, expire_on_commit=False
        ),
    )
    yield engine
    assert not any(session.in_transaction() for session in sessions)
    assert engine.pool.checkedout() == 0


@pytest.fixture
def context(database, target):
    scope = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    return service.capture_code_embedding_target(**scope)


def save(context, value=None, active=None):
    return service.save_code_embedding_batch(
        target=context, source=value or batch(), config=active or config()
    )


def load(context, saved, **changes):
    args = {
        key: getattr(context, key) for key in ("user_id", "workspace_id", "task_id")
    }
    args.update(
        batch_id=saved.batch_id, config=config(), response_model="fixture-model-v1"
    )
    args.update(changes)
    return service.load_code_embedding_batch(**args)


def counts(engine):
    with Session(engine) as reader:
        return tuple(
            reader.scalar(select(func.count()).select_from(model))
            for model in (CodeEmbeddingSpace, CodeEmbeddingBatch, CodeEmbeddingVector)
        )


def test_controlled_generation_then_atomic_storage_roundtrips_provenance(
    database, context
):
    def handler(request):
        import httpx
        from tests.model.test_code_embeddings import response

        assert database.pool.checkedout() == 0  # 模型调用不占用已捕获目标的数据库事务。
        return httpx.Response(
            200, json=response(len(json.loads(request.content)["input"]))
        )

    original = run(
        source(["def first(): return '受控'\n", "def second(): return 2\n"]),
        handler=handler,
    )
    saved = save(context, original)
    stored = load(context, saved)
    assert saved.chunk_count == 2 and counts(database) == (1, 1, 2)
    for before, after in zip(original.embeddings, stored.chunks):
        assert {key: after[key] for key in asdict(before.chunk)} == json.loads(
            json.dumps(asdict(before.chunk))
        )
        assert after["vector"] == before.vector
    assert stored.metadata["files"] == json.loads(
        json.dumps([asdict(file) for file in original.files])
    )
    assert stored.metadata["content_trust"] == "untrusted_project_content"
    assert stored.metadata["embedding_space_id"] == saved.space_id
    with database.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT pg_typeof(embedding)::text FROM code_embedding_vectors LIMIT 1"
                )
            )
            == "vector"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT bool_and(vector_dims(embedding)=3) FROM code_embedding_vectors"
                )
            )
            is True
        )


@pytest.mark.parametrize("dimensions", [1, 2, 4096])
def test_float32_and_dimension_boundaries_are_real_storage(
    database, context, dimensions
):
    active = config(dimensions=dimensions)
    saved = save(context, batch(active=active), active)
    stored = load(context, saved, config=active)
    assert (
        stored.chunks[0]["vector"][0] == struct.unpack("!f", struct.pack("!f", 0.1))[0]
    )
    assert len(stored.chunks[0]["vector"]) == dimensions
    assert counts(database) == (1, 1, 1)


@pytest.mark.parametrize("content,count", [("x" * 2000, 20), ("码" * 1365 + "a", 1)])
def test_exact_chunk_count_character_and_byte_limits_persist(
    database, context, content, count
):
    saved = save(context, batch(texts=[content] * count))
    stored = load(context, saved)
    assert saved.chunk_count == count and len(stored.chunks) == count
    assert all(chunk["text"] == content for chunk in stored.chunks)


def test_partial_coverage_unknown_usage_and_whitespace_are_kept(database, context):
    value = replace(
        batch(texts=["\n"]), truncated=True, incomplete_reasons=("chunk_budget",)
    )
    saved = save(context, value)
    stored = load(context, saved)
    assert stored.batch.truncated and stored.metadata["incomplete_reasons"] == [
        "chunk_budget"
    ]
    assert (
        stored.metadata["prompt_tokens"] is None
        and stored.metadata["total_tokens"] is None
    )
    assert stored.chunks[0]["text"] == "\n" and stored.chunks[0]["end_column"] == 1


@pytest.mark.parametrize("partial", [False, True])
def test_definition_parts_and_truncated_prefix_keep_real_part_count(
    database, context, partial
):
    value = split_batch()
    if partial:
        value = replace(
            value,
            embeddings=value.embeddings[:1],
            truncated=True,
            incomplete_reasons=("chunk_budget",),
        )
    saved = save(context, value)
    stored = load(context, saved)
    assert [chunk["part_index"] for chunk in stored.chunks] == (
        [1] if partial else [1, 2]
    )
    assert all(chunk["part_count"] == 2 for chunk in stored.chunks)
    assert stored.batch.truncated is partial


def test_new_batch_never_overwrites_previous_snapshot(database, context):
    first = save(context)
    second = save(context, batch(texts=["def changed(): return 2\n"]))
    assert first.batch_id != second.batch_id and counts(database) == (1, 2, 2)
    assert (
        load(context, first).chunks[0]["text"]
        != load(context, second).chunks[0]["text"]
    )


@pytest.mark.parametrize(
    "change", ["provider", "model", "version", "dimensions", "dimension-option"]
)
def test_same_task_separates_model_spaces_and_rejects_wrong_read(
    database, context, change
):
    first = save(context)
    active = config(
        **{
            "provider": {"base_url": "https://another.invalid/v1"},
            "model": {"model": "other-model"},
            "version": {},
            "dimensions": {"dimensions": 4},
            "dimension-option": {"request_dimensions": True},
        }[change]
    )
    value = batch(active=active)
    response_model = "fixture-model-v1"
    if change == "version":
        from app.services.model.code_embeddings import code_embedding_space_id

        response_model = "fixture-model-v2"
        value = replace(
            value,
            response_model=response_model,
            embedding_space_id=code_embedding_space_id(active, response_model),
        )
    second = save(context, value, active)
    assert first.space_id != second.space_id and counts(database) == (2, 2, 2)
    assert (
        load(context, second, config=active, response_model=response_model).batch
        == second
    )
    with pytest.raises(WorkspaceNotAccessibleError):
        load(context, first, config=active, response_model=response_model)


def test_key_rotation_keeps_space_and_secret_is_not_stored(database, context):
    saved = save(context)
    rotated = config(api_key=SecretStr("ROTATED_PRIVATE"))
    assert load(context, saved, config=rotated).batch.space_id == saved.space_id
    second = save(context, batch(active=rotated), rotated)
    assert counts(database) == (1, 2, 2) and second.space_id == saved.space_id
    with database.connect() as connection:
        payload = connection.scalar(
            text("SELECT row_to_json(b)::text FROM code_embedding_batches b LIMIT 1")
        )
    assert "PRIVATE" not in payload and "provider.invalid" not in payload


@pytest.mark.parametrize(
    "kind",
    [
        "foreign-owner",
        "wrong-project",
        "foreign-conversation",
        "missing-conversation",
        "missing-task",
        "missing-workspace",
    ],
)
def test_capture_write_and_read_reauthorize_current_scope(
    database, context, target, kind
):
    saved = save(context)
    scope = {
        key: getattr(context, key) for key in ("user_id", "workspace_id", "task_id")
    }
    with Session(database) as session, session.begin():
        if kind == "foreign-owner":
            require_value(session.scalar(select(Workspace))).user_id = target[
                "other_id"
            ]
        elif kind == "wrong-project":
            session.add(
                Workspace(
                    external_id="f" * 32,
                    name="其他项目",
                    user_id=context.user_id,
                    root_path="/other",
                )
            )
            scope["workspace_id"] = "f" * 32
        elif kind == "foreign-conversation":
            require_value(
                session.get(Conversation, target["conversation_pk"])
            ).user_id = target["other_id"]
        elif kind == "missing-conversation":
            session.delete(
                require_value(session.get(Conversation, target["conversation_pk"]))
            )
        elif kind == "missing-task":
            scope["task_id"] = "f" * 32
        else:
            scope["workspace_id"] = "f" * 32
    with pytest.raises(WorkspaceNotAccessibleError):
        service.capture_code_embedding_target(**scope)
    with pytest.raises(WorkspaceNotAccessibleError):
        load(context, saved, **scope)
    if kind in {"foreign-owner", "foreign-conversation", "missing-conversation"}:
        with pytest.raises(WorkspaceNotAccessibleError):
            save(context)
    assert counts(database) == (1, 1, 1)


@pytest.mark.parametrize("kind", ["other-project", "sibling-task", "other-user"])
def test_exact_batch_repository_scope_cannot_be_crossed(
    database, context, target, kind
):
    saved = save(context)
    scope = {
        key: getattr(context, key) for key in ("user_id", "workspace_id", "task_id")
    }
    if kind == "sibling-task":
        scope["task_id"] = "d" * 32
    elif kind == "other-user":
        scope["user_id"] = target["other_id"]
    else:
        with Session(database) as session, session.begin():
            session.add(
                Workspace(
                    external_id="f" * 32,
                    name="其他",
                    user_id=context.user_id,
                    root_path="/other",
                )
            )
        scope["workspace_id"] = "f" * 32
    with Session(database) as reader, pytest.raises(WorkspaceNotAccessibleError):
        read_owned_code_embedding_batch(
            reader, **scope, batch_id=saved.batch_id, space_id=saved.space_id
        )


@pytest.mark.parametrize("kind", ["root", "revision", "unbind", "roundtrip"])
def test_binding_changes_deny_old_write_and_read(database, context, kind):
    saved = save(context)
    with Session(database) as session, session.begin():
        workspace = require_value(session.scalar(select(Workspace).with_for_update()))
        if kind == "revision":
            workspace.binding_revision += 1
        elif kind == "roundtrip":
            set_locked_workspace_root(workspace, "/other")
            set_locked_workspace_root(workspace, context.bound_root)
        else:
            set_locked_workspace_root(workspace, None if kind == "unbind" else "/other")
    with pytest.raises(
        service.CodeVectorStorageError, match="code_embedding_binding_changed"
    ):
        save(context)
    with pytest.raises(WorkspaceNotAccessibleError):
        load(context, saved)
    assert counts(database) == (1, 1, 1)


def test_unbound_capture_fails(database, target):
    with Session(database) as session, session.begin():
        require_value(session.scalar(select(Workspace))).root_path = None
    with pytest.raises(
        service.CodeVectorStorageError, match="code_embedding_project_unbound"
    ):
        service.capture_code_embedding_target(
            **{key: target[key] for key in ("user_id", "workspace_id", "task_id")}
        )


@pytest.mark.parametrize("stage", ["after-vectors", "before-commit"])
def test_late_failure_rolls_back_space_batch_and_all_vectors(
    database, context, monkeypatch, stage
):
    if stage == "after-vectors":
        original = service.insert_code_embedding_batch

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected late failure")

        monkeypatch.setattr(service, "insert_code_embedding_batch", fail)
        with pytest.raises(RuntimeError, match="injected late failure"):
            save(context, batch(texts=["def a(): return 1\n", "def b(): return 2\n"]))
    else:
        factory = service.SessionLocal

        def fail_commit(session):
            raise RuntimeError("injected commit failure")

        event.listen(factory.class_, "before_commit", fail_commit)
        try:
            with pytest.raises(RuntimeError, match="injected commit failure"):
                save(context)
        finally:
            event.remove(factory.class_, "before_commit", fail_commit)
    assert counts(database) == (0, 0, 0)


def test_child_insert_failure_rolls_back_earlier_batch_and_space(
    database, context, monkeypatch
):
    original = service.insert_code_embedding_batch

    def malformed(*args, **kwargs):
        kwargs["vectors"][1]["embedding"] = [1.0]  # 数据库CHECK而非Python预检拒绝。
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "insert_code_embedding_batch", malformed)
    with pytest.raises(IntegrityError):
        save(context, batch(texts=["def a(): return 1\n", "def b(): return 2\n"]))
    assert counts(database) == (0, 0, 0)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE code_embedding_vectors SET dimensions=2, embedding='[1,2]'",
        "UPDATE code_embedding_vectors SET space_id=repeat('f',64)",
        "UPDATE code_embedding_batches SET dimensions=2",
        "UPDATE code_embedding_vectors SET embedding='[1,2]'",
        "UPDATE code_embedding_vectors SET ordinal=20",
        "UPDATE code_embedding_vectors SET content=''",
    ],
)
def test_database_constraints_prevent_mixed_dimensions_spaces_and_bad_rows(
    database, context, sql
):
    saved = save(context)
    with pytest.raises(IntegrityError), database.begin() as connection:
        connection.execute(text(sql))
    assert load(context, saved).batch == saved and counts(database) == (1, 1, 1)


def test_task_delete_cascades_only_its_derived_batches_and_vectors(database, context):
    first = save(context)
    sibling = service.capture_code_embedding_target(
        user_id=context.user_id, workspace_id=context.workspace_id, task_id="d" * 32
    )
    other = save(sibling, replace(batch(), task_id="d" * 32))
    with Session(database) as session:
        delete_workspace_task(
            session,
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            task_id=context.task_id,
        )
    assert counts(database) == (1, 1, 1)
    assert load(sibling, other).batch == other
    with pytest.raises(WorkspaceNotAccessibleError):
        load(context, first)


def test_task_delete_failure_restores_derived_vectors_in_same_transaction(
    database, context
):
    saved = save(context)

    def fail(session):
        raise RuntimeError("injected delete commit failure")

    with Session(database) as session:
        event.listen(session, "before_commit", fail)
        with pytest.raises(RuntimeError, match="injected delete commit failure"):
            delete_workspace_task(
                session,
                user_id=context.user_id,
                workspace_id=context.workspace_id,
                task_id=context.task_id,
            )
        assert not session.in_transaction()
    assert counts(database) == (1, 1, 1) and load(context, saved).batch == saved


def test_parallel_projects_share_space_without_sharing_batches(database, context):
    with Session(database) as session, session.begin():
        workspace = Workspace(
            external_id="f" * 32,
            name="其他",
            user_id=context.user_id,
            root_path="/other",
        )
        task = Task(external_id="g" * 32, title="其他任务", workspace=workspace)
        session.add_all(
            [
                workspace,
                task,
                Conversation(external_id="h" * 32, user_id=context.user_id, task=task),
            ]
        )
    other = service.capture_code_embedding_target(
        user_id=context.user_id, workspace_id="f" * 32, task_id="g" * 32
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(save, context),
            pool.submit(
                save, other, replace(batch(), workspace_id="f" * 32, task_id="g" * 32)
            ),
        ]
        first, second = [future.result(timeout=10) for future in futures]
    assert counts(database) == (1, 2, 2) and first.space_id == second.space_id
    with pytest.raises(WorkspaceNotAccessibleError):
        load(context, second)
    assert load(other, second).batch == second
