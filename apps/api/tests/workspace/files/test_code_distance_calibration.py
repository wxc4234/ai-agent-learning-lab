"""校准只使用开发标注；保留失败、不覆盖冻结结果、不触碰留出。"""

from copy import deepcopy
import json
from pathlib import Path
import runpy
import sys

import httpx
import pytest

from app.services.workspace.files.code_abstention_evaluation import Split
from app.services.workspace.files.code_distance_calibration import calibrate_distance, decide, canonical_digest
from app.services.workspace.files.code_provider_evaluation import ProviderPlan, SendBudget, FIXTURE
from app.services.workspace.files.code_vector_evaluation import controlled_config


def fixture():
    plan = ProviderPlan(controlled_config(), "fixture", dataset_role="distance-development")
    dataset, _ = plan.load_dataset()
    rows = []
    for query in dataset.queries:
        gold = set(query.relevant_source_ids)
        ordered = sorted(dataset.sources, key=lambda s: (s.id not in gold, s.id))
        nearest = 0.4 if gold else 0.7
        rows.append({"query_id": query.id, "status": "ok", "answerable": bool(gold), "nearest_distance": nearest,
                     "candidates": [{"source_id": s.id, "rank": rank, "distance": nearest + (rank - 1) * 0.01,
                                     "relevant": s.id in gold} for rank, s in enumerate(ordered, 1)]})
    return Split(role="development", dataset=dataset), {
        "mode": "real", "manifest": plan.preflight(),
        "distance_observation": {"metric": "cosine_distance", "direction": "lower_is_closer", "rows": rows}}


def test_grid_objective_freeze_and_report_binding():
    split, report = fixture()
    before = deepcopy(report)
    result = calibrate_distance(split, report)
    assert result["policy"]["threshold_milli"] == 400
    assert result["policy"]["grid_milli"] == list(range(0, 2001, 100))
    assert result["policy"]["report_sha256"] == canonical_digest(report)
    assert result["policy"]["config_sha256"] == report["manifest"]["config_sha256"]
    assert result["selected"]["balanced_loss"] == "0"
    assert result["selected"]["positive_recall_at_3"] == "1"
    assert result["trials"][0]["false_refusal_rate"] == "1"
    assert result["trials"][-1]["false_acceptance_rate"] == "1"
    assert result["positive_count"] == result["negative_count"] == 4
    assert report == before


def test_loss_tie_prefers_fewer_false_acceptances():
    split, report = fixture()
    # 正例比负例更远，完全拒答和完全接受的损失均为1/2；选前者。
    for row in report["distance_observation"]["rows"]:
        nearest = 0.8 if row["answerable"] else 0.2
        row["nearest_distance"] = nearest
        for c in row["candidates"]:
            c["distance"] = nearest + c["rank"] * 0.01 - 0.01
        row["nearest_distance"] = row["candidates"][0]["distance"]
    result = calibrate_distance(split, report)
    assert result["selected"]["balanced_loss"] == "1/2"
    assert result["policy"]["threshold_milli"] == 0


@pytest.mark.parametrize("nearest,status,expected", [(0.5,"ok","retained"),(0.500001,"ok","abstained"),(None,"ok","no_candidates"),(None,"error","retrieval_error")])
def test_decisions_do_not_use_labels(nearest, status, expected):
    assert decide(nearest, status=status, threshold_milli=500) == expected


@pytest.mark.parametrize("mode", ["holdout","controlled","role","dataset","metric","query","duplicate","error","empty","missing","rank","order","nan","bool","label","nearest"])
def test_invalid_or_incomplete_development_cannot_freeze(mode):
    split, report = fixture()
    row = report["distance_observation"]["rows"][0]
    if mode == "holdout": split = split.model_copy(update={"role": "holdout"})
    elif mode == "controlled": report["mode"] = "controlled"
    elif mode == "role": report["manifest"]["dataset_role"] = "baseline"
    elif mode == "dataset": report["manifest"]["dataset_sha256"] = "0" * 64
    elif mode == "metric": report["distance_observation"]["metric"] = "similarity"
    elif mode == "query": row["query_id"] = "unknown"
    elif mode == "duplicate": report["distance_observation"]["rows"][1] = deepcopy(row)
    elif mode == "error": row["status"] = "error"
    elif mode == "empty": row["candidates"] = []
    elif mode == "missing": row["candidates"].pop()
    elif mode == "rank": row["candidates"][0]["rank"] = True
    elif mode == "order": row["candidates"][1]["distance"] = 0.1
    elif mode == "nan": row["candidates"][0]["distance"] = float("nan")
    elif mode == "bool": row["candidates"][0]["distance"] = True
    elif mode == "label": row["candidates"][0]["relevant"] = False
    else: row["nearest_distance"] = 0.9
    with pytest.raises(ValueError): calibrate_distance(split, report)


def test_development_whitelist_rejects_baseline_text():
    plan = ProviderPlan(controlled_config(), "fixture", dataset_role="distance-development")
    budget = SendBudget(plan)
    baseline, _ = ProviderPlan(controlled_config(), "fixture").load_dataset()
    with pytest.raises(ValueError):
        budget.check(httpx.Request("POST", plan.config.base_url + "/embeddings", json=plan.payload([baseline.queries[0].query])))
    dataset, _ = plan.load_dataset()
    budget.check(httpx.Request("POST", plan.config.base_url + "/embeddings", json=plan.payload([dataset.queries[0].query])))
    assert budget.requests == 1


def test_split_role_and_overlap_rejected(monkeypatch, tmp_path):
    import app.services.workspace.files.code_provider_evaluation as module
    # 仅替换读取固定开发文件的位置；基线语料仍经真实加载验证。
    original = module.load_dataset
    monkeypatch.setattr(module, "load_dataset", lambda _: original(FIXTURE))
    monkeypatch.setattr(module, "FIXTURE", tmp_path / "v1")
    directory = tmp_path / "distance-v1"
    directory.mkdir()
    base, _ = original(FIXTURE)
    for role in ("holdout", "development"):
        (directory / "development.json").write_text(Split(role=role, dataset=base).model_dump_json())
        with pytest.raises(ValueError): ProviderPlan(controlled_config(), "fixture", dataset_role="distance-development").preflight()


def test_cli_freezes_once_and_preserves_existing_output(monkeypatch, tmp_path):
    _, report = fixture()
    raw = tmp_path / "report.json"
    raw.write_text(json.dumps(report))
    output = tmp_path / "frozen.json"
    script = Path(__file__).resolve().parents[5] / "scripts/calibrate_code_distance.py"
    monkeypatch.setattr(sys, "argv", [str(script), "--report", str(raw), "--output", str(output)])
    main = runpy.run_path(str(script))["main"]
    assert main() == 0
    before = output.read_bytes()
    assert main() == 2 and output.read_bytes() == before


@pytest.mark.parametrize("answerable", [True, False])
def test_single_class_development_rejected(answerable):
    from app.services.workspace.files.code_retrieval_evaluation import dataset_digest
    split, report = fixture()
    selected = [q for q in split.dataset.queries if bool(q.relevant_source_ids) == answerable]
    split = split.model_copy(update={"dataset": split.dataset.model_copy(update={"queries": selected})})
    report["manifest"]["dataset_sha256"] = dataset_digest(split.dataset)
    report["manifest"]["query_count"] = len(selected)
    with pytest.raises(ValueError, match="development_needs_both_classes"):
        calibrate_distance(split, report)
