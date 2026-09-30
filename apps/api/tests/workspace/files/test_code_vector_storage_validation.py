"""存储入口预检拒绝无效快照，不能先开启数据库事务或反射私有数据。"""

from dataclasses import replace

import pytest

from app.services.model.code_embeddings import (
    CodeEmbeddings,
    EmbeddedCodeChunk,
    code_embedding_space_id,
)
from app.services.workspace.files import code_vector_storage as service
from tests.model.test_code_embeddings import config, source
from app.services.workspace.files.python_chunks import _chunk
from app.services.workspace.files.python_symbols import PythonSymbol


def batch(*, active=None, texts=None):
    active = active or config()
    original = source(texts)
    return CodeEmbeddings(
        workspace_id=original.workspace_id,
        task_id=original.task_id,
        files=original.files,
        embeddings=tuple(
            EmbeddedCodeChunk(
                chunk,
                tuple(([0.1, -1.0] + [0.0] * active.dimensions)[: active.dimensions]),
            )
            for chunk in original.chunks
        ),
        requested_model=active.model,
        response_model="fixture-model-v1",
        dimensions=active.dimensions,
        embedding_space_id=code_embedding_space_id(active, "fixture-model-v1"),
        request_count=1,
        prompt_tokens=None,
        total_tokens=None,
        truncated=False,
        incomplete_reasons=(),
        chunk_strategy=original.strategy,
        chunk_policy=original.policy,
        chunk_parser=original.parser,
    )


def split_batch():
    value = batch(texts=["def parent():\n", "    pass\n"])
    file = value.files[0]
    symbol = PythonSymbol(
        file.relative_path, "parent", "parent", "function", 1, 1, 2, file.sha256
    )
    chunks = tuple(
        replace(_chunk(symbol, text, index, 1, index + 1, 1, index), part_count=2)
        for index, text in enumerate(["def parent():\n", "    pass\n"], 1)
    )
    return replace(
        value,
        embeddings=tuple(
            EmbeddedCodeChunk(chunk, (0.25, -1.0, 0.0)) for chunk in chunks
        ),
    )


@pytest.mark.parametrize(
    "kind",
    ["duplicate-index", "different-count", "different-reasons", "missing-full-part"],
)
def test_inconsistent_definition_parts_never_write(monkeypatch, kind):
    value = split_batch()
    first, second = value.embeddings
    if kind == "missing-full-part":
        value = replace(value, embeddings=(first,))
    else:
        changes = {
            "duplicate-index": {"part_index": 1},
            "different-count": {"part_count": 3},
            "different-reasons": {"split_reasons": ("line_budget",)},
        }[kind]
        value = replace(
            value,
            embeddings=(first, replace(second, chunk=replace(second.chunk, **changes))),
        )
    monkeypatch.setattr(service, "SessionLocal", None)
    with pytest.raises(
        service.CodeVectorStorageError, match="code_embedding_batch_invalid"
    ):
        service.save_code_embedding_batch(
            target=service.CodeEmbeddingTarget(1, "a" * 32, "b" * 32, "/PRIVATE", 1),
            source=value,
            config=config(),
        )


@pytest.mark.parametrize(
    "path", ["C:/PRIVATE.py", "file.py:secret", "con.py", "folder/PRIVATE.py."]
)
def test_consistent_but_noncanonical_path_is_rejected_before_transaction(
    monkeypatch, path
):
    value = batch()
    original = value.embeddings[0]
    symbol = replace(original.chunk.symbol, relative_path=path)
    chunk = original.chunk
    identity = _chunk(
        symbol,
        chunk.text,
        chunk.start_line,
        chunk.start_column,
        chunk.end_line,
        chunk.end_column,
        chunk.part_index,
    ).chunk_id
    value = replace(
        value,
        files=(replace(value.files[0], relative_path=path),),
        embeddings=(
            replace(original, chunk=replace(chunk, symbol=symbol, chunk_id=identity)),
        ),
    )
    monkeypatch.setattr(service, "SessionLocal", None)
    with pytest.raises(
        service.CodeVectorStorageError, match="code_embedding_batch_invalid"
    ):
        service.save_code_embedding_batch(
            target=service.CodeEmbeddingTarget(1, "a" * 32, "b" * 32, "/PRIVATE", 1),
            source=value,
            config=config(),
        )


