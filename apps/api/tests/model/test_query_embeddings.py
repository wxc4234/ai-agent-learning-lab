"""单查询真实HTTPX管道与受控传输；不访问供应商、数据库或项目目录。"""

import asyncio
from dataclasses import asdict
from hashlib import sha256
import json

import httpx
from pydantic import SecretStr
import pytest

from app.services.model import query_embeddings as service
from app.services.model.embedding_config import EmbeddingError
from tests.model.test_code_embeddings import (
    TrackedStream,
    config,
    response,
    run as run_code_embeddings,
)


@pytest.fixture(autouse=True)
def closed_clients(monkeypatch):
    clients = []
    original = httpx.AsyncClient

    class TrackedClient(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            clients.append(self)

    monkeypatch.setattr(service.httpx, "AsyncClient", TrackedClient)
    yield clients
    # 所有成功与失败路径都必须退出客户端上下文，不能只检查业务异常。
    assert all(client.is_closed for client in clients)


def run(query: str = "查找任务取消后的执行占用释放", *, active=None, handler=None):
    selected_handler = handler or (lambda request: httpx.Response(200, json=response()))
    return asyncio.run(
        service.generate_query_embedding(
            query,
            config=active or config(),
            transport=httpx.MockTransport(selected_handler),
        )
    )


def raw_response(raw: bytes, *, media: str = "application/json") -> httpx.Response:
    return httpx.Response(200, content=raw, headers={"content-type": media})


def test_single_request_preserves_exact_text_and_plain_result(closed_clients):
    query = "  查询取消后的占用释放\n保留原文空白  "
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://provider.invalid/v1/embeddings"
        assert request.headers["authorization"] == "Bearer EMBED_PRIVATE"
        assert request.headers["accept-encoding"] == "identity"
        assert json.loads(request.content) == {
            "model": "fixture-model",
            "input": [query],
            "encoding_format": "float",
        }
        assert request.extensions["timeout"] == {
            "connect": 30.0,
            "read": 30.0,
            "write": 30.0,
            "pool": 30.0,
        }
        return httpx.Response(200, json=response())

    result = run(query, handler=handle)
    assert len(calls) == result.request_count == 1
    assert len(closed_clients) == 1
    assert result.query_sha256 == sha256(query.encode("utf-8")).hexdigest()
    assert result.vector == (0.25, -1.0, 0.0)
    assert result.requested_model == "fixture-model"
    assert result.response_model == "fixture-model-v1"
    assert result.dimensions == 3
    assert result.prompt_tokens == result.total_tokens == 2
    assert result.source == "query_embedding"
    public = repr(asdict(result))
    assert query not in public and "EMBED_PRIVATE" not in public
    assert "provider.invalid" not in public
    assert not {"workspace_id", "task_id", "files", "query"} & asdict(result).keys()


@pytest.mark.parametrize("enabled", [False, True])
def test_dimensions_parameter_is_explicit_opt_in(enabled):
    def handle(request):
        payload = json.loads(request.content)
        if enabled:
            assert payload["dimensions"] == 3
        else:
            assert "dimensions" not in payload
        return httpx.Response(200, json=response())

    assert (
        run(active=config(request_dimensions=enabled), handler=handle).dimensions == 3
    )


@pytest.mark.parametrize("dimensions", [1, 2, 4096])
def test_explicit_dimension_boundaries_are_checked_without_guessing(dimensions):
    body = response()
    body["data"][0]["embedding"] = [0.25] * dimensions
    result = run(
        active=config(dimensions=dimensions),
        handler=lambda request: httpx.Response(200, json=body),
    )
    assert result.dimensions == len(result.vector) == dimensions


@pytest.mark.parametrize("query", ["x" * 2000, "😀" * 1024])
def test_exact_input_character_and_utf8_boundaries(query):
    calls = []

    def handle(request):
        calls.append(request)
        assert json.loads(request.content)["input"] == [query]
        return httpx.Response(200, json=response())

    result = run(query, handler=handle)
    assert len(calls) == 1
    assert result.query_sha256 == sha256(query.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "query,code",
    [
        (None, "embedding_query_invalid"),
        (True, "embedding_query_invalid"),
        (123, "embedding_query_invalid"),
        (["PRIVATE"], "embedding_query_invalid"),
        ("", "embedding_query_invalid"),
        (" \t\n\u00a0", "embedding_query_invalid"),
        ("PRIVATE\x00QUERY", "embedding_query_invalid"),
        ("PRIVATE\ud800", "embedding_query_invalid"),
        ("x" * 2001, "embedding_query_budget_exceeded"),
        ("😀" * 1025, "embedding_query_budget_exceeded"),
    ],
)
def test_input_preflight_never_constructs_client_or_echoes_query(
    monkeypatch, query: object, code
):
    def forbidden(**kwargs):
        pytest.fail("invalid query created an HTTP client")

    monkeypatch.setattr(service.httpx, "AsyncClient", forbidden)
    with pytest.raises(EmbeddingError) as caught:
        # 非法输入只在具体调用处标明，生产接口仍保持str契约。
        run(query)  # pyright: ignore[reportArgumentType]
    assert caught.value.code == code
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("kind", ["missing", "null", "zero"])
def test_unknown_usage_is_distinct_from_reported_zero(kind):
    body = response(usage=False)
    if kind == "null":
        body["usage"] = None
    elif kind == "zero":
        body["usage"] = {"prompt_tokens": 0, "total_tokens": 0}
    result = run(handler=lambda request: httpx.Response(200, json=body))
    if kind == "zero":
        assert result.prompt_tokens == result.total_tokens == 0
    else:
        assert result.prompt_tokens is result.total_tokens is None


def test_query_and_code_generation_use_the_same_model_space():
    active = config()
    query = run(active=active)
    code = run_code_embeddings(active=active)
    assert query.embedding_space_id == code.embedding_space_id
    assert query.source != code.source


@pytest.mark.parametrize(
    "kind", ["provider", "model", "version", "dimensions", "dimension-option"]
)
def test_observed_model_space_changes_with_identity_fields(kind):
    original = run()
    active = config(
        **{
            "provider": {"base_url": "https://another.invalid/v1"},
            "model": {"model": "another-model"},
            "version": {},
            "dimensions": {"dimensions": 4},
            "dimension-option": {"request_dimensions": True},
        }[kind]
    )
    body = response(
        model="fixture-model-v2" if kind == "version" else "fixture-model-v1"
    )
    body["data"][0]["embedding"] = [0.25] * active.dimensions
    changed = run(active=active, handler=lambda request: httpx.Response(200, json=body))
    assert changed.embedding_space_id != original.embedding_space_id


def test_key_rotation_keeps_model_space_and_does_not_publish_key():
    first = run()
    second = run(active=config(api_key=SecretStr("ROTATED_PRIVATE")))
    assert second.embedding_space_id == first.embedding_space_id
    assert "PRIVATE" not in repr(second)


@pytest.mark.parametrize("vector", [(0.1, 0, 0), (0, 0, 0), (1e39, 0, 0)])
def test_generation_preserves_finite_values_without_claiming_cosine_readiness(vector):
    body = response()
    body["data"][0]["embedding"] = list(vector)
    result = run(handler=lambda request: httpx.Response(200, json=body))
    # 生成层不静默量化/归一化；float32与零向量边界仍由召回入口负责。
    assert result.vector == tuple(float(number) for number in vector)


@pytest.mark.parametrize(
    "kind",
    [
        "missing-data",
        "empty-data",
        "extra-data",
        "wrong-data",
        "wrong-item",
        "missing-index",
        "negative-index",
        "large-index",
        "bool-index",
        "float-index",
        "string-index",
        "missing-vector",
        "empty-vector",
        "short-vector",
        "long-vector",
        "base64-vector",
        "bool-number",
        "string-number",
        "null-number",
        "nan",
        "infinity",
        "huge-number",
        "wrong-object",
        "wrong-item-object",
        "missing-model",
        "empty-model",
        "space-model",
        "control-model",
        "long-model",
        "surrogate-model",
        "null-model",
        "invalid-usage",
        "partial-usage",
        "negative-usage",
        "float-usage",
        "bool-usage",
        "inverted-usage",
    ],
)
def test_single_result_schema_rejects_malformed_response_without_partial_success(kind):
    body = response()
    item = body["data"][0]
    if kind == "missing-data":
        body.pop("data")
    elif kind == "empty-data":
        body["data"] = []
    elif kind == "extra-data":
        body["data"].append(dict(item))
    elif kind == "wrong-data":
        body["data"] = {}
    elif kind == "wrong-item":
        body["data"] = [None]
    elif kind == "missing-index":
        item.pop("index")
    elif kind.endswith("index"):
        item["index"] = {
            "negative-index": -1,
            "large-index": 1,
            "bool-index": False,
            "float-index": 0.0,
            "string-index": "0",
        }[kind]
    elif kind == "missing-vector":
        item.pop("embedding")
    elif kind.endswith("vector"):
        item["embedding"] = {
            "empty-vector": [],
            "short-vector": [1],
            "long-vector": [1] * 4,
            "base64-vector": "PRIVATE_BASE64",
        }[kind]
    elif kind in {
        "bool-number",
        "string-number",
        "null-number",
        "nan",
        "infinity",
        "huge-number",
    }:
        item["embedding"][0] = {
            "bool-number": True,
            "string-number": "PRIVATE_NUMBER",
            "null-number": None,
            "nan": float("nan"),
            "infinity": float("inf"),
            "huge-number": 10**400,
        }[kind]
    elif kind == "wrong-object":
        body["object"] = "other"
    elif kind == "wrong-item-object":
        item["object"] = "other"
    elif kind == "missing-model":
        body.pop("model")
    elif kind.endswith("model"):
        body["model"] = {
            "empty-model": "",
            "space-model": " PRIVATE ",
            "control-model": "PRIVATE\x00MODEL",
            "long-model": "x" * 257,
            "surrogate-model": "\ud800",
            "null-model": None,
        }[kind]
    elif kind == "invalid-usage":
        body["usage"] = []
    elif kind == "partial-usage":
        body["usage"].pop("total_tokens")
    else:
        body["usage"]["prompt_tokens"] = {
            "negative-usage": -1,
            "float-usage": 1.5,
            "bool-usage": True,
            "inverted-usage": 99,
        }[kind]
    raw = json.dumps(body).encode("utf-8")
    with pytest.raises(EmbeddingError) as caught:
        run(handler=lambda request: raw_response(raw))
    assert caught.value.code == "embedding_response_invalid"
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize(
    "raw",
    [
        b"PRIVATE not JSON",
        b"\xff",
        b'{"object":"list","object":"list"}',
        b"[" * 2000 + b"]" * 2000,
        b'{"object":"list","model":"fixture-model-v1","data":[{"object":"embedding","index":0,"index":0,"embedding":[1,0,0]}]}',
    ],
)
def test_invalid_encoding_duplicate_keys_and_deep_json_are_safe(raw):
    with pytest.raises(EmbeddingError) as caught:
        run(handler=lambda request: raw_response(raw))
    assert caught.value.code == "embedding_response_invalid"
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("status", [301, 307, 401, 429, 500])
def test_failed_status_does_not_follow_retry_or_read_supplier_error(status):
    stream = TrackedStream(b"EMBED_PRIVATE QUERY_PRIVATE", fail_if_read=True)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, stream=stream, headers={"location": "https://other.invalid"}
        )

    with pytest.raises(EmbeddingError) as caught:
        run(handler=handle)
    assert caught.value.code == "embedding_request_failed"
    assert len(calls) == 1 and stream.closed
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"content-type": "text/html"},
        {"content-type": "text/json"},
        {"content-type": "application/json", "content-encoding": "gzip"},
    ],
)
def test_invalid_media_or_compression_fails_before_read_and_closes_response(headers):
    stream = TrackedStream(b"PRIVATE", fail_if_read=True)
    with pytest.raises(EmbeddingError) as caught:
        run(handler=lambda request: httpx.Response(200, stream=stream, headers=headers))
    assert caught.value.code == "embedding_response_invalid" and stream.closed


