"""真实HTTPX请求/响应管道与受控传输；不访问供应商或发送项目源码。"""

import asyncio
from dataclasses import asdict, replace
from hashlib import sha256
import json
from typing import Any

import httpx
from pydantic import SecretStr
import pytest

from app.services.model import code_embeddings as service
from app.services.model.embedding_config import EmbeddingConfig, EmbeddingError
from app.services.workspace.files.code_inventory import CodeFile
from app.services.workspace.files import python_chunks
from app.services.workspace.files.python_symbols import PythonSymbol


def source(texts=None):
    texts = texts if texts is not None else ["def sample(): return 'fixture'\n"]
    raw = "".join(texts).encode("utf-8")
    file = CodeFile("fixture.py", "source", "python", len(raw), sha256(raw).hexdigest())
    chunks = []
    line = 1
    for index, text in enumerate(texts):
        symbol = PythonSymbol(
            file.relative_path,
            f"sample_{index}",
            f"sample_{index}",
            "function",
            line,
            line,
            line + text.count("\n"),
            file.sha256,
        )
        end_line = line + text.count("\n")
        end_column = len(text.rsplit("\n", 1)[-1]) + 1
        chunk = python_chunks._chunk(symbol, text, line, 1, end_line, end_column, 1)
        chunks.append(replace(chunk, part_count=1))
        line = end_line + 1
    return python_chunks.PythonCodeChunks(
        "a" * 32,
        "b" * 32,
        (file,),
        tuple(chunks),
        1,
        1,
        1,
        len(chunks),
        len(chunks),
        line - 1,
        0,
        {},
        False,
        (),
        "fixture_parser",
    )


def config(**values):
    return EmbeddingConfig.model_validate(
        {
            "api_key": SecretStr("EMBED_PRIVATE"),
            "base_url": "https://provider.invalid/v1",
            "model": "fixture-model",
            "dimensions": 3,
            **values,
        }
    )


def response(count=1, *, model="fixture-model-v1", usage=True) -> dict[str, Any]:
    result = {
        "object": "list",
        "model": model,
        "data": [
            {"object": "embedding", "index": i, "embedding": [i + 0.25, -1, 0]}
            for i in reversed(range(count))
        ],
    }
    if usage:
        result["usage"] = {"prompt_tokens": count * 2, "total_tokens": count * 2}
    return result


def run(data=None, *, active=None, handler=None):
    if handler is None:
        handler = lambda request: httpx.Response(
            200, json=response(len(json.loads(request.content)["input"]))
        )
    return asyncio.run(
        service.generate_code_embeddings(
            data or source(),
            config=active or config(),
            transport=httpx.MockTransport(handler),
        )
    )


def test_actual_request_and_reordered_indexes_keep_all_provenance_without_sending_metadata():
    data = source(["def a(): return 'fixture'\n", "def b(): return 'fixture'\n"])
    calls = []

    def handle(request):
        calls.append(request)
        assert (
            request.method == "POST"
            and str(request.url) == "https://provider.invalid/v1/embeddings"
        )
        assert request.headers["authorization"] == "Bearer EMBED_PRIVATE"
        assert request.headers["accept-encoding"] == "identity"
        assert json.loads(request.content) == {
            "model": "fixture-model",
            "input": [chunk.text for chunk in data.chunks],
            "encoding_format": "float",
        }
        assert (
            b"fixture.py" not in request.content and b"chunk_id" not in request.content
        )
        return httpx.Response(200, json=response(2))

    result = run(data, handler=handle)
    assert len(calls) == result.request_count == 1
    assert (
        result.requested_model == "fixture-model"
        and result.response_model == "fixture-model-v1"
    )
    assert result.dimensions == 3 and result.prompt_tokens == result.total_tokens == 4
    assert [item.chunk for item in result.embeddings] == list(data.chunks)
    assert [item.vector for item in result.embeddings] == [
        (0.25, -1.0, 0.0),
        (1.25, -1.0, 0.0),
    ]
    assert result.workspace_id == data.workspace_id and result.task_id == data.task_id
    assert result.files == data.files and result.chunk_strategy == data.strategy
    assert result.content_trust == "untrusted_project_content"
    assert "EMBED_PRIVATE" not in repr(asdict(result))


def test_dimensions_parameter_is_opt_in():
    def handle(request):
        assert json.loads(request.content)["dimensions"] == 3
        return httpx.Response(200, json=response())

    run(active=config(request_dimensions=True), handler=handle)


