"""固定开发集的查询级拒答校准；离线消费报告，不读取留出或调用模型。"""

from fractions import Fraction
from hashlib import sha256
import json
import math
from typing import Literal

from pydantic import Field

from app.services.workspace.files.code_abstention_evaluation import Split, corpus_digest
from app.services.workspace.files.code_retrieval_evaluation import Record, dataset_digest

# 在读取开发集响应前固定：余弦距离0～2，每0.1一个候选；不是概率阈值。
THRESHOLDS_MILLI = tuple(range(0, 2001, 100))
RULE = "query-nearest-cosine-lte-v1"
OBJECTIVE = "equal-query-false-refusal-and-false-acceptance-v1"


class FrozenDistancePolicy(Record):
    schema_version: Literal[1]
    rule: Literal["query-nearest-cosine-lte-v1"]
    objective: Literal["equal-query-false-refusal-and-false-acceptance-v1"]
    threshold_milli: int = Field(ge=0, le=2000)
    corpus_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    development_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    requested_model: str
    response_model: str
    dimensions: int = Field(ge=1, le=4096)
    # 保存规则搜索空间与确定性并列裁决，避免只有一个不可解释的阈值。
    grid_milli: list[int]
    tie_break: Literal["loss,false_acceptance,lower_threshold"]
    status: Literal["development_only_not_validated_on_holdout"]


def canonical_digest(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def decide(nearest: float | None, *, status: str, threshold_milli: int) -> str:
    """运行决策不读取gold；失败、无候选与主动拒答互不替代。"""
    if type(threshold_milli) is not int or not 0 <= threshold_milli <= 2000:
        raise ValueError("invalid_distance_threshold")
    if status == "error":
        if nearest is not None:
            raise ValueError("error_with_distance")
        return "retrieval_error"
    if status != "ok":
        raise ValueError("invalid_distance_status")
    if nearest is None:
        return "no_candidates"
    if type(nearest) not in (int, float) or not math.isfinite(nearest) or not 0 <= nearest <= 2:
        raise ValueError("invalid_distance")
    return "retained" if nearest <= threshold_milli / 1000 else "abstained"


def calibrate_distance(development: Split, report: dict) -> dict:
    if development.role != "development" or report.get("mode") != "real":
        raise ValueError("real_development_required")
    dataset = development.dataset
    manifest = report["manifest"]
    if (manifest.get("dataset_role") != "distance-development"
            or manifest["dataset_sha256"] != dataset_digest(dataset)
            or manifest["source_count"] != len(dataset.sources)
            or manifest["query_count"] != len(dataset.queries)):
        raise ValueError("development_report_mismatch")
    positive_count = sum(bool(q.relevant_source_ids) for q in dataset.queries)
    negative_count = len(dataset.queries) - positive_count
    if not positive_count or not negative_count:
        raise ValueError("development_needs_both_classes")
    observation = report["distance_observation"]
    if observation.get("metric") != "cosine_distance" or observation.get("direction") != "lower_is_closer":
        raise ValueError("distance_metric_mismatch")
    rows = observation["rows"]
    if len(rows) != len(dataset.queries) or len({r["query_id"] for r in rows}) != len(rows):
        raise ValueError("distance_query_mismatch")
    by_id = {r["query_id"]: r for r in rows}
    if set(by_id) != {q.id for q in dataset.queries}:
        raise ValueError("distance_query_mismatch")
    sources = {s.id for s in dataset.sources}
    for query in dataset.queries:
        row = by_id[query.id]
        if row["status"] != "ok" or not row["candidates"]:
            # 不从失败开发集冻结阈值；不能通过剔除失败题优化成绩。
            raise ValueError("development_retrieval_incomplete")
        candidates = row["candidates"]
        if len(candidates) != len(sources) or {c["source_id"] for c in candidates} != sources:
            raise ValueError("distance_source_mismatch")
        previous = -1.0
        for rank, candidate in enumerate(candidates, 1):
            distance = candidate["distance"]
            decide(distance, status="ok", threshold_milli=0)
            if (type(candidate["rank"]) is not int or candidate["rank"] != rank or distance < previous
                    or candidate["relevant"] is not (candidate["source_id"] in query.relevant_source_ids)):
                raise ValueError("distance_candidate_mismatch")
            previous = distance
        if row["nearest_distance"] != candidates[0]["distance"] or row["answerable"] is not bool(query.relevant_source_ids):
            raise ValueError("distance_query_mismatch")
    trials = []
    choices = []
    for threshold in THRESHOLDS_MILLI:
        false_refusal = false_acceptance = 0
        recall = Fraction()
        decisions = []
        for query in dataset.queries:
            row = by_id[query.id]
            decision = decide(row["nearest_distance"], status=row["status"], threshold_milli=threshold)
            retained = decision == "retained"
            gold = set(query.relevant_source_ids)
            false_refusal += bool(gold) and not retained
            false_acceptance += not gold and retained
            if gold and retained:
                recall += Fraction(len(gold & {c["source_id"] for c in row["candidates"][:3]}), len(gold))
            decisions.append({"query_id": query.id, "decision": decision})
        fnr = Fraction(false_refusal, positive_count)
        fpr = Fraction(false_acceptance, negative_count)
        loss = (fnr + fpr) / 2
        trials.append({"threshold_milli": threshold, "balanced_loss": str(loss),
                       "false_refusal_count": false_refusal, "false_acceptance_count": false_acceptance,
                       "false_refusal_rate": str(fnr), "false_acceptance_rate": str(fpr),
                       "positive_recall_at_3": str(recall / positive_count), "decisions": decisions})
        choices.append((loss, fpr, threshold))
    threshold = min(choices)[2]
    policy = FrozenDistancePolicy(schema_version=1, rule=RULE, objective=OBJECTIVE, threshold_milli=threshold,
        corpus_sha256=corpus_digest(dataset), development_sha256=dataset_digest(dataset),
        report_sha256=canonical_digest(report), config_sha256=manifest["config_sha256"],
        requested_model=manifest["requested_model"], response_model=manifest["expected_response_model"],
        dimensions=manifest["dimensions"], grid_milli=list(THRESHOLDS_MILLI),
        tie_break="loss,false_acceptance,lower_threshold", status="development_only_not_validated_on_holdout")
    return {"schema_version": 1, "policy": policy.model_dump(), "positive_count": positive_count,
            "negative_count": negative_count, "trials": trials,
            "selected": next(t for t in trials if t["threshold_milli"] == threshold),
            "limits": ["query-level rejection; retained candidates are not individually filtered",
                       "development score is not holdout performance", "same small authored corpus",
                       "error or empty development retrieval prevents freezing", "not enabled in product"]}
