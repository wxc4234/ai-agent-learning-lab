"""查询预检在事务前拒绝无效输入；距离检查保留严格数值边界。"""

import pytest

from app.services.workspace.files import code_vector_search as service
from tests.model.test_code_embeddings import config


def invoke(**changes):
    arguments = {
        "user_id": 1,
        "workspace_id": "a" * 32,
        "task_id": "b" * 32,
        "batch_id": "c" * 32,
        "config": config(),
        "response_model": "fixture-model-v1",
        "query_vector": (1.0, 0.0, 0.0),
        **changes,
    }
    return service.search_code_embedding_batch(**arguments)


@pytest.mark.parametrize("value", [0, -1, 21, True, 1.5, "2", None])
def test_invalid_top_k_never_opens_transaction(monkeypatch, value):
    monkeypatch.setattr(service, "SessionLocal", None)
    with pytest.raises(service.CodeVectorSearchError) as caught:
        invoke(top_k=value)
    assert str(caught.value) == caught.value.code == "code_embedding_query_invalid"


@pytest.mark.parametrize(
    "value",
    [
        (),
        (1.0, 0.0),
        (1.0,) * 4097,
        [1.0, 0.0, 0.0],
        "PRIVATE_QUERY",
        None,
        (True, 0.0, 0.0),
        ("PRIVATE_NUMBER", 0.0, 0.0),
        (float("nan"), 0.0, 0.0),
        (float("inf"), 0.0, 0.0),
        (float("-inf"), 0.0, 0.0),
        (1e39, 0.0, 0.0),
        (10**400, 0.0, 0.0),
    ],
)
def test_invalid_vector_never_opens_transaction_or_echoes_input(monkeypatch, value):
    monkeypatch.setattr(service, "SessionLocal", None)
    with pytest.raises(service.CodeVectorSearchError) as caught:
        invoke(query_vector=value)
    assert str(caught.value) == caught.value.code == "code_embedding_query_invalid"


@pytest.mark.parametrize(
    "value", [(0.0, 0.0, 0.0), (-0.0, 0.0, 0.0), (1e-50, 0.0, 0.0)]
)
def test_zero_after_float32_conversion_never_opens_transaction(monkeypatch, value):
    monkeypatch.setattr(service, "SessionLocal", None)
    with pytest.raises(service.CodeVectorSearchError) as caught:
        invoke(query_vector=value)
    assert caught.value.code == "code_embedding_query_zero"


@pytest.mark.parametrize(
    "value", [None, True, "", " PRIVATE ", "PRIVATE\x00MODEL", "x" * 257, "\ud800"]
)
def test_invalid_reported_model_never_opens_transaction(monkeypatch, value):
    monkeypatch.setattr(service, "SessionLocal", None)
    with pytest.raises(service.CodeVectorSearchError) as caught:
        invoke(response_model=value)
    assert str(caught.value) == "code_embedding_query_invalid"


@pytest.mark.parametrize(
    "value",
    [None, True, "PRIVATE", float("nan"), float("inf"), float("-inf"), -0.01, 2.01],
)
def test_unknown_or_invalid_distance_is_not_a_low_relevance_score(value: object):
    with pytest.raises(service.CodeVectorSearchError) as caught:
        # 有意向类型化入口传入非法距离，检查运行时拒绝而非放宽生产签名。
        service._checked_distance(value)  # pyright: ignore[reportArgumentType]
    assert str(caught.value) == caught.value.code == "code_embedding_distance_invalid"


@pytest.mark.parametrize("value", [0.0, 1.0, 2.0])
def test_cosine_distance_boundaries_remain_valid(value: float):
    assert service._checked_distance(value) == value