@pytest.mark.parametrize("count,expected", [(8, [8]), (9, [8, 1]), (20, [8, 8, 4])])
def test_item_budget_batches_and_preserves_input_order(count, expected):
    data = source([f"fixture_{i}" for i in range(count)])
    requests = []

    def handle(request):
        inputs = json.loads(request.content)["input"]
        requests.append(inputs)
        return httpx.Response(200, json=response(len(inputs)))

    result = run(data, handler=handle)
    assert [len(batch) for batch in requests] == expected
    assert [text for batch in requests for text in batch] == [
        chunk.text for chunk in data.chunks
    ]
    assert [item.chunk.chunk_id for item in result.embeddings] == [
        chunk.chunk_id for chunk in data.chunks
    ]
    assert result.request_count == len(expected)


def test_byte_budget_batches_at_exact_boundary():
    requests = []
    data = source(["😀" * 1024 for _ in range(5)])

    def handle(request):
        inputs = json.loads(request.content)["input"]
        requests.append(inputs)
        assert (
            sum(len(text.encode("utf-8")) for text in inputs)
            <= service.MAX_REQUEST_INPUT_BYTES
        )
        return httpx.Response(200, json=response(len(inputs)))

    result = run(data, handler=handle)
    assert [len(batch) for batch in requests] == [4, 1] and len(result.embeddings) == 5


def test_empty_input_never_creates_http_client_and_usage_is_unknown(monkeypatch):
    monkeypatch.setattr(
        service.httpx,
        "AsyncClient",
        lambda **kwargs: pytest.fail("empty batch network client"),
    )
    result = run(source([]))
    assert result.embeddings == () and result.request_count == 0
    assert result.prompt_tokens is result.total_tokens is result.response_model is None


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("too-many", "embedding_input_budget_exceeded"),
        ("chars", "embedding_input_budget_exceeded"),
        ("bytes", "embedding_input_budget_exceeded"),
        ("empty", "embedding_input_invalid"),
        ("surrogate", "embedding_input_invalid"),
        ("duplicate", "embedding_input_invalid"),
        ("id", "embedding_input_invalid"),
        ("digest", "embedding_input_invalid"),
        ("file-version", "embedding_input_invalid"),
    ],
)
def test_entire_input_preflight_before_any_request(kind, expected):
    data = source(["fixture_one", "fixture_two"])
    last = data.chunks[-1]
    if kind == "too-many":
        data = source([f"fixture_{i}" for i in range(21)])
    elif kind in {"chars", "bytes", "empty", "surrogate"}:
        text = {
            "chars": "x" * 2001,
            "bytes": "😀" * 1025,
            "empty": "",
            "surrogate": "\ud800",
        }[kind]
        data = replace(data, chunks=(data.chunks[0], replace(last, text=text)))
    elif kind == "duplicate":
        data = replace(data, chunks=(last, last))
    elif kind == "id":
        data = replace(data, chunks=(replace(last, chunk_id="../PRIVATE"),))
    elif kind == "digest":
        data = replace(data, chunks=(replace(last, text_sha256="f" * 64),))
    else:
        data = replace(
            data, chunks=(replace(last, symbol=replace(last.symbol, sha256="f" * 64)),)
        )
    with pytest.raises(EmbeddingError) as caught:
        run(data, handler=lambda request: pytest.fail("invalid input sent request"))
    assert caught.value.code == expected and "PRIVATE" not in str(caught.value)


def test_total_input_bytes_exact_and_extra_rejected_before_request(monkeypatch):
    data = source(["fixture_one", "fixture_two"])
    size = sum(len(chunk.text.encode("utf-8")) for chunk in data.chunks)
    monkeypatch.setattr(service, "MAX_TOTAL_INPUT_BYTES", size)
    assert len(run(data).embeddings) == 2
    monkeypatch.setattr(service, "MAX_TOTAL_INPUT_BYTES", size - 1)
    with pytest.raises(EmbeddingError) as caught:
        run(data, handler=lambda request: pytest.fail("over-budget sent request"))
    assert caught.value.code == "embedding_input_budget_exceeded"


@pytest.mark.parametrize("text", ["x" * 2000, "😀" * 1024])
def test_per_input_budget_exact_boundary(text):
    assert len(run(source([text])).embeddings) == 1


def test_nonempty_whitespace_fragment_keeps_its_index_and_source():
    data = source(["\n", "fixture"])
    result = run(data)
    assert [item.chunk for item in result.embeddings] == list(data.chunks)


