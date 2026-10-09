"""内部工具定义实际调用授权检索组合；隔离PG和受控HTTP，无Agent注册。"""

import asyncio
from dataclasses import replace
import json

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.services.workspace.files.code_context import CodeContextBudget
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.search_code import make_search_code_definition
from tests.assertions import require_value
from tests.model.test_code_embeddings import config
from tests.model.test_query_embeddings import closed_clients
from tests.workspace.files.test_code_query_context import context_database
from tests.workspace.files.test_code_query_search import query_response
from tests.workspace.files.test_code_retrieval_context import retrieval_database
from tests.workspace.files.test_code_vector_search import source_vectors
from tests.workspace.files.test_code_vector_storage import context, counts, database, save, target

__all__ = ["closed_clients", "context", "context_database", "database", "retrieval_database", "target"]


def invoke(context, handler, **options):
    definition = make_search_code_definition(config=config(), transport=httpx.MockTransport(handler), **options)
    request = definition.validate_arguments('{"query":"查找取消"}')
    trusted = ToolExecutionContext(context.user_id, "d" * 32, context.workspace_id, context.task_id)
    return asyncio.run(definition.execute_async(request, context=trusted))


@pytest.mark.parametrize("mode", ["empty-window", "zero-hits", "matches"])
def test_real_public_projection_and_no_writes(retrieval_database, context, closed_clients, mode):
    if mode != "empty-window":
        original = source_vectors([(0, 0, 0)] if mode == "zero-hits" else [(1, 0, 0), (0, 1, 0)])
        save(context, replace(original, truncated=True, incomplete_reasons=("chunk_budget",)))
    before = counts(retrieval_database)
    calls = []

    def handler(request):
        assert retrieval_database.pool.checkedout() == 0
        calls.append(request)
        return query_response()

    payload = json.loads(invoke(context, handler, budget=CodeContextBudget(max_chunks=1)))
    if mode == "empty-window":
        assert payload["status"] == "not_found_in_window" and calls == [] and closed_clients == []
    else:
        assert payload["status"] == "context_ready" and len(calls) == 1
        assert payload["coverage"]["snapshot_truncated"] is True
        assert len(payload["matches"]) == (1 if mode == "matches" else 0)
        if mode == "matches":
            assert payload["coverage"]["builder_omitted_hits"] == 1
            assert payload["matches"][0]["relative_path"].endswith(".py")
    assert counts(retrieval_database) == before


@pytest.mark.parametrize("phase", ["before", "during"])
def test_real_revoke_returns_fixed_error(retrieval_database, context, target, phase):
    save(context)
    calls = []

    def revoke():
        with Session(retrieval_database) as session, session.begin():
            require_value(session.scalar(select(Workspace))).user_id = target["other_id"]

    def handler(request):
        calls.append(request)
        revoke()
        return query_response()

    if phase == "before":
        revoke()
    with pytest.raises(SafeToolExecutionError) as caught:
        invoke(context, handler)
    assert caught.value.code == "workspace_not_accessible"
    assert len(calls) == (1 if phase == "during" else 0)


@pytest.mark.parametrize("mode,code", [("version", "code_search_space_changed"), ("budget", "code_search_budget_exceeded"), ("http", "code_search_unavailable")])
def test_real_failure_is_not_empty_success(retrieval_database, context, mode, code):
    save(context)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500, text="PRIVATE") if mode == "http" else query_response(model="changed" if mode == "version" else "fixture-model-v1")

    with pytest.raises(SafeToolExecutionError) as caught:
        invoke(context, handler, budget=CodeContextBudget(max_bytes=1) if mode == "budget" else None)
    assert caught.value.code == code and "PRIVATE" not in str(caught.value)
    assert len(calls) == 1
