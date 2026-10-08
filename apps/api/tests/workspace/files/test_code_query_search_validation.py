"""串联入口预检与未知生成结果拒绝；这些反例不创建数据库。"""

import asyncio
from dataclasses import replace
from hashlib import sha256
from typing import Any, cast

import pytest

from app.services.model.code_embeddings import code_embedding_space_id
from app.services.model.embedding_config import EmbeddingError
from app.services.model.query_embeddings import QueryEmbedding
from app.services.workspace.files import code_query_search as service
from tests.model.test_code_embeddings import config


def invoke(**changes: Any):
    arguments: dict[str, Any] = {
        "query": "  查找取消\n",
        "user_id": 1,
        "workspace_id": "a" * 32,
        "task_id": "b" * 32,
        "batch_id": "c" * 32,
        "config": config(),
        "response_model": "fixture-model-v1",
        **changes,
    }
    return asyncio.run(service.search_code_query(**arguments))


def forbidden(*args, **kwargs):
    raise AssertionError("invalid input reached a side effect")


@pytest.fixture
def no_side_effects(monkeypatch):
    monkeypatch.setattr(service, "SessionLocal", None)
    monkeypatch.setattr(service, "generate_query_embedding", forbidden)


@pytest.mark.parametrize(
    "value", [None, True, 1, "", " \n", "PRIVATE\x00QUERY", "\ud800"]
)
def test_invalid_query_never_opens_session_or_sends(no_side_effects, value):
    with pytest.raises(EmbeddingError) as caught:
        invoke(query=value)
    assert caught.value.code == "embedding_query_invalid"
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("value", ["x" * 2001, "码" * 1366])
def test_query_budget_never_opens_session_or_sends(no_side_effects, value):
    with pytest.raises(EmbeddingError) as caught:
        invoke(query=value)
    assert caught.value.code == "embedding_query_budget_exceeded"


@pytest.mark.parametrize("value", [0, -1, 21, True, 1.5, "2", None])
def test_invalid_top_k_never_opens_session_or_sends(no_side_effects, value):
    with pytest.raises(service.CodeVectorSearchError) as caught:
        invoke(top_k=value)
    assert str(caught.value) == caught.value.code == "code_embedding_query_invalid"


@pytest.mark.parametrize(
    "value", [None, True, "", " PRIVATE ", "PRIVATE\x00MODEL", "x" * 257, "\ud800"]
)
def test_invalid_expected_version_never_opens_session_or_sends(no_side_effects, value):
    with pytest.raises(service.CodeVectorSearchError) as caught:
        invoke(response_model=value)
    assert str(caught.value) == "code_embedding_query_invalid"


def generated_query():
    active = config()
    return QueryEmbedding(
        query_sha256=sha256("  查找取消\n".encode()).hexdigest(),
        vector=(1.0, 0.0, 0.0),
        requested_model=active.model,
        response_model="fixture-model-v1",
        dimensions=3,
        embedding_space_id=code_embedding_space_id(active, "fixture-model-v1"),
        prompt_tokens=None,
        total_tokens=None,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"query_sha256": "f" * 64},
        {"requested_model": "PRIVATE_MODEL"},
        {"response_model": "fixture-model-v2"},
        {"dimensions": 2},
        {"dimensions": True},
        {"embedding_space_id": "f" * 64},
        {"request_count": 0},
        {"request_count": 2},
        {"request_count": True},
        {"source": "unknown"},
        {"prompt_tokens": 0},
        {"total_tokens": 0},
        {"prompt_tokens": True, "total_tokens": 1},
        {"prompt_tokens": 1, "total_tokens": True},
        {"prompt_tokens": -1, "total_tokens": 0},
        {"prompt_tokens": 2, "total_tokens": 1},
        {"prompt_tokens": "PRIVATE_USAGE", "total_tokens": 1},
    ],
)
def test_inconsistent_generated_result_never_reaches_recall(monkeypatch, changes):
    value = replace(generated_query(), **changes)

    async def generate(*args, **kwargs):
        return value

    monkeypatch.setattr(service, "_preflight_batch", lambda **kwargs: None)
    monkeypatch.setattr(service, "generate_query_embedding", generate)
    monkeypatch.setattr(service, "search_code_embedding_batch", forbidden)
    with pytest.raises(service.CodeVectorSearchError) as caught:
        invoke()
    assert (
        str(caught.value) == caught.value.code == "code_embedding_query_result_invalid"
    )


@pytest.mark.parametrize("value", [None, {}, "PRIVATE_RESULT"])
def test_unknown_generated_object_is_not_a_fabricated_success(monkeypatch, value):
    async def generate(*args, **kwargs) -> QueryEmbedding:
        # 仅在故障注入处绕过生产类型，验证运行时的未知结果拒绝。
        return cast(QueryEmbedding, value)

    monkeypatch.setattr(service, "_preflight_batch", lambda **kwargs: None)
    monkeypatch.setattr(service, "generate_query_embedding", generate)
    monkeypatch.setattr(service, "search_code_embedding_batch", forbidden)
    with pytest.raises(service.CodeVectorSearchError) as caught:
        invoke()
    assert str(caught.value) == "code_embedding_query_result_invalid"
