"""评测适配的真实隔离PG专项；生成和查询仅使用受控HTTPX。"""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil

import httpx
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.models import Workspace
from app.repositories.workspace.workspace_repository import set_locked_workspace_root
from app.services.workspace.directory import workspace_path
from app.services.workspace.files import code_query_search, code_vector_search, code_vector_storage
from app.services.workspace.files.code_batch_generation import generate_and_save_code_batch
from app.services.workspace.files.code_retrieval_evaluation import Dataset, evaluate, lexical_baseline, load_dataset
from app.services.workspace.files import code_vector_evaluation as service
from tests.assertions import require_value
from tests.workspace.files.test_code_vector_storage import database, target
from tests.model.test_query_embeddings import closed_clients

__all__ = ["closed_clients", "database", "target"]
ROOT = Path(__file__).resolve().parents[5]
FIXTURE = ROOT / "apps/api/evaluations/code_retrieval/v1"


def snapshot(engine):
    with Session(engine) as session:
        return {table: session.scalars(text(f"SELECT to_jsonb(t)::text FROM {table} t ORDER BY id")).all() for table in ("code_embedding_batches", "code_embedding_vectors", "workspaces")}


@pytest.fixture
def prepared(database, target, tmp_path, monkeypatch, request):
    dataset, texts = load_dataset(FIXTURE)
    root = tmp_path / "corpus"
    shutil.copytree(FIXTURE / "corpus", root)
    # 即使后续生成/断言失败也删除本轮自有副本，不依赖pytest保留目录轮换。
    request.addfinalizer(lambda: shutil.rmtree(root))
    with Session(database) as session, session.begin():
        set_locked_workspace_root(require_value(session.scalar(select(Workspace))), str(root.resolve()))
    for module in (workspace_path, code_query_search, code_vector_search):
        monkeypatch.setattr(module, "SessionLocal", code_vector_storage.SessionLocal)
    scope = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    generation_calls = []

    def embedding(request):
        assert database.pool.checkedout() == 0
        generation_calls.append(request)
        return service.controlled_response(request)

    saved = asyncio.run(generate_and_save_code_batch(**scope, config=service.controlled_config(), transport=httpx.MockTransport(embedding)))
    assert saved.batch.chunk_count == 8 and not saved.coverage.incomplete_reasons
    assert saved.prompt_tokens is None and len(generation_calls) == saved.request_count == 1
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    yield dataset, texts, scope, saved.batch.batch_id
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}


def collect(prepared, handler=service.controlled_response):
    dataset, texts, scope, batch_id = prepared
    return asyncio.run(service.collect_vector_run(dataset, texts, **scope, batch_id=batch_id, transport=httpx.MockTransport(handler)))


def test_real_pipeline_comparison_and_no_mutation(prepared, database, closed_clients, tmp_path):
    dataset, texts, _, _ = prepared
    before = snapshot(database)
    calls = []

    def response(request):
        assert database.pool.checkedout() == 0
        calls.append(json.loads(request.content)["input"][0])
        return service.controlled_response(request)

    distance_queries = []

    def sql(connection, cursor, statement, parameters, context, executemany):
        if "<=>" in statement:
            distance_queries.append(statement)

    event.listen(database, "before_cursor_execute", sql)
    try:
        run = collect(prepared, response)
    finally:
        event.remove(database, "before_cursor_execute", sql)
    assert len(distance_queries) == 8
    assert calls == [q.query for q in dataset.queries]
    assert all(p.status == "ok" for p in run.predictions)
    vector = evaluate(dataset, run, k=3)
    literal = evaluate(dataset, lexical_baseline(dataset, texts), k=3)
    assert snapshot(database) == before
    assert all(client.is_closed for client in closed_clients)
    assert len(closed_clients) == 9  # 1次生成+8次查询，各自关闭，不复用外部HTTP。
    assert vector["query_count"] == literal["query_count"] == 8
    assert vector["dataset_sha256"] == literal["dataset_sha256"]
    assert vector["no_answer_false_positive_count"] == 2  # 无阈值Top K不能假装具备拒答。
    report = {"schema_version": 1, "literal": literal, "controlled_vector": vector,
              "evidence": {"generation_requests": 1, "query_requests": 8, "stored_chunks": 8,
                           "files_and_rows_unchanged": True, "http_clients_closed": True,
                           "provider_usage": None, "real_semantic_evaluation": False}}
    expected = FIXTURE / "vector-comparison.json"
    assert report == json.loads(expected.read_text())
    # 本轮产物写临时目录；只有外部显式命令才复制为版本化回归预期。
    output = Path(os.environ.get("CODE_VECTOR_EVALUATION_REPORT", str(tmp_path / "report.json")))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=4) + "\n")
    print(f"Comparison report: {output}")


