"""同一真实PG召回Run供两路与RRF评分；不重复生成或查询。"""

import json
import os
from pathlib import Path

import httpx

from app.services.workspace.files.code_retrieval_evaluation import evaluate, lexical_baseline
from app.services.workspace.files.code_rrf_evaluation import fuse_rrf
from tests.workspace.files.test_code_vector_evaluation import (
    FIXTURE, closed_clients, collect, database, prepared, snapshot, target,
)

__all__ = ["closed_clients", "database", "prepared", "target"]


def test_three_strategy_comparison(prepared, database, closed_clients, tmp_path):
    dataset, texts, _, _ = prepared
    before = snapshot(database)
    lexical = lexical_baseline(dataset, texts)
    vector = collect(prepared)
    fusion = fuse_rrf(dataset, lexical, vector)
    # 所有策略共享同次快照、任务集和K；不拿gold挑参数，也不再请求模型。
    report = {
        "schema_version": 1,
        "literal": evaluate(dataset, lexical, k=3),
        "controlled_vector": evaluate(dataset, vector, k=3),
        "rrf": evaluate(dataset, fusion.run, k=3),
        "fusion": {"rank_constant": fusion.rank_constant, "failure_policy": fusion.failure_policy, "diagnostics": fusion.diagnostics},
    }
    prior = json.loads((FIXTURE / "vector-comparison.json").read_text())
    assert report["literal"] == prior["literal"]
    assert report["controlled_vector"] == prior["controlled_vector"]
    assert report["rrf"]["error_count"] == 0
    assert report["rrf"]["no_answer_false_positive_count"] == 2
    assert snapshot(database) == before
    assert len(closed_clients) == 9 and all(c.is_closed for c in closed_clients)
    assert report == json.loads((FIXTURE / "rrf-comparison.json").read_text())
    output = Path(os.environ.get("CODE_VECTOR_EVALUATION_REPORT", str(tmp_path / "rrf.json")))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")


def test_vector_failure_does_not_become_successful_fusion(prepared, closed_clients):
    dataset, texts, _, _ = prepared
    vector = collect(prepared, lambda request: httpx.Response(503, json={"error": "fixture"}))
    fusion = fuse_rrf(dataset, lexical_baseline(dataset, texts), vector)
    report = evaluate(dataset, fusion.run)
    assert report["error_count"] == 8 and report["no_answer_error_count"] == 2
    assert report["recall_at_k"] == report["mrr_at_k"] == 0
    assert all(p.status == "error" and not p.citations for p in fusion.run.predictions)
    assert len(closed_clients) == 9 and all(c.is_closed for c in closed_clients)
