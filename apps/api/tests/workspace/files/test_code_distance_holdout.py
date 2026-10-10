"""冻结来源、数据角色与报告边界；替身数据不调用真实模型。"""

from copy import deepcopy
import json
from pathlib import Path
import runpy
import sys

import pytest

from app.services.workspace.files.code_abstention_evaluation import Split
from app.services.workspace.files.code_distance_calibration import canonical_digest
from app.services.workspace.files.code_distance_holdout import evaluate_holdout, validate_holdout_inputs
from app.services.workspace.files.code_provider_evaluation import FIXTURE, ProviderPlan
from app.services.workspace.files.code_retrieval_evaluation import load_dataset, dataset_digest
from app.services.workspace.files.code_vector_evaluation import controlled_config

DIRECTORY = FIXTURE.parent / "distance-v1"


def inputs():
    baseline, _ = load_dataset(FIXTURE)
    development = Split.model_validate_json((DIRECTORY / "development.json").read_bytes())
    holdout = Split.model_validate_json((DIRECTORY / "holdout.json").read_bytes())
    frozen = json.loads((DIRECTORY / "frozen-policy.json").read_text())
    development_report = json.loads((DIRECTORY / "development-report.json").read_text())
    return development, holdout, baseline, frozen, development_report, canonical_digest(frozen)


def sample():
    args = inputs()
    policy = validate_holdout_inputs(*args)
    holdout = args[1]
    rows = []
    for query in holdout.dataset.queries:
        gold = set(query.relevant_source_ids)
        ordered = sorted(holdout.dataset.sources, key=lambda s: (s.id not in gold, s.id))
        nearest = 0.3 if gold else 0.7
        rows.append({"query_id": query.id, "status": "ok", "answerable": bool(gold), "nearest_distance": nearest,
            "candidates": [{"source_id": s.id, "rank": rank, "distance": nearest+(rank-1)*0.01,
                            "relevant": s.id in gold} for rank,s in enumerate(ordered,1)]})
    manifest = {"dataset_role": "distance-holdout", "dataset_sha256": dataset_digest(holdout.dataset),
                "source_count": 8, "query_count": 8, "config_sha256": policy.config_sha256,
                "requested_model": policy.requested_model, "expected_response_model": policy.response_model, "dimensions": policy.dimensions}
    return holdout, {"mode": "real", "manifest": manifest, "distance_observation": {"metric": "cosine_distance", "direction": "lower_is_closer", "rows": rows}}, policy


def test_applies_frozen_rule_without_mutation():
    holdout, report, policy = sample()
    before = deepcopy(report)
    result = evaluate_holdout(holdout, report, policy)
    assert result["threshold_milli"] == 500
    assert result["counts"]["correct_abstention"] == 4
    assert result["false_refusal_rate"] == result["false_acceptance_rate"] == "0"
    assert result["positive_recall_at_3_after"] == "1"
    assert report == before
    assert result["report_sha256"] == canonical_digest(report)


def test_failures_empty_false_refusal_and_false_acceptance_are_separate():
    holdout, report, policy = sample()
    rows = report["distance_observation"]["rows"]
    for index,status in ((0,"error"),(1,"ok"),(4,"error"),(5,"ok")):
        rows[index].update(status=status, candidates=[], nearest_distance=None)
    for index,distance in ((2,0.9),(6,0.1)):
        rows[index]["nearest_distance"] = distance
        for rank,c in enumerate(rows[index]["candidates"]): c["distance"] = distance+rank*0.01
    result = evaluate_holdout(holdout,report,policy)
    assert result["counts"] == {"false_refusal":1,"false_acceptance":1,"correct_abstention":1,
        "positive_no_candidates":1,"negative_no_candidates":1,"positive_error":1,"negative_error":1}
    assert result["positive_recall_at_3_after"] == "1/4"
    assert result["false_refusal_rate"] == result["false_acceptance_rate"] == "1/4"


@pytest.mark.parametrize("mode", ["frozen","development_report","development","corpus","role","observed_id","normalized_text"])
def test_provenance_and_overlap_rejected(mode):
    dev,holdout,base,frozen,raw,digest = inputs()
    if mode == "frozen": frozen["policy"]["threshold_milli"] = 600
    elif mode == "development_report": raw["mode"] = "tampered"
    elif mode == "development": dev=dev.model_copy(update={"dataset":dev.dataset.model_copy(update={"dataset_id":"changed"})})
    elif mode == "corpus": holdout=holdout.model_copy(update={"dataset":holdout.dataset.model_copy(update={"sources":holdout.dataset.sources[:-1]})})
    elif mode == "role": holdout=holdout.model_copy(update={"role":"development"})
    else:
        changes = {"id":base.queries[0].id} if mode == "observed_id" else {"query":"  " + dev.dataset.queries[0].query.upper() + "   "}
        queries=[holdout.dataset.queries[0].model_copy(update=changes),*holdout.dataset.queries[1:]]
        holdout=holdout.model_copy(update={"dataset":holdout.dataset.model_copy(update={"queries":queries})})
    with pytest.raises(ValueError): validate_holdout_inputs(dev,holdout,base,frozen,raw,digest)


@pytest.mark.parametrize("mode", ["role","mode","model","config","dimension","dataset","metric","duplicate","missing_source","rank","order","nan","label","nearest","error_hits"])
def test_invalid_holdout_report_rejected(mode):
    holdout,report,policy=sample()
    row=report["distance_observation"]["rows"][0]
    if mode == "role": report["manifest"]["dataset_role"]="distance-development"
    elif mode == "mode": report["mode"]="controlled"
    elif mode == "model": report["manifest"]["expected_response_model"]="other"
    elif mode == "config": report["manifest"]["config_sha256"]="0"*64
    elif mode == "dimension": report["manifest"]["dimensions"]=3
    elif mode == "dataset": report["manifest"]["dataset_sha256"]="0"*64
    elif mode == "metric": report["distance_observation"]["metric"]="other"
    elif mode == "duplicate": report["distance_observation"]["rows"][1]=deepcopy(row)
    elif mode == "missing_source": row["candidates"].pop()
    elif mode == "rank": row["candidates"][0]["rank"]=False
    elif mode == "order": row["candidates"][1]["distance"]=0.1
    elif mode == "nan": row["candidates"][0]["distance"]=float("nan")
    elif mode == "label": row["candidates"][0]["relevant"]=False
    elif mode == "nearest": row["nearest_distance"]=0.1
    else: row["status"]="error"
    with pytest.raises(ValueError): evaluate_holdout(holdout,report,policy)


def test_model_change_fails_preflight_before_network():
    with pytest.raises(ValueError,match="model_space_mismatch"):
        ProviderPlan(controlled_config(),"fixture",dataset_role="distance-holdout").preflight()


def test_holdout_cli_preserves_frozen_and_existing_output(monkeypatch,tmp_path):
    _,report,_=sample()
    raw=tmp_path/"raw.json"; raw.write_text(json.dumps(report))
    output=tmp_path/"result.json"
    script=Path(__file__).resolve().parents[5]/"scripts/evaluate_distance_holdout.py"
    before=(DIRECTORY/"frozen-policy.json").read_bytes()
    monkeypatch.setattr(sys,"argv",[str(script),"--report",str(raw),"--output",str(output)])
    main=runpy.run_path(str(script))["main"]
    assert main()==0
    saved=output.read_bytes()
    assert main()==2 and output.read_bytes()==saved
    assert (DIRECTORY/"frozen-policy.json").read_bytes()==before