@pytest.mark.parametrize(
    "media",
    ["application/json; charset=utf-8", "Application/JSON", "application/vendor+json"],
)
def test_supported_json_media_types_are_parsed(media):
    raw = json.dumps(response()).encode()
    assert run(handler=lambda request: raw_response(raw, media=media)).dimensions == 3


@pytest.mark.parametrize("extra", [0, 1])
def test_actual_response_byte_budget_and_one_extra_byte_close_stream(extra):
    raw = json.dumps(response()).encode()
    raw += b" " * (service.MAX_RESPONSE_BYTES - len(raw) + extra)
    stream = TrackedStream(raw)
    if extra:
        with pytest.raises(EmbeddingError) as caught:
            run(
                handler=lambda request: httpx.Response(
                    200, stream=stream, headers={"content-type": "application/json"}
                )
            )
        assert caught.value.code == "embedding_response_too_large"
    else:
        assert (
            run(
                handler=lambda request: httpx.Response(
                    200, stream=stream, headers={"content-type": "application/json"}
                )
            ).dimensions
            == 3
        )
    assert stream.closed


@pytest.mark.parametrize(
    "failure,code",
    [
        (httpx.ConnectError, "embedding_request_failed"),
        (httpx.ReadError, "embedding_request_failed"),
        (httpx.ConnectTimeout, "embedding_timeout"),
        (httpx.ReadTimeout, "embedding_timeout"),
        (RuntimeError, "embedding_request_failed"),
    ],
)
def test_transport_failures_are_static_and_not_retried(failure, code):
    calls = []

    def handle(request):
        calls.append(request)
        raise failure("EMBED_PRIVATE QUERY_PRIVATE")

    with pytest.raises(EmbeddingError) as caught:
        run(handler=handle)
    assert caught.value.code == code and len(calls) == 1
    assert "PRIVATE" not in str(caught.value)