def test_embedding_space_identity_distinguishes_provider_model_and_version():
    first = run()
    assert first.embedding_space_id == run().embedding_space_id
    assert (
        first.embedding_space_id
        == run(active=config(api_key=SecretStr("OTHER_PRIVATE"))).embedding_space_id
    )
    assert (
        first.embedding_space_id
        != run(active=config(base_url="https://other.invalid/v1")).embedding_space_id
    )
    assert (
        first.embedding_space_id
        != run(active=config(model="other-model")).embedding_space_id
    )
    assert (
        first.embedding_space_id
        != run(
            handler=lambda request: httpx.Response(
                200, json=response(model="other-version")
            )
        ).embedding_space_id
    )
    assert (
        first.chunk_policy == source().policy and first.chunk_parser == source().parser
    )


@pytest.mark.parametrize(
    "kind",
    [
        "missing-data",
        "too-many",
        "too-few",
        "duplicate-index",
        "negative-index",
        "large-index",
        "float-index",
        "bool-index",
        "string-index",
        "empty-vector",
        "short-vector",
        "base64-vector",
        "string-number",
        "bool-number",
        "null-number",
        "nan",
        "inf",
        "huge-number",
        "wrong-object",
        "wrong-item-object",
        "missing-model",
        "empty-model",
        "surrogate-model",
        "invalid-usage",
        "negative-usage",
        "float-usage",
        "bool-usage",
        "inconsistent-usage",
    ],
)
def test_malformed_response_never_coerces_or_returns_partial_vectors(kind):
    body = response(2)
    first = body["data"][0]
    if kind == "missing-data":
        body.pop("data")
    elif kind == "too-many":
        body["data"].append(body["data"][0])
    elif kind == "too-few":
        body["data"].pop()
    elif kind == "duplicate-index":
        body["data"][1]["index"] = first["index"]
    elif kind.endswith("index"):
        first["index"] = {
            "negative-index": -1,
            "large-index": 2,
            "float-index": 1.0,
            "bool-index": True,
            "string-index": "1",
        }[kind]
    elif kind.endswith("vector"):
        first["embedding"] = {
            "empty-vector": [],
            "short-vector": [1],
            "base64-vector": "AAAA",
        }[kind]
    elif kind in {
        "string-number",
        "bool-number",
        "null-number",
        "nan",
        "inf",
        "huge-number",
    }:
        first["embedding"][0] = {
            "string-number": "0.25",
            "bool-number": True,
            "null-number": None,
            "nan": float("nan"),
            "inf": float("inf"),
            "huge-number": 10**400,
        }[kind]
    elif kind == "wrong-object":
        body["object"] = "other"
    elif kind == "wrong-item-object":
        first["object"] = "other"
    elif kind == "missing-model":
        body.pop("model")
    elif kind == "empty-model":
        body["model"] = ""
    elif kind == "surrogate-model":
        body["model"] = "\ud800"
    elif kind == "invalid-usage":
        body["usage"] = []
    else:
        body["usage"]["prompt_tokens"] = {
            "negative-usage": -1,
            "float-usage": 1.5,
            "bool-usage": True,
            "inconsistent-usage": 99,
        }[kind]
    raw = json.dumps(body).encode()
    with pytest.raises(EmbeddingError) as caught:
        run(
            source(["one", "two"]),
            handler=lambda request: httpx.Response(
                200, content=raw, headers={"content-type": "application/json"}
            ),
        )
    assert caught.value.code == "embedding_response_invalid"


@pytest.mark.parametrize(
    "raw",
    [
        b"not json PRIVATE",
        b"\xff",
        b'{"object":"list","object":"other"}',
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_invalid_utf8_json_duplicates_and_deep_data_are_safe(raw):
    with pytest.raises(EmbeddingError) as caught:
        run(
            handler=lambda request: httpx.Response(
                200, content=raw, headers={"content-type": "application/json"}
            )
        )
    assert caught.value.code == "embedding_response_invalid" and "PRIVATE" not in str(
        caught.value
    )


def test_missing_usage_in_one_batch_remains_unknown_not_zero():
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=response(len(json.loads(request.content)["input"]), usage=calls == 1),
        )

    result = run(source([f"fixture_{i}" for i in range(9)]), handler=handle)
    assert (
        result.prompt_tokens is result.total_tokens is None
        and result.request_count == 2
    )


def test_different_reported_model_across_batches_rejects_entire_result():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            json=response(
                len(json.loads(request.content)["input"]), model=f"version-{len(calls)}"
            ),
        )

    with pytest.raises(EmbeddingError) as caught:
        run(source([f"fixture_{i}" for i in range(20)]), handler=handle)
    assert caught.value.code == "embedding_response_invalid" and len(calls) == 2


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, raw, *, delay=0.0, fail_if_read=False):
        self.raw, self.delay, self.fail_if_read = raw, delay, fail_if_read
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        if self.fail_if_read:
            pytest.fail("error body was read")
        if self.delay:
            await asyncio.sleep(self.delay)
        yield self.raw[:7]
        yield self.raw[7:]

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("status", [301, 307, 401, 429, 500])
def test_non_success_never_follows_redirect_retries_or_reads_error_body(status):
    stream = TrackedStream(b"EMBED_PRIVATE fixture text", fail_if_read=True)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, stream=stream, headers={"location": "https://other.invalid"}
        )

    with pytest.raises(EmbeddingError) as caught:
        run(handler=handle)
    assert caught.value.code == "embedding_request_failed" and "PRIVATE" not in str(
        caught.value
    )
    assert len(calls) == 1 and stream.closed


