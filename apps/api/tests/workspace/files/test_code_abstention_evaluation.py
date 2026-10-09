"""过滤只看查询/候选，标签只计分；失败不能充当正确拒答。"""

from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.services.workspace.files import code_abstention_evaluation as service
from app.services.workspace.files.code_retrieval_evaluation import (
    Dataset, Prediction, Run, dataset_digest,
)
from tests.workspace.files.test_code_retrieval_evaluation import small

__all__ = ["small"]


def setup_case(dataset, ranked=None):
    texts = {"A": "two relevant", "B": "two relevant", "C": "one relevant"}
    run = Run(schema_version=1, dataset_sha256=dataset_digest(dataset),
              strategy=service.STRATEGY, citation_origin="retrieval_reference",
              predictions=ranked or [Prediction(query_id=q.id, status="ok", ranked_source_ids=["C", "A", "A", "B"], citations=[]) for q in dataset.queries])
    policy = service.FrozenPolicy(schema_version=1, rule=service.RULE,
                                  input_strategy=service.STRATEGY, objective=service.OBJECTIVE,
                                  corpus_sha256=service.corpus_digest(dataset),
                                  development_sha256=dataset_digest(dataset), min_shared_terms=2, evaluation_k=3)
    return texts, run, policy


def test_filter_order_dedup_and_rebuilt_citation(small):
    texts, run, policy = setup_case(small)
    result = service.apply_policy(small, texts, run, policy)
    assert result.run.predictions[0].ranked_source_ids == ["A", "B"]
    assert result.run.predictions[0].citations[0].source_id == "A"
    report = service.refusal_metrics(small, result)
    assert report["recall_at_k"] == 1
    assert report["abstention"]["correct_abstention_count"] == 1


@pytest.mark.parametrize("status,ranked,decision", [
    ("error", [], "retrieval_error"), ("ok", [], "no_candidates"),
    ("ok", ["C"], "abstained"), ("ok", ["A"], "retained"),
])
def test_distinct_outcomes(small, status, ranked, decision):
    predictions = [Prediction(query_id="q1", status=status, ranked_source_ids=ranked, citations=[]),
                   Prediction(query_id="q2", status="ok", ranked_source_ids=[], citations=[]),
                   Prediction(query_id="q3", status="error", ranked_source_ids=[], citations=[])]
    texts, run, policy = setup_case(small, predictions)
    result = service.apply_policy(small, texts, run, policy)
    report = service.refusal_metrics(small, result)
    assert result.decisions[0]["decision"] == decision
    assert report["abstention"]["false_refusal_count"] == int(decision == "abstained")
    assert report["abstention"]["correct_abstention_count"] == 0
    assert report["abstention"]["retrieval_error_count"] == 1 + int(status == "error")
    assert report["abstention"]["positive_no_candidates_count"] == 1 + int(decision == "no_candidates")


def test_calibration_fixed_grid_and_low_threshold_tie(small):
    texts, run, _ = setup_case(small)
    policy, trials = service.calibrate(service.Split(role="development", dataset=small), texts, run)
    assert policy.min_shared_terms == 1
    assert [t["threshold"] for t in trials] == [1, 2, 3, 4]
    assert trials[0]["balanced_loss"] == trials[1]["balanced_loss"] == "0"


@pytest.mark.parametrize("change", ["holdout", "no_negatives", "error"])
def test_calibration_rejects_invalid_development(small, change):
    if change == "no_negatives":
        small = small.model_copy(update={"queries": small.queries[:2]})
    texts, run, _ = setup_case(small)
    if change == "error":
        run = run.model_copy(update={"predictions": [Prediction(query_id=q.id, status="error", ranked_source_ids=[], citations=[]) for q in small.queries]})
    with pytest.raises(ValueError):
        service.calibrate(service.Split(role="holdout" if change == "holdout" else "development", dataset=small), texts, run)


@pytest.mark.parametrize("field,value", [("min_shared_terms", 0), ("min_shared_terms", 5),
    ("min_shared_terms", True), ("evaluation_k", 3.0), ("schema_version", True), ("rule", "unknown")])
def test_policy_strict(small, field, value):
    _, _, policy = setup_case(small)
    with pytest.raises(ValidationError):
        service.FrozenPolicy.model_validate(dict(policy.model_dump(), **{field: value}))


@pytest.mark.parametrize("change", ["strategy", "corpus", "digest", "texts"])
def test_filter_rejects_mismatched_inputs(small, change):
    texts, run, policy = setup_case(small)
    if change == "strategy":
        run = run.model_copy(update={"strategy": "other"})
    elif change == "corpus":
        policy = policy.model_copy(update={"corpus_sha256": "0" * 64})
    elif change == "digest":
        run = run.model_copy(update={"dataset_sha256": "0" * 64})
    else:
        texts.pop("A")
    with pytest.raises(ValueError):
        service.apply_policy(small, texts, run, policy)


def test_gold_does_not_change_decisions(small):
    texts, run, policy = setup_case(small)
    original = service.apply_policy(small, texts, run, policy)
    altered = small.model_copy(update={"queries": [q.model_copy(update={"relevant_source_ids": []}) for q in small.queries]})
    changed = service.apply_policy(altered, texts, run.model_copy(update={"dataset_sha256": dataset_digest(altered)}), policy)
    assert changed.decisions == original.decisions
    assert changed.run.predictions == original.run.predictions
    report = service.refusal_metrics(altered, changed)
    assert report["recall_at_k"] is None
    assert report["abstention"]["false_refusal_rate"] is None
    assert report["abstention"]["false_acceptance_count"] == 2


@pytest.mark.parametrize("change", ["id", "text", "corpus", "development"])
def test_holdout_rejects_leaks_and_policy_mismatch(small, change):
    _, _, policy = setup_case(small)
    dev = service.Split(role="development", dataset=small)
    queries = [q.model_copy(update={"id": "held-" + q.id, "query": "new " + q.query}) for q in small.queries]
    if change == "id":
        queries[0] = queries[0].model_copy(update={"id": small.queries[0].id})
    if change == "text":
        queries[0] = queries[0].model_copy(update={"query": "  TWO   RELEVANT sources "})
    holdout = service.Split(role="holdout", dataset=small.model_copy(update={"queries": queries}))
    if change == "corpus":
        policy = policy.model_copy(update={"corpus_sha256": "0" * 64})
    if change == "development":
        policy = policy.model_copy(update={"development_sha256": "0" * 64})
    with pytest.raises(ValueError):
        service.validate_holdout(dev, holdout, policy)


def test_split_duplicate_normalized_text(small):
    data = small.model_dump()
    data["queries"][1]["query"] = " TWO  RELEVANT SOURCES "
    with pytest.raises(ValueError, match="duplicate_normalized"):
        service.validate_split(service.Split(role="development", dataset=Dataset.model_validate(data)), small)


@pytest.mark.parametrize("phase,existing,same", [("calibrate", True, False), ("evaluate", False, False), ("calibrate", False, True)])
def test_cli_refuses_bad_paths_before_database(tmp_path, phase, existing, same):
    policy = tmp_path / "policy.json"
    if existing:
        policy.write_text("untouched")
    script = Path(__file__).resolve().parents[5] / "scripts/evaluate_code_abstention.py"
    result = subprocess.run([sys.executable, str(script), phase, "--policy", str(policy),
                             "--output", str(policy if same else tmp_path / "report.json")], capture_output=True, check=False)
    assert result.returncode == 2
    if existing:
        assert policy.read_text() == "untouched"