def test_unknown_client_constructor_failure_is_not_exposed(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("EMBED_PRIVATE QUERY_PRIVATE")

    monkeypatch.setattr(service.httpx, "AsyncClient", fail)
    with pytest.raises(EmbeddingError) as caught:
        run()
    assert caught.value.code == "embedding_request_failed"
    assert "PRIVATE" not in str(caught.value)


def test_client_exit_failure_does_not_publish_constructed_result(monkeypatch):
    original = httpx.AsyncClient

    class FailingExitClient(original):
        async def __aexit__(self, *args, **kwargs):
            await super().__aexit__(*args, **kwargs)
            raise RuntimeError("EMBED_PRIVATE exit failure")

    monkeypatch.setattr(service.httpx, "AsyncClient", FailingExitClient)
    with pytest.raises(EmbeddingError) as caught:
        run()
    assert caught.value.code == "embedding_request_failed"
    assert "PRIVATE" not in str(caught.value)


def test_wall_clock_timeout_closes_active_response():
    stream = TrackedStream(json.dumps(response()).encode(), delay=0.02)
    # 只缩短测试等待，不改变正式配置1秒的下限。
    active = config().model_copy(update={"timeout_seconds": 0.005})
    with pytest.raises(EmbeddingError) as caught:
        run(
            active=active,
            handler=lambda request: httpx.Response(
                200, stream=stream, headers={"content-type": "application/json"}
            ),
        )
    assert caught.value.code == "embedding_timeout" and stream.closed


def test_external_cancellation_propagates_and_closes_response():
    async def scenario():
        stream = TrackedStream(json.dumps(response()).encode(), delay=20)
        task = asyncio.create_task(
            service.generate_query_embedding(
                "QUERY_PRIVATE",
                config=config(),
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, stream=stream, headers={"content-type": "application/json"}
                    )
                ),
            )
        )
        await asyncio.wait_for(stream.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed

    asyncio.run(scenario())