@pytest.mark.parametrize(
    "headers",
    [
        {"content-type": "text/html"},
        {"content-type": "application/json", "content-encoding": "gzip"},
    ],
)
def test_invalid_media_or_compression_is_rejected_before_read(headers):
    stream = TrackedStream(b"EMBED_PRIVATE", fail_if_read=True)
    with pytest.raises(EmbeddingError) as caught:
        run(handler=lambda request: httpx.Response(200, stream=stream, headers=headers))
    assert caught.value.code == "embedding_response_invalid" and stream.closed


def test_response_budget_exact_and_extra_byte_closes_stream(monkeypatch):
    raw = json.dumps(response()).encode()
    monkeypatch.setattr(service, "MAX_RESPONSE_BYTES", len(raw))
    assert (
        len(
            run(
                handler=lambda request: httpx.Response(
                    200, content=raw, headers={"content-type": "application/json"}
                )
            ).embeddings
        )
        == 1
    )
    monkeypatch.setattr(service, "MAX_RESPONSE_BYTES", len(raw) - 1)
    stream = TrackedStream(raw)
    with pytest.raises(EmbeddingError) as caught:
        run(
            handler=lambda request: httpx.Response(
                200, stream=stream, headers={"content-type": "application/json"}
            )
        )
    assert caught.value.code == "embedding_response_too_large" and stream.closed


@pytest.mark.parametrize(
    "failure,code",
    [
        (httpx.ConnectError, "embedding_request_failed"),
        (httpx.ReadTimeout, "embedding_timeout"),
        (RuntimeError, "embedding_request_failed"),
    ],
)
def test_transport_errors_have_fixed_messages(failure, code):
    def handle(request):
        raise failure("EMBED_PRIVATE fixture input")

    with pytest.raises(EmbeddingError) as caught:
        run(handler=handle)
    assert caught.value.code == code and "PRIVATE" not in str(caught.value)


def test_later_batch_failure_stops_without_returning_or_retrying_earlier_vectors():
    calls = []

    def handle(request):
        calls.append(request)
        return (
            httpx.Response(200, json=response(8))
            if len(calls) == 1
            else httpx.Response(500)
        )

    with pytest.raises(EmbeddingError) as caught:
        run(source([f"fixture_{i}" for i in range(20)]), handler=handle)
    assert caught.value.code == "embedding_request_failed" and len(calls) == 2


def test_total_wall_clock_timeout_closes_active_response():
    stream = TrackedStream(json.dumps(response()).encode(), delay=0.02)
    # 仅测试缩短时间，不把低于配置下限的值开放给用户。
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
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=stream, headers={"content-type": "application/json"}
            )
        )
        task = asyncio.create_task(
            service.generate_code_embeddings(
                source(), config=config(), transport=transport
            )
        )
        await asyncio.wait_for(stream.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed

    asyncio.run(scenario())


def test_coverage_flags_are_preserved_and_input_json_result_budget_is_complete(
    monkeypatch,
):
    data = replace(source(), truncated=True, incomplete_reasons=("chunk_budget",))
    result = run(data)
    assert result.truncated and result.incomplete_reasons == data.incomplete_reasons
    size = len(
        json.dumps(
            asdict(result), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    )
    monkeypatch.setattr(service, "MAX_EMBEDDING_RESULT_BYTES", size)
    assert asdict(run(data)) == asdict(result)
    monkeypatch.setattr(service, "MAX_EMBEDDING_RESULT_BYTES", size - 1)
    with pytest.raises(EmbeddingError) as caught:
        run(data)
    assert caught.value.code == "embedding_result_too_large"
