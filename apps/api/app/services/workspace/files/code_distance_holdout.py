"""只应用已冻结规则的问题级留出评估；不在留出结果上搜索阈值。"""

from fractions import Fraction

from app.services.workspace.files.code_abstention_evaluation import Split, corpus_digest, validate_split, _normalized
from app.services.workspace.files.code_distance_calibration import (
    FrozenDistancePolicy, THRESHOLDS_MILLI, canonical_digest, decide,
)
from app.services.workspace.files.code_retrieval_evaluation import Dataset, dataset_digest


def validate_holdout_inputs(
    development: Split, holdout: Split, baseline: Dataset, frozen: dict,
    development_report: dict, expected_frozen_sha256: str,
) -> FrozenDistancePolicy:
    """在任何留出请求前绑定已有冻结文件，不能用本次结果改写它。"""
    if development.role != "development" or holdout.role != "holdout":
        raise ValueError("invalid_split_roles")
    if canonical_digest(frozen) != expected_frozen_sha256:
        raise ValueError("frozen_policy_changed")
    policy = FrozenDistancePolicy.model_validate(frozen["policy"])
    if (policy.development_sha256 != dataset_digest(development.dataset)
            or policy.report_sha256 != canonical_digest(development_report)
            or policy.grid_milli != list(THRESHOLDS_MILLI)
            or policy.threshold_milli not in THRESHOLDS_MILLI
            or frozen["selected"]["threshold_milli"] != policy.threshold_milli):
        raise ValueError("frozen_policy_provenance_mismatch")
    validate_split(development, baseline)
    validate_split(holdout, baseline)
    if corpus_digest(holdout.dataset) != policy.corpus_sha256:
        raise ValueError("policy_corpus_mismatch")
    for observed in (baseline, development.dataset):
        if ({q.id for q in observed.queries} & {q.id for q in holdout.dataset.queries}
                or {_normalized(q.query) for q in observed.queries} & {_normalized(q.query) for q in holdout.dataset.queries}):
            raise ValueError("holdout_overlaps_observed_queries")
    return policy


def validate_model_binding(policy: FrozenDistancePolicy, manifest: dict) -> None:
    if (manifest["config_sha256"] != policy.config_sha256
            or manifest["requested_model"] != policy.requested_model
            or manifest["expected_response_model"] != policy.response_model
            or manifest["dimensions"] != policy.dimensions):
        raise ValueError("holdout_model_space_mismatch")


def evaluate_holdout(holdout: Split, report: dict, policy: FrozenDistancePolicy) -> dict:
    if holdout.role != "holdout" or report.get("mode") != "real":
        raise ValueError("real_holdout_required")
    dataset = holdout.dataset
    manifest = report["manifest"]
    validate_model_binding(policy, manifest)
    if (manifest.get("dataset_role") != "distance-holdout"
            or manifest["dataset_sha256"] != dataset_digest(dataset)
            or corpus_digest(dataset) != policy.corpus_sha256
            or manifest["source_count"] != len(dataset.sources)
            or manifest["query_count"] != len(dataset.queries)):
        raise ValueError("holdout_report_mismatch")
    observation = report["distance_observation"]
    if observation.get("metric") != "cosine_distance" or observation.get("direction") != "lower_is_closer":
        raise ValueError("distance_metric_mismatch")
    rows = observation["rows"]
    if len(rows) != len(dataset.queries) or {r["query_id"] for r in rows} != {q.id for q in dataset.queries}:
        raise ValueError("holdout_queries_mismatch")
    by_id = {r["query_id"]: r for r in rows}
    sources = {s.id for s in dataset.sources}
    counts = {"false_refusal": 0, "false_acceptance": 0, "correct_abstention": 0,
              "positive_no_candidates": 0, "negative_no_candidates": 0, "positive_error": 0, "negative_error": 0}
    positive_count = sum(bool(q.relevant_source_ids) for q in dataset.queries)
    negative_count = len(dataset.queries) - positive_count
    if not positive_count or not negative_count:
        raise ValueError("holdout_needs_both_classes")
    decisions = []
    before_recall = after_recall = Fraction()
    for query in dataset.queries:
        row = by_id[query.id]
        candidates = row["candidates"]
        gold = set(query.relevant_source_ids)
        if row["answerable"] is not bool(gold):
            raise ValueError("holdout_label_mismatch")
        if row["status"] == "error" and candidates:
            raise ValueError("error_with_candidates")
        if candidates:
            if len(candidates) != len(sources) or {c["source_id"] for c in candidates} != sources:
                raise ValueError("holdout_sources_mismatch")
            previous = -1.0
            for rank, c in enumerate(candidates, 1):
                decide(c["distance"], status="ok", threshold_milli=policy.threshold_milli)
                if (type(c["rank"]) is not int or c["rank"] != rank or c["distance"] < previous
                        or c["relevant"] is not (c["source_id"] in gold)):
                    raise ValueError("holdout_candidate_mismatch")
                previous = c["distance"]
        nearest = candidates[0]["distance"] if candidates else None
        if row["nearest_distance"] != nearest:
            raise ValueError("holdout_nearest_mismatch")
        decision = decide(nearest, status=row["status"], threshold_milli=policy.threshold_milli)
        original_top = [c["source_id"] for c in candidates[:3]]
        retained_top = original_top if decision == "retained" else []
        if gold:
            before_recall += Fraction(len(gold & set(original_top)), len(gold))
            after_recall += Fraction(len(gold & set(retained_top)), len(gold))
        if decision == "retrieval_error": counts["positive_error" if gold else "negative_error"] += 1
        elif decision == "no_candidates": counts["positive_no_candidates" if gold else "negative_no_candidates"] += 1
        elif decision == "abstained": counts["false_refusal" if gold else "correct_abstention"] += 1
        elif not gold: counts["false_acceptance"] += 1
        decisions.append({"query_id": query.id, "answerable": bool(gold), "decision": decision,
                          "nearest_distance": nearest, "before_top_source_ids": original_top,
                          "retained_top_source_ids": retained_top})
    return {"schema_version": 1, "stage": "holdout_evaluated_not_enabled_in_product",
            "policy_sha256": canonical_digest(policy.model_dump()), "report_sha256": canonical_digest(report),
            "holdout_sha256": dataset_digest(dataset), "threshold_milli": policy.threshold_milli,
            "positive_count": positive_count, "negative_count": negative_count, "counts": counts,
            "false_refusal_rate": str(Fraction(counts["false_refusal"], positive_count)),
            "false_acceptance_rate": str(Fraction(counts["false_acceptance"], negative_count)),
            "positive_recall_at_3_before": str(before_recall / positive_count),
            "positive_recall_at_3_after": str(after_recall / positive_count), "decisions": decisions,
            "limits": ["question-level authored holdout on shared small corpus; not blind or real-project generalization",
                       "retrieval errors and no-candidates are separate; not correct abstentions",
                       "no threshold search or policy rewrite", "query-level rule does not filter individual candidates"]}


def load_frozen_holdout(directory, baseline: Dataset):
    import json
    development = Split.model_validate_json((directory / "development.json").read_bytes())
    holdout = Split.model_validate_json((directory / "holdout.json").read_bytes())
    frozen = json.loads((directory / "frozen-policy.json").read_text())
    development_report = json.loads((directory / "development-report.json").read_text())
    design = json.loads((directory / "holdout-design.json").read_text())
    policy = validate_holdout_inputs(development, holdout, baseline, frozen, development_report, design["frozen_sha256"])
    if design["holdout_sha256"] != dataset_digest(holdout.dataset):
        raise ValueError("holdout_design_changed")
    return holdout, policy
