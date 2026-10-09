"""两阶段独立命令：先开发集校准；冻结后才读取留出集，不现场重新调参。"""

import json
import os
from pathlib import Path

from app.services.workspace.files.code_retrieval_evaluation import evaluate, lexical_baseline, read_json
from app.services.workspace.files.code_rrf_evaluation import fuse_rrf
from app.services.workspace.files import code_abstention_evaluation as service
from tests.workspace.files.test_code_vector_evaluation import (
    FIXTURE, closed_clients, collect, database, prepared, snapshot, target,
)

__all__ = ["closed_clients", "database", "prepared", "target"]
SUITE = FIXTURE.parent / "abstention-v1"


def split_for(role, corpus):
    split = service.Split.model_validate(read_json(SUITE / f"{role}.json"))
    service.validate_split(split, corpus)
    assert split.role == role
    return split


def ranked(prepared, split):
    _, texts, scope, batch = prepared
    vector = collect((split.dataset, texts, scope, batch))
    lexical = lexical_baseline(split.dataset, texts)
    return fuse_rrf(split.dataset, lexical, vector).run


def publish(value, tmp_path):
    output = Path(os.environ.get("CODE_ABSTENTION_REPORT", str(tmp_path / "report.json")))
    output.write_text(json.dumps(value, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")


def test_development_calibration(prepared, database, closed_clients, tmp_path):
    split = split_for("development", prepared[0])
    before = snapshot(database)
    run = ranked(prepared, split)
    policy, trials = service.calibrate(split, prepared[1], run)
    result = service.apply_policy(split.dataset, prepared[1], run, policy)
    report = {"phase": "development", "policy": policy.model_dump(), "trials": trials,
              "before": evaluate(split.dataset, run), "after": service.refusal_metrics(split.dataset, result)}
    assert snapshot(database) == before
    assert len(closed_clients) == 1 + len(split.dataset.queries) and all(c.is_closed for c in closed_clients)
    assert report == read_json(SUITE / f"{split.role}-report.json")
    publish(report, tmp_path)


def test_frozen_holdout_evaluation(prepared, database, closed_clients, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("holdout must not calibrate")

    monkeypatch.setattr(service, "calibrate", forbidden)
    # 先从持久文件加载冻结参数。此函数不调用calibrate，不保存新策略。
    policy_path = Path(os.environ.get("CODE_ABSTENTION_POLICY", str(SUITE / "policy.json")))
    original_policy = policy_path.read_bytes()
    policy = service.FrozenPolicy.model_validate(read_json(policy_path))
    development = split_for("development", prepared[0])
    holdout = split_for("holdout", prepared[0])
    service.validate_holdout(development, holdout, policy)
    before = snapshot(database)
    run = ranked(prepared, holdout)
    result = service.apply_policy(holdout.dataset, prepared[1], run, policy)
    report = {"phase": "holdout", "policy": policy.model_dump(),
              "before": evaluate(holdout.dataset, run), "after": service.refusal_metrics(holdout.dataset, result)}
    assert snapshot(database) == before
    assert len(closed_clients) == 1 + len(holdout.dataset.queries) and all(c.is_closed for c in closed_clients)
    assert policy_path.read_bytes() == original_policy
    assert report == read_json(SUITE / "holdout-report.json")
    publish(report, tmp_path)