@pytest.mark.parametrize("mode", ["provider", "version", "binding"])
def test_known_failures_stay_in_denominator(prepared, database, mode):
    calls = []
    if mode == "binding":
        with Session(database) as session, session.begin():
            require_value(session.scalar(select(Workspace))).binding_revision += 1

    def respond(request):
        calls.append(request)
        if mode == "provider":
            return httpx.Response(503, json={"error": "controlled failure"})
        payload = service.controlled_response(request).json()
        payload["model"] = "wrong-version"
        return httpx.Response(200, json=payload)

    report = evaluate(prepared[0], collect(prepared, respond), k=3)
    assert report["error_count"] == 8 and report["no_answer_error_count"] == 2
    assert report["recall_at_k"] == report["mrr_at_k"] == 0
    assert report["citation_relevance"] is None
    assert len(calls) == (0 if mode == "binding" else 8)


@pytest.fixture
def recalled(prepared):
    dataset, texts, scope, batch_id = prepared
    result = asyncio.run(code_query_search.search_code_query(
        dataset.queries[0].query, **scope, batch_id=batch_id, config=service.controlled_config(),
        response_model=service.MODEL, top_k=20, transport=httpx.MockTransport(service.controlled_response),
    ))
    return dataset, texts, scope, batch_id, result.recall


@pytest.mark.parametrize("mode", ["batch", "workspace", "file_hash", "text", "symbol", "line", "split", "duplicate", "truncated", "missing_hit"])
def test_mapping_rejects_incompatible_sources(recalled, mode):
    dataset, texts, scope, batch_id, result = recalled
    result = deepcopy(result)
    if mode == "batch":
        result = replace(result, batch_id="wrong")
    elif mode == "workspace":
        result.metadata["workspace_id"] = "wrong"
    elif mode == "file_hash":
        result.metadata["files"][0]["sha256"] = "0" * 64
    elif mode == "truncated":
        result.metadata["truncated"] = True
    elif mode == "missing_hit":
        result = replace(result, hits=result.hits[:-1])
    elif mode == "duplicate":
        result.hits[1].chunk.update(result.hits[0].chunk)
    else:
        chunk = result.hits[0].chunk
        if mode == "text":
            chunk["text"] = "wrong"
        elif mode == "symbol":
            chunk["symbol"]["qualified_name"] = "wrong"
        elif mode == "line":
            chunk["symbol"]["start_line"] += 1
        elif mode == "split":
            chunk["part_count"] = 2
    with pytest.raises(ValueError, match="evaluation_"):
        service.map_recall(dataset, texts, result, workspace_id=scope["workspace_id"], task_id=scope["task_id"], batch_id=batch_id, query_id=dataset.queries[0].id)


def test_labels_do_not_change_real_vector_ranking(prepared):
    first = collect(prepared)
    dataset, texts, scope, batch_id = prepared
    changed = dataset.model_dump()
    for query in changed["queries"]:
        query["relevant_source_ids"] = []
        query["rationale"] = "changed labels"
    second = collect((Dataset.model_validate(changed), texts, scope, batch_id))
    assert first.predictions == second.predictions
    assert first.dataset_sha256 != second.dataset_sha256


def test_cancellation_propagates_and_closes_client(prepared, database, closed_clients):
    async def respond(request):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        collect(prepared, respond)
    assert len(closed_clients) == 2 and all(c.is_closed for c in closed_clients)
    assert database.pool.checkedout() == 0


def test_mapping_failure_is_not_scored_as_retrieval_miss(prepared, monkeypatch):
    def refuse(*args, **kwargs):
        raise ValueError("evaluation_source_mismatch")
    monkeypatch.setattr(service, "map_recall", refuse)
    with pytest.raises(ValueError, match="evaluation_source_mismatch"):
        collect(prepared)