def mutate(value, kind):
    if kind in {
        "nan",
        "infinity",
        "float32-overflow",
        "bool",
        "string",
        "huge-int",
        "short-vector",
    }:
        number = {
            "nan": float("nan"),
            "infinity": float("inf"),
            "float32-overflow": 1e39,
            "bool": True,
            "string": "1",
            "huge-int": 10**400,
            "short-vector": 0.1,
        }[kind]
        vector = (number,) if kind == "short-vector" else (number, -1.0, 0.0)
        return replace(value, embeddings=(replace(value.embeddings[0], vector=vector),))
    changes = {
        "empty": {"embeddings": ()},
        "too-many": {"embeddings": value.embeddings * 21},
        "duplicate": {"embeddings": value.embeddings * 2},
        "no-files": {"files": ()},
        "duplicate-files": {"files": value.files * 2},
        "wrong-scope": {"task_id": "other"},
        "wrong-workspace": {"workspace_id": "other"},
        "wrong-space": {"embedding_space_id": "f" * 64},
        "wrong-dimensions": {"dimensions": 4},
        "bool-dimensions": {"dimensions": True},
        "wrong-model": {"requested_model": "other"},
        "no-model": {"response_model": None},
        "bad-model": {"response_model": "PRIVATE\x00MODEL"},
        "no-requests": {"request_count": 0},
        "too-many-requests": {"request_count": 6},
        "negative-usage": {"prompt_tokens": -1, "total_tokens": -1},
        "half-usage": {"total_tokens": 0},
        "inverted-usage": {"prompt_tokens": 2, "total_tokens": 1},
        "bool-usage": {"prompt_tokens": True, "total_tokens": True},
        "false-completeness": {"truncated": True},
        "hidden-incomplete": {"incomplete_reasons": ("chunk_budget",)},
        "unknown-reason": {"truncated": True, "incomplete_reasons": ("unknown",)},
        "wrong-strategy": {"chunk_strategy": "other"},
        "wrong-policy": {"chunk_policy": "other"},
        "trusted-content": {"content_trust": "trusted"},
        "wrong-source": {"source": "other"},
        "bad-parser": {"chunk_parser": "PRIVATE\x00PARSER"},
    }
    if kind in changes:
        return replace(value, **changes[kind])
    chunk = value.embeddings[0].chunk
    if kind.startswith("symbol-"):
        changes = {
            "symbol-path": {"relative_path": "../PRIVATE.py"},
            "symbol-hash": {"sha256": "f" * 64},
            "symbol-start": {"start_line": 2},
            "symbol-name": {"name": "PRIVATE\x00NAME"},
            "symbol-qualified": {"qualified_name": "wrong"},
        }
        chunk = replace(chunk, symbol=replace(chunk.symbol, **changes[kind]))
    elif kind.startswith("file-"):
        changes = {
            "file-path": {"relative_path": "/PRIVATE.py"},
            "file-hash": {"sha256": "f" * 64},
            "file-large": {"byte_count": 65537},
            "file-language": {"language": "javascript"},
        }
        return replace(value, files=(replace(value.files[0], **changes[kind]),))
    else:
        changes = {
            "chunk-id": {"chunk_id": "f" * 64},
            "text-hash": {"text_sha256": "f" * 64},
            "text-changed": {"text": "PRIVATE"},
            "text-large": {"text": "x" * 2001},
            "text-bytes": {"text": "码" * 1366},
            "text-nul": {"text": "PRIVATE\x00"},
            "text-surrogate": {"text": "\ud800"},
            "text-cr": {"text": "PRIVATE\r"},
            "start-bool": {"start_line": True},
            "column-zero": {"start_column": 0},
            "end-column": {"end_column": 2},
            "end-line": {"end_line": 3},
            "line-count": {"line_count": 2},
            "part-zero": {"part_index": 0},
            "part-mismatch": {"part_index": 2},
            "bad-split": {"split_reasons": ("other",)},
        }
        chunk = replace(chunk, **changes[kind])
    return replace(value, embeddings=(replace(value.embeddings[0], chunk=chunk),))


@pytest.mark.parametrize(
    "kind",
    [
        "nan",
        "infinity",
        "float32-overflow",
        "bool",
        "string",
        "huge-int",
        "short-vector",
        "empty",
        "too-many",
        "duplicate",
        "no-files",
        "duplicate-files",
        "wrong-scope",
        "wrong-workspace",
        "wrong-space",
        "wrong-dimensions",
        "bool-dimensions",
        "wrong-model",
        "no-model",
        "bad-model",
        "no-requests",
        "too-many-requests",
        "negative-usage",
        "half-usage",
        "inverted-usage",
        "bool-usage",
        "false-completeness",
        "hidden-incomplete",
        "unknown-reason",
        "wrong-strategy",
        "wrong-policy",
        "trusted-content",
        "wrong-source",
        "bad-parser",
        "symbol-path",
        "symbol-hash",
        "symbol-start",
        "symbol-name",
        "symbol-qualified",
        "file-path",
        "file-hash",
        "file-large",
        "file-language",
        "chunk-id",
        "text-hash",
        "text-changed",
        "text-large",
        "text-bytes",
        "text-nul",
        "text-surrogate",
        "text-cr",
        "start-bool",
        "column-zero",
        "end-column",
        "end-line",
        "line-count",
        "part-zero",
        "part-mismatch",
        "bad-split",
    ],
)
def test_invalid_batch_never_opens_transaction_or_exposes_content(monkeypatch, kind):
    class ForbiddenSessions:
        def begin(self):
            pytest.fail("Invalid input opened a database transaction")

    monkeypatch.setattr(service, "SessionLocal", ForbiddenSessions())
    target = service.CodeEmbeddingTarget(1, "a" * 32, "b" * 32, "/PRIVATE", 1)
    with pytest.raises(service.CodeVectorStorageError) as caught:
        service.save_code_embedding_batch(
            target=target, source=mutate(batch(), kind), config=config()
        )
    assert str(caught.value) == "code_embedding_batch_invalid"
    assert caught.value.code == "code_embedding_batch_invalid"


@pytest.mark.parametrize("revision", [True, 0, -1, 2**63])
def test_invalid_binding_snapshot_rejected_before_transaction(monkeypatch, revision):
    monkeypatch.setattr(service, "SessionLocal", None)
    target = service.CodeEmbeddingTarget(1, "a" * 32, "b" * 32, "/PRIVATE", revision)
    with pytest.raises(
        service.CodeVectorStorageError, match="code_embedding_batch_invalid"
    ):
        service.save_code_embedding_batch(
            target=target, source=batch(), config=config()
        )
