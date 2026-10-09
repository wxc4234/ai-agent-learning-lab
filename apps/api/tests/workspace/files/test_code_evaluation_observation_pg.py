"""离线入口包裹现有边界；不修改生产函数，不持有事务跨模型等待。"""

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from app.services.workspace.files import code_batch_generation, code_query_search
from app.services.workspace.files.code_evaluation_observation import Observation
from app.services.workspace.files.code_retrieval_evaluation import evaluate, lexical_baseline, read_json
from app.services.workspace.files.code_rrf_evaluation import fuse_rrf
from app.services.workspace.files.code_abstention_evaluation import FrozenPolicy, apply_policy
from app.services.workspace.files.code_vector_search import CodeVectorSearchError
from tests.workspace.files import test_code_vector_evaluation as vector_test
from tests.workspace.files.test_code_vector_evaluation import (
    FIXTURE, closed_clients, collect, database, prepared, snapshot, target,
)

__all__ = ["closed_clients", "database", "prepared", "target"]


@pytest.mark.parametrize("mode", ["unknown", "reported", "provider_error", "recall_error", "cancelled"])
def test_observed_pipeline(request, monkeypatch, database, closed_clients, tmp_path, mode):
    observation = Observation()
    current = {"sample": "build"}
    original_build = vector_test.generate_and_save_code_batch
    original_generate = code_batch_generation.generate_code_embeddings
    original_query = code_query_search.generate_query_embedding
    original_recall = code_query_search.search_code_embedding_batch

    async def build(*args, **kwargs):
        with observation.span("build", "build", "end_to_end"):
            return await original_build(*args, **kwargs)

    async def generate(*args, **kwargs):
        kwargs["transport"] = observation.transport("build", "build", kwargs["transport"])
        with observation.span("build", "build", "embedding"):
            result = await original_generate(*args, **kwargs)
            observation.accept_usage("build", "build", result)
            return result

    async def query(*args, **kwargs):
        sample = current["sample"]
        kwargs["transport"] = observation.transport("query", sample, kwargs["transport"])
        with observation.span("query", sample, "embedding"):
            result = await original_query(*args, **kwargs)
            # 即使后续召回失败，已经报告的模型用量也不会回滚。
            observation.accept_usage("query", sample, result)
            return result

    def recall(*args, **kwargs):
        with observation.span("query", current["sample"], "recall"):
            if mode == "recall_error":
                raise CodeVectorSearchError("controlled_failure")
            return original_recall(*args, **kwargs)

    monkeypatch.setattr(vector_test, "generate_and_save_code_batch", build)
    monkeypatch.setattr(code_batch_generation, "generate_code_embeddings", generate)
    monkeypatch.setattr(code_query_search, "generate_query_embedding", query)
    monkeypatch.setattr(code_query_search, "search_code_embedding_batch", recall)
    # 包装在fixture建库前安装；仍由既有fixture负责临时项目/数据库清理。
    fixture = request.getfixturevalue("prepared")
    dataset, texts, scope, batch = fixture
    before = snapshot(database)
    policy = FrozenPolicy.model_validate(read_json(FIXTURE.parent / "abstention-v1/policy.json"))
    completed = []

    def respond(req):
        assert database.pool.checkedout() == 0
        if mode == "cancelled":
            raise asyncio.CancelledError()
        if mode == "provider_error":
            return httpx.Response(503)
        body = vector_test.service.controlled_response(req).json()
        if mode in ("reported", "recall_error"):
            body["usage"] = {"prompt_tokens": 10, "total_tokens": 12}
        return httpx.Response(200, json=body)

    for q in dataset.queries:
        current["sample"] = q.id
        observation.entry("query", q.id)
        subset = dataset.model_copy(update={"queries": [q]})
        try:
            with observation.span("query", q.id, "end_to_end"):
                with observation.span("query", q.id, "literal"):
                    literal = lexical_baseline(subset, texts)
                vector = collect((subset, texts, scope, batch), respond)
                if vector.predictions[0].status == "error":
                    raise RuntimeError("retrieval_failed")
                with observation.span("query", q.id, "fusion"):
                    fused = fuse_rrf(subset, literal, vector).run
                with observation.span("query", q.id, "filter"):
                    filtered = apply_policy(subset, texts, fused, policy)
            completed.append(evaluate(subset, filtered.run))
        except asyncio.CancelledError:
            break  # 后续题未执行，不虚构耗时或请求。
        except RuntimeError as exc:
            assert str(exc) == "retrieval_failed"

    report = observation.report()
    report["dataset_sha256"] = vector_test.service.dataset_digest(dataset)
    report["planned_queries"] = len(dataset.queries)
    report["started_queries"] = len([r for r in report["usage"] if r["phase"] == "query"])
    report["completed_queries"] = len(completed)
    report["unstarted_queries"] = report["planned_queries"] - report["started_queries"]
    report["quality_rows"] = completed
    report["usage_origin"] = "controlled fixture, not provider billing"
    assert snapshot(database) == before
    assert all(c.is_closed for c in closed_clients)
    assert len(closed_clients) == 1 + report["started_queries"]
    end = [r for r in report["latency"] if r["phase"] == "query" and r["stage"] == "end_to_end"]
    expected_status = "cancelled" if mode == "cancelled" else "error" if mode.endswith("error") else "ok"
    assert len(end) == 1 and end[0]["status"] == expected_status
    assert end[0]["count"] == (1 if mode == "cancelled" else 8)
    totals = {r["phase"]: r for r in report["usage_totals"]}
    assert totals["build"]["attempts"] == 1 and totals["build"]["total_tokens"] is None
    assert totals["query"]["attempts"] == report["started_queries"]
    assert totals["query"]["total_tokens"] == (96 if mode in ("reported", "recall_error") else None)
    assert all(r["cost"] is None for r in report["usage_totals"])
    assert report["completed_queries"] == (8 if mode in ("unknown", "reported") else 0)
    if mode in ("unknown", "reported"):
        assert all(len([s for s in report["spans"] if s["sample"] == q.id]) == 6 for q in dataset.queries)
    if mode == "unknown":
        output = Path(os.environ.get("CODE_OBSERVATION_REPORT", str(tmp_path / "observation.json")))
        output.write_text(json.dumps(report, ensure_ascii=False, indent=4) + "\n")
